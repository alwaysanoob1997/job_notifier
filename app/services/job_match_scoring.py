from __future__ import annotations

import datetime as dt
import json
import logging
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import (
    lms_auto_shutdown_enabled,
    lms_auto_start_server_enabled,
    lms_cli_executable,
    lms_server_bind_address,
    lms_server_start_port,
    lms_server_start_wait_seconds,
    lmstudio_base_url,
    lmstudio_model,
)
from app.db import session_scope
from app.default_system_prompt import DEFAULT_SYSTEM_PROMPT
from app.llm_score_db import JobLlmScore, session_scope_llm
from app.models import Job, ScrapeRun
from app.services.ideal_job_requirements import get_active_requirement
from app.services.smtp_notify import send_plaintext_email
from app.services.system_prompt_versions import effective_system_prompt_for_scoring

logger = logging.getLogger(__name__)

_run_score_locks: dict[int, threading.Lock] = {}
_run_score_locks_mutex = threading.Lock()


def _lock_for_run(run_id: int) -> threading.Lock:
    with _run_score_locks_mutex:
        if run_id not in _run_score_locks:
            _run_score_locks[run_id] = threading.Lock()
        return _run_score_locks[run_id]


_MAX_DESCRIPTION_CHARS = 12_000
_MAX_HTML_SNIPPET = 400

_USER_TEMPLATE = """## User ideal job requirements

{ideal}

## Job listing (structured)

{job_blob}

Return JSON with "score" (0–100) and "reasoning" (string).
Reasoning: only brief separate lines, each one short."""


@dataclass
class JobScoringSnapshot:
    """ORM-free job fields for LLM scoring (built inside a DB session, used after it closes)."""

    job_id: str
    first_seen_run_id: int
    created_at: dt.datetime | None
    query: str
    location: str
    title: str
    company: str
    place: str
    link: str
    apply_link: str
    description: str
    description_html: str
    date: str
    date_text: str
    salary: str


def _job_to_snapshot(j: Job) -> JobScoringSnapshot:
    return JobScoringSnapshot(
        job_id=j.job_id,
        first_seen_run_id=j.first_seen_run_id,
        created_at=j.created_at,
        query=j.query or "",
        location=j.location or "",
        title=j.title or "",
        company=j.company or "",
        place=j.place or "",
        link=j.link or "",
        apply_link=j.apply_link or "",
        description=j.description or "",
        description_html=j.description_html or "",
        date=j.date or "",
        date_text=j.date_text or "",
        salary=j.salary or "",
    )


def _match_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "job_match_score",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "reasoning": {"type": "string", "maxLength": 450},
                },
                "required": ["score", "reasoning"],
                "additionalProperties": False,
            },
        },
    }


def format_job_blob(job: Job | JobScoringSnapshot) -> str:
    desc = (job.description or "").strip()
    if len(desc) > _MAX_DESCRIPTION_CHARS:
        desc = desc[:_MAX_DESCRIPTION_CHARS] + "\n… [truncated]"

    html = (job.description_html or "").strip()
    if html:
        if len(html) > _MAX_HTML_SNIPPET:
            html = html[:_MAX_HTML_SNIPPET] + "… [truncated]"
        html_block = f"\n### description_html (snippet)\n{html}"
    else:
        html_block = ""

    lines = [
        f"job_id: {job.job_id}",
        f"title: {job.title or ''}",
        f"company: {job.company or ''}",
        f"place: {job.place or ''}",
        f"query: {job.query or ''}",
        f"location: {job.location or ''}",
        f"link: {job.link or ''}",
        f"apply_link: {job.apply_link or ''}",
        f"salary: {job.salary or ''}",
        f"date: {job.date or ''}",
        f"date_text: {job.date_text or ''}",
        f"created_at: {job.created_at.isoformat() if job.created_at else ''}",
        f"first_seen_run_id: {job.first_seen_run_id}",
        "### description (plain text)",
        desc or "(empty)",
    ]
    if html_block:
        lines.append(html_block)
    return "\n".join(lines)


def _parse_score_response(raw: str) -> tuple[int, str]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    score = data.get("score")
    reasoning = data.get("reasoning")
    if not isinstance(score, int) or not (0 <= score <= 100):
        raise ValueError("invalid score")
    if not isinstance(reasoning, str):
        raise ValueError("invalid reasoning")
    return score, reasoning.strip()


