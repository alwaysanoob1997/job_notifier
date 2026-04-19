from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import scrape_job_limit
from app.db import session_scope
from app.models import AppSettings, Job, JobFilter, ScrapeRun

logger = logging.getLogger(__name__)

_busy_lock = threading.Lock()
_is_running = False


def try_begin_scrape() -> bool:
    global _is_running
    with _busy_lock:
        if _is_running:
            return False
        _is_running = True
        return True


def end_scrape() -> None:
    global _is_running
    with _busy_lock:
        _is_running = False


def create_pending_run(session: Session, filter_id: int, trigger: str) -> ScrapeRun:
    filt = session.get(JobFilter, filter_id)
    if filt is None:
        raise RuntimeError(f"JobFilter {filter_id} not found")
    limit = scrape_job_limit()
    run = ScrapeRun(
        filter_id=filter_id,
        started_at=dt.datetime.now(dt.timezone.utc),
        status="running",
        job_title=filt.job_title or "",
        location=filt.location or "",
        trigger=trigger,
        jobs_returned=0,
        jobs_new=0,
        jobs_duplicate=0,
        scrape_target_limit=limit,
    )
    session.add(run)
    session.flush()
    return run


def _event_data_to_row(run_id: int, data) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    jid = str(data.job_id or "").strip()
    return {
        "job_id": jid,
        "first_seen_run_id": run_id,
        "created_at": now,
        "query": data.query or "",
        "location": data.location or "",
        "title": data.title or "",
        "company": data.company or "",
        "place": data.place or "",
        "link": data.link or "",
        "apply_link": data.apply_link or "",
        "description": data.description or "",
        "description_html": data.description_html or "",
        "date": data.date or "",
        "date_text": data.date_text or "",
        "salary": data.salary or "",
    }


def _try_insert_job(session: Session, run_id: int, data) -> str:
    """Returns 'new', 'duplicate', or 'skipped'."""
    jid = str(data.job_id or "").strip()
    if not jid:
        return "skipped"
    row = _event_data_to_row(run_id, data)
    stmt = sqlite_insert(Job).values(**row).on_conflict_do_nothing(index_elements=["job_id"])
    result = session.execute(stmt)
    if result.rowcount == 1:
        return "new"
    return "duplicate"


