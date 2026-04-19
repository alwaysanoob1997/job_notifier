from __future__ import annotations

import json
import logging
import subprocess
import time
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
from app.llm_score_db import JobLlmScore, session_scope_llm
from app.models import Job, ScrapeRun
from app.services.ideal_job_requirements import get_active_requirement
from app.services.smtp_notify import send_plaintext_email

logger = logging.getLogger(__name__)

_MAX_DESCRIPTION_CHARS = 12_000
_MAX_HTML_SNIPPET = 400

_SYSTEM_PROMPT = """You are an impartial assistant that compares job postings to a user's stated ideal job requirements.
You must respond with a single JSON object only, matching the requested schema exactly.
The score must be an integer from 0 to 100 inclusive: 100 means an excellent match, 0 means no meaningful match.
Reasoning should be very concise (2-3 sentences) and cite specific overlaps or gaps versus the requirements."""

_USER_TEMPLATE = """## User ideal job requirements

{ideal}

## Job listing (structured)

{job_blob}

Respond with JSON containing "score" (0-100 integer) and "reasoning" (string) only."""


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
                    "reasoning": {"type": "string"},
                },
                "required": ["score", "reasoning"],
                "additionalProperties": False,
            },
        },
    }


def format_job_blob(job: Job) -> str:
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


def score_jobs_for_run(run_id: int) -> None:
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

    with session_scope() as session:
        active = get_active_requirement(session)
        ideal_text = (active.description or "").strip() if active else ""
        if not ideal_text:
            logger.info("skip LLM scoring run_id=%s: no active ideal job requirements", run_id)
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
        if not jobs:
            logger.info("skip LLM scoring run_id=%s: no new jobs for this run", run_id)
            return

        start_lmstudio_server_for_scoring()

        with httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            for job in jobs:
                blob = format_job_blob(job)
                user_msg = _USER_TEMPLATE.format(ideal=ideal_text, job_blob=blob)
                messages = [
                    {"role": "system", "content": _SYSTEM_PROMPT},
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

    if notify_to and digest_payload:
        subject = f"LinkedIn Jobs: {len(digest_payload)} match(es) ≥ {notify_thr} (run #{run_id})"
        body = _format_job_match_digest_body(run_id, run_title, run_loc, notify_thr, digest_payload)
        send_plaintext_email(notify_to, subject, body)

    if had_successful_llm_response and lms_auto_shutdown_enabled():
        try:
            shutdown_lmstudio_after_inference()
        except Exception as e:
            logger.warning("LM Studio shutdown step failed: %s", e, exc_info=True)