def _chat_completions(
    client: httpx.Client,
    *,
    model: str,
    messages: list[dict[str, str]],
) -> str:
    url = f"{lmstudio_base_url().rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "response_format": _match_response_format(),
    }
    r = client.post(
        url,
        json=payload,
        headers={"Authorization": "Bearer lm-studio"},
    )
    r.raise_for_status()
    body = r.json()
    choices = body.get("choices") or []
    if not choices:
        raise ValueError("no choices in response")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty message content")
    return content.strip()


def _upsert_score(session: Session, job_id: str, score: int, reasoning: str) -> None:
    stmt = sqlite_insert(JobLlmScore).values(job_id=job_id, score=score, reasoning=reasoning)
    stmt = stmt.on_conflict_do_update(
        index_elements=[JobLlmScore.job_id],
        set_={
            "score": stmt.excluded.score,
            "reasoning": stmt.excluded.reasoning,
        },
    )
    session.execute(stmt)


def _run_lms(args: list[str]) -> subprocess.CompletedProcess[str]:
    exe = lms_cli_executable()
    cmd = [exe, *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        shell=False,
    )


def start_lmstudio_server_for_scoring() -> None:
    """``lms server start --bind …`` so WSL/LAN can reach the HTTP API (optional via env)."""
    if not lms_auto_start_server_enabled():
        return
    exe = lms_cli_executable()
    bind = lms_server_bind_address()
    cmd = [exe, "server", "start", "--bind", bind]
    port = lms_server_start_port()
    if port is not None:
        cmd.extend(["--port", str(port)])
    logger.info("LM Studio: starting server (%s)", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, shell=False)
    if r.returncode != 0:
        logger.warning(
            "lms server start failed (code=%s): %s — continuing in case server was already running",
            r.returncode,
            (r.stderr or r.stdout or "").strip()[:500],
        )
    wait = lms_server_start_wait_seconds()
    if wait > 0:
        time.sleep(wait)


def _clamp_notify_threshold(n: int) -> int:
    return max(0, min(100, n))


def _format_job_match_digest_body(
    run_id: int,
    run_job_title: str,
    run_location: str,
    threshold: int,
    entries: list[dict[str, str]],
) -> str:
    lines = [
        f"Scrape run #{run_id}",
        f"Search: {run_job_title or '—'} · {run_location or '—'}",
        f"Threshold: scores ≥ {threshold}",
        "",
        f"{len(entries)} listing(s) matched:",
        "",
    ]
    for i, e in enumerate(entries, 1):
        lines.extend(
            [
                f"--- Job {i} ---",
                f"Title: {e['title']}",
                f"Company: {e['company']}",
                f"Place: {e['place']}",
                f"Query location: {e['location']}",
                f"Score: {e['score']}/100",
                f"Job ID: {e['job_id']}",
                f"Listing link: {e['link'] or '(none)'}",
                f"Apply link: {e['apply_link'] or '(none)'}",
                "",
                "LLM reasoning:",
                e["reasoning"],
                "",
            ]
        )
    return "\n".join(lines)


def shutdown_lmstudio_after_inference() -> None:
    """Unload model and stop LM Studio server (best-effort; logs warnings)."""
    if not lms_auto_shutdown_enabled():
        return
    model = lmstudio_model()
    r1 = _run_lms(["unload", model])
    if r1.returncode != 0:
        logger.warning(
            "lms unload %s failed (code=%s): %s",
            model,
            r1.returncode,
            (r1.stderr or r1.stdout or "").strip()[:500],
        )
        r1b = _run_lms(["unload", "--all"])
        if r1b.returncode != 0:
            logger.warning(
                "lms unload --all failed (code=%s): %s",
                r1b.returncode,
                (r1b.stderr or r1b.stdout or "").strip()[:500],
            )
    r2 = _run_lms(["server", "stop"])
    if r2.returncode != 0:
        logger.warning(
            "lms server stop failed (code=%s): %s",
            r2.returncode,
            (r2.stderr or r2.stdout or "").strip()[:500],
        )


def _persist_llm_compare_done(run_id: int, done: int) -> None:
    with session_scope() as session:
        run_row = session.get(ScrapeRun, run_id)
        if run_row is not None:
            run_row.llm_compare_done = done


