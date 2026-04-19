from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import JobFilter, ScheduleAudit


def clear_filter_schedule_and_audits(session: Session, filter_id: int) -> bool:
    """
    Clear live schedule fields on JobFilter and remove all schedule audit rows for the filter.
    Caller must commit and call refresh_schedule().
    Returns False if the filter does not exist.
    """
    filt = session.get(JobFilter, filter_id)
    if filt is None:
        return False
    filt.schedule_times_json = None
    filt.runs_per_day = 0
    session.execute(delete(ScheduleAudit).where(ScheduleAudit.filter_id == filter_id))
    return True
