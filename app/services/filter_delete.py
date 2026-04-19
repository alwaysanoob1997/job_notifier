from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.models import JobFilter, ScheduleAudit, ScrapeRun
from app.services.scheduler import effective_daily_slots


def get_filter_delete_context(session: Session, filter_id: int) -> dict | None:
    filt = session.get(JobFilter, filter_id)
    if filt is None:
        return None
    audits = (
        session.scalar(
            select(func.count()).select_from(ScheduleAudit).where(ScheduleAudit.filter_id == filter_id)
        )
        or 0
    )
    runs = (
        session.scalar(
            select(func.count()).select_from(ScrapeRun).where(ScrapeRun.filter_id == filter_id)
        )
        or 0
    )
    running = (
        session.scalar(
            select(func.count()).select_from(ScrapeRun).where(
                ScrapeRun.filter_id == filter_id,
                ScrapeRun.status == "running",
            )
        )
        or 0
    )
    return {
        "filter": filt,
        "schedule_audit_count": int(audits),
        "scrape_run_count": int(runs),
        "running_scrape_count": int(running),
        "daily_slot_count": len(effective_daily_slots(filt)),
        "has_active_job_title": bool((filt.job_title or "").strip()),
    }


def try_delete_filter(session: Session, filter_id: int) -> tuple[bool, str]:
    """
    Remove ScheduleAudit rows for this filter, detach ScrapeRuns, delete JobFilter.
    Returns (True, "") on success, (False, "not_found" | "running") on failure.
    """
    ctx = get_filter_delete_context(session, filter_id)
    if ctx is None:
        return False, "not_found"
    if ctx["running_scrape_count"] > 0:
        return False, "running"
    session.execute(delete(ScheduleAudit).where(ScheduleAudit.filter_id == filter_id))
    session.execute(update(ScrapeRun).where(ScrapeRun.filter_id == filter_id).values(filter_id=None))
    filt = session.get(JobFilter, filter_id)
    if filt is not None:
        session.delete(filt)
    return True, ""