def _score_jobs_for_run_impl(run_id: int) -> None:
    """Score all jobs first seen in this run; optional LM Studio teardown after HTTP inference.

    Teardown (unload + server stop) runs only after at least one **successful** chat completion,
    so connection errors do not stop LM Studio and make debugging harder.
    """
    model = lmstudio_model()
    had_successful_llm_response = False
    digest_payload: list[dict[str, str]] = []
    notify_to: str | None = None
    notify_thr = 60
    run_title = ""
    run_loc = ""
    ideal_text = ""
    llm_system_prompt = DEFAULT_SYSTEM_PROMPT

    with session_scope() as session:
        llm_system_prompt = effective_system_prompt_for_scoring(session)
        active = get_active_requirement(session)
        ideal_text = (active.description or "").strip() if active else ""
        if not ideal_text:
            logger.info("skip LLM scoring run_id=%s: no active ideal job requirements", run_id)
            run_row = session.get(ScrapeRun, run_id)
            if run_row is not None:
                run_row.llm_compare_total = -1
                run_row.llm_compare_done = 0
            return
        assert active is not None
        notify_to = (active.notify_email or "").strip() or None
        try:
            notify_thr = _clamp_notify_threshold(int(active.notify_threshold))
        except (TypeError, ValueError):
            notify_thr = 60
        run_row = session.get(ScrapeRun, run_id)
        if run_row is not None:
            run_title = run_row.job_title or ""
            run_loc = run_row.location or ""

        jobs = list(
            session.scalars(
                select(Job).where(Job.first_seen_run_id == run_id).order_by(Job.job_id)
            )
        )
        snapshots = [_job_to_snapshot(j) for j in jobs]

    if not snapshots:
        with session_scope() as session:
            run_row = session.get(ScrapeRun, run_id)
            if run_row is not None:
                run_row.llm_compare_total = 0
                run_row.llm_compare_done = 0
        logger.info("skip LLM scoring run_id=%s: no new jobs for this run", run_id)
        return

    start_lmstudio_server_for_scoring()

    with session_scope() as session:
        run_row = session.get(ScrapeRun, run_id)
        if run_row is not None:
            run_row.llm_compare_total = len(snapshots)
            run_row.llm_compare_done = 0

    done_ct = 0
    with httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
        for job in snapshots:
            try:
                blob = format_job_blob(job)
                user_msg = _USER_TEMPLATE.format(ideal=ideal_text, job_blob=blob)
                messages = [
                    {"role": "system", "content": llm_system_prompt},
                    {"role": "user", "content": user_msg},
                ]
                try:
                    raw = _chat_completions(client, model=model, messages=messages)
                    score, reasoning = _parse_score_response(raw)
                    had_successful_llm_response = True
                except Exception as e:
                    logger.warning("LLM scoring failed job_id=%s: %s", job.job_id, e, exc_info=True)
                    continue
                try:
                    with session_scope_llm() as llm_session:
                        _upsert_score(llm_session, job.job_id, score, reasoning)
                except Exception as e:
                    logger.warning("persist LLM score failed job_id=%s: %s", job.job_id, e, exc_info=True)
                    continue
                if notify_to and score >= notify_thr:
                    digest_payload.append(
                        {
                            "job_id": job.job_id,
                            "title": job.title or "",
                            "company": job.company or "",
                            "place": job.place or "",
                            "location": job.location or "",
                            "link": job.link or "",
                            "apply_link": job.apply_link or "",
                            "score": str(score),
                            "reasoning": reasoning,
                        }
                    )
            finally:
                done_ct += 1
                _persist_llm_compare_done(run_id, done_ct)

    if notify_to and digest_payload:
        subject = f"LinkedIn Jobs: {len(digest_payload)} match(es) ≥ {notify_thr} (run #{run_id})"
        body = _format_job_match_digest_body(run_id, run_title, run_loc, notify_thr, digest_payload)
        send_plaintext_email(notify_to, subject, body)

    if had_successful_llm_response and lms_auto_shutdown_enabled():
        try:
            shutdown_lmstudio_after_inference()
        except Exception as e:
            logger.warning("LM Studio shutdown step failed: %s", e, exc_info=True)


def score_jobs_for_run(run_id: int) -> None:
    """Run LLM scoring synchronously (used by tests)."""
    _score_jobs_for_run_impl(run_id)


def start_score_jobs_for_run_background(run_id: int) -> bool:
    """Start `_score_jobs_for_run_impl` in a daemon thread if this run is not already scoring.

    Returns True if a worker was started and took the lock; False if another scoring pass
    is already in progress for this run_id.
    """
    lock = _lock_for_run(run_id)
    handshake: queue.SimpleQueue[bool] = queue.SimpleQueue()

    def worker() -> None:
        if not lock.acquire(blocking=False):
            logger.info("skip LLM scoring run_id=%s: already in progress", run_id)
            handshake.put(False)
            return
        handshake.put(True)
        try:
            _score_jobs_for_run_impl(run_id)
        finally:
            lock.release()

    threading.Thread(
        target=worker,
        name=f"llm-score-{run_id}",
        daemon=True,
    ).start()
    return handshake.get()
