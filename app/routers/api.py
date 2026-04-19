from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.models import ScrapeRun

router = APIRouter()


@router.get("/runs/{run_id}")
def get_run_status(run_id: int, db: Session = Depends(get_db)):
    run = db.get(ScrapeRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "jobs_returned": run.jobs_returned,
        "jobs_new": run.jobs_new,
        "jobs_duplicate": run.jobs_duplicate,
        "error_message": run.error_message,
        "trigger": run.trigger,
    }
