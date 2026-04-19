from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.llm_score_db import JobLlmScore, session_scope_llm
from app.models import Job, ScrapeRun

logger = logging.getLogger(__name__)


def delete_scrape_run(session: Session, run_id: int) -> tuple[bool, str, list[str]]:
    """
    Remove a scrape run and jobs first recorded in that run (first_seen_run_id).

    Returns (ok, err, job_ids) where job_ids should be passed to cleanup_llm_scores_for_jobs
    after the main session commits successfully.
    """
    run = session.get(ScrapeRun, run_id)
    if run is None:
        return False, "not_found", []
    if run.status == "running":
        return False, "running", []
    job_ids = list(session.scalars(select(Job.job_id).where(Job.first_seen_run_id == run_id)))
    session.execute(delete(Job).where(Job.first_seen_run_id == run_id))
    session.delete(run)
    return True, "", job_ids


def cleanup_llm_scores_for_jobs(job_ids: list[str]) -> None:
    if not job_ids:
        return
    try:
        with session_scope_llm() as llm_session:
            llm_session.execute(delete(JobLlmScore).where(JobLlmScore.job_id.in_(job_ids)))
    except Exception:
        logger.warning("Could not remove LLM score rows for deleted jobs", exc_info=True)