def run_scrape_sync(run_id: int) -> None:
    """Execute LinkedIn scrape for an existing ScrapeRun row. Runs in a worker thread."""
    from linkedin_jobs_scraper import LinkedinScraper
    from linkedin_jobs_scraper.events import Events, EventData
    from linkedin_jobs_scraper.filters import RelevanceFilters, TimeFilters
    from linkedin_jobs_scraper.query import Query, QueryOptions, QueryFilters

    rows: list[EventData] = []
    errors: list[str] = []
    collect_lock = threading.Lock()
    progress_flush_lock = threading.Lock()
    progress_state = {"committed_n": 0, "flush_t": 0.0}

    try:
        with session_scope() as session:
            run = session.get(ScrapeRun, run_id)
            if run is None:
                raise RuntimeError(f"ScrapeRun {run_id} not found")
            filt = session.get(JobFilter, run.filter_id) if run.filter_id is not None else None
            if filt is None:
                raise RuntimeError("ScrapeRun has no filter_id or filter missing")
            job_title = filt.job_title or ""
            location = (filt.location or "Bengaluru").strip() or "Bengaluru"
            settings = session.get(AppSettings, 1)
            chrome_executable_path = settings.chrome_executable_path if settings else None
            chrome_binary_location = settings.chrome_binary_location if settings else None

        kwargs: dict = {
            "headless": True,
            "max_workers": 1,
            "slow_mo": 1.3,
        }
        if chrome_executable_path:
            kwargs["chrome_executable_path"] = chrome_executable_path
        if chrome_binary_location:
            kwargs["chrome_binary_location"] = chrome_binary_location

        scraper = LinkedinScraper(**kwargs)
        job_limit = scrape_job_limit()

        def _persist_jobs_returned_count(n: int) -> None:
            try:
                with session_scope() as session:
                    run_row = session.get(ScrapeRun, run_id)
                    if run_row is not None:
                        run_row.jobs_returned = n
            except Exception as e:
                logger.warning("persist scrape progress failed: %s", e)

        def on_data(data: EventData) -> None:
            with collect_lock:
                rows.append(data)
                n = len(rows)
            now = time.monotonic()
            with progress_flush_lock:
                if n - progress_state["committed_n"] < 5 and now - progress_state["flush_t"] < 1.0:
                    return
            with collect_lock:
                n2 = len(rows)
            _persist_jobs_returned_count(n2)
            with progress_flush_lock:
                progress_state["committed_n"] = n2
                progress_state["flush_t"] = time.monotonic()

        def on_error(err: str) -> None:
            with collect_lock:
                errors.append(err)
            logger.warning("scraper error: %s", err)

        scraper.on(Events.DATA, on_data)
        scraper.on(Events.ERROR, on_error)

        queries = [
            Query(
                query=job_title,
                options=QueryOptions(
                    locations=[location],
                    limit=job_limit,
                    filters=QueryFilters(
                        time=TimeFilters.DAY,
                        relevance=RelevanceFilters.RECENT,
                    ),
                ),
            )
        ]
        scraper.run(queries)

        with session_scope() as session:
            run = session.get(ScrapeRun, run_id)
            if run is None:
                return
            returned = 0
            new_c = 0
            dup_c = 0
            for data in rows:
                returned += 1
                kind = _try_insert_job(session, run_id, data)
                if kind == "new":
                    new_c += 1
                elif kind == "duplicate":
                    dup_c += 1
            run.jobs_returned = returned
            run.jobs_new = new_c
            run.jobs_duplicate = dup_c
            run.finished_at = dt.datetime.now(dt.timezone.utc)
            run.status = "success"
            if errors:
                run.error_message = "\n".join(errors[:20])
            else:
                run.error_message = None

        def _score_worker(rid: int = run_id) -> None:
            from app.services.job_match_scoring import score_jobs_for_run

            score_jobs_for_run(rid)

        threading.Thread(
            target=_score_worker,
            name=f"llm-score-{run_id}",
            daemon=True,
        ).start()
    except Exception as e:
        logger.exception("scrape failed")
        with session_scope() as session:
            run = session.get(ScrapeRun, run_id)
            if run is not None:
                run.status = "failed"
                run.finished_at = dt.datetime.now(dt.timezone.utc)
                run.error_message = str(e)
    finally:
        end_scrape()


def start_scrape_if_idle_for_filter(filter_id: int, trigger: str) -> int | None:
    """Returns run_id if started, None if another scrape is already running."""
    if not try_begin_scrape():
        return None
    try:
        with session_scope() as session:
            run = create_pending_run(session, filter_id, trigger=trigger)
            run_id = run.id
    except Exception:
        end_scrape()
        raise

    def worker() -> None:
        run_scrape_sync(run_id)

    threading.Thread(target=worker, name=f"scrape-{run_id}", daemon=True).start()
    return run_id


def run_scheduled_once_sync() -> int | None:
    """
    Blocking scrape for cron/launchd. Uses same DB as the web app.
    Runs the lowest-id JobFilter. Returns run_id, or None if busy / no filters.
    """
    if not try_begin_scrape():
        logger.info("Skipped scheduled run: scrape already in progress")
        return None
    try:
        with session_scope() as session:
            filter_id = session.scalar(select(JobFilter.id).order_by(JobFilter.id).limit(1))
            if filter_id is None:
                logger.warning("No JobFilter rows; cannot run scheduled scrape")
                end_scrape()
                return None
            run = create_pending_run(session, filter_id, trigger="scheduled")
            run_id = run.id
    except Exception:
        end_scrape()
        raise
    run_scrape_sync(run_id)
    return run_id
