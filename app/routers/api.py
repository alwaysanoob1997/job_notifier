from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import lmstudio_env_overrides_model
from app.dependencies import get_db
from app.lmstudio_cli import lms_cli_available, list_downloaded_models
from app.lmstudio_prefs import get_preferred_model_id, set_preferred_model_id
from app.models import ScrapeRun

router = APIRouter()


class LmstudioPreferencesBody(BaseModel):
    preferred_model_id: str = Field(..., min_length=1, max_length=2048)


@router.get("/lmstudio/status")
def lmstudio_status():
    """LM Studio CLI presence, downloaded models, and persisted preference (file, not env)."""
    cli = lms_cli_available()
    models: list[str] = []
    list_error: str | None = None
    if cli:
        models, list_error = list_downloaded_models()
    return {
        "cli_available": cli,
        "models": models,
        "preferred_model_id": get_preferred_model_id(),
        "env_overrides_model": lmstudio_env_overrides_model(),
        "list_error": list_error,
    }


@router.post("/lmstudio/preferences")
def lmstudio_preferences(body: LmstudioPreferencesBody):
    """Persist preferred model id (validated against `lms ls` when the CLI works and models exist)."""
    if not lms_cli_available():
        raise HTTPException(
            status_code=503,
            detail="LM Studio CLI not found. Install LM Studio from https://lmstudio.ai/ "
            "or set LINKEDIN_LMS_CLI to your lms executable.",
        )
    models, list_error = list_downloaded_models()
    if list_error:
        raise HTTPException(
            status_code=503,
            detail=f"Could not list models: {list_error}",
        )
    if not models:
        raise HTTPException(
            status_code=400,
            detail="No models downloaded yet. Open LM Studio and download a model, then try again.",
        )
    choice = body.preferred_model_id.strip()
    if choice not in models:
        raise HTTPException(
            status_code=400,
            detail="Selected model is not in the downloaded models list.",
        )
    set_preferred_model_id(choice)
    return {"ok": True, "preferred_model_id": choice}


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
        "scrape_target_limit": run.scrape_target_limit,
        "llm_compare_total": run.llm_compare_total,
        "llm_compare_done": run.llm_compare_done,
        "error_message": run.error_message,
        "trigger": run.trigger,
    }
