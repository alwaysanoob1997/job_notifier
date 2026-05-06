from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.llm import PROVIDER_IDS, all_providers, get_active_provider, get_provider
from app.llm.openrouter import OpenRouterProvider, invalidate_models_cache
from app.llm_prefs import (
    PROVIDER_CUSTOM,
    PROVIDER_LMSTUDIO,
    PROVIDER_OPENROUTER,
    set_active_provider_id,
    update_provider_block,
)
from app.models import ScrapeRun

router = APIRouter()


class _LmStudioPrefs(BaseModel):
    model: str = Field("", max_length=2048)


class _OpenRouterPrefs(BaseModel):
    model: str = Field("", max_length=2048)


class _CustomPrefs(BaseModel):
    base_url: str = Field("", max_length=2048)
    model: str = Field("", max_length=2048)


class LlmPreferencesBody(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    lmstudio: _LmStudioPrefs | None = None
    openrouter: _OpenRouterPrefs | None = None
    custom: _CustomPrefs | None = None


@router.get("/llm/status")
def llm_status():
    """All providers' configuration state plus which one is currently selected."""
    active = get_active_provider()
    providers: dict[str, dict] = {}
    for p in all_providers():
        providers[p.id] = p.status_summary()
    return {
        "active_provider": active.id,
        "configured": active.is_configured(),
        "providers": providers,
    }


@router.post("/llm/preferences")
def llm_preferences(body: LlmPreferencesBody):
    """Persist provider choice and per-provider non-secret settings."""
    pid = body.provider.strip().lower()
    if pid not in PROVIDER_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider {pid!r}; expected one of {list(PROVIDER_IDS)}.",
        )

    if body.lmstudio is not None and (model := body.lmstudio.model.strip()):
        if pid == PROVIDER_LMSTUDIO:
            available, list_error = get_provider(PROVIDER_LMSTUDIO).available_models()
            if list_error and not available:
                raise HTTPException(status_code=503, detail=f"Could not list models: {list_error}")
            if available and model not in available:
                raise HTTPException(
                    status_code=400,
                    detail="Selected model is not in the downloaded models list.",
                )
        update_provider_block(PROVIDER_LMSTUDIO, {"preferred_model_id": model})

    if body.openrouter is not None and (model := body.openrouter.model.strip()):
        update_provider_block(PROVIDER_OPENROUTER, {"model": model})

    if body.custom is not None:
        updates: dict[str, str] = {}
        if body.custom.base_url.strip():
            updates["base_url"] = body.custom.base_url.strip()
        if body.custom.model.strip():
            updates["model"] = body.custom.model.strip()
        if updates:
            update_provider_block(PROVIDER_CUSTOM, updates)

    set_active_provider_id(pid)

    active = get_active_provider()
    return {
        "ok": True,
        "active_provider": active.id,
        "configured": active.is_configured(),
    }


@router.get("/llm/openrouter/models")
def llm_openrouter_models(refresh: int = 0):
    """List OpenRouter models for the dropdown (cached for ~10 minutes)."""
    if refresh:
        invalidate_models_cache()
    provider = OpenRouterProvider()
    models, error = provider.available_models()
    return {"models": models, "list_error": error}


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
