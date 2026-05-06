from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.llm import PROVIDER_IDS, all_providers, get_active_provider, get_provider
from app.llm.gemini import invalidate_models_cache as invalidate_gemini_models_cache
from app.llm_prefs import (
    PROVIDER_CUSTOM,
    PROVIDER_GEMINI,
    PROVIDER_LMSTUDIO,
    set_active_provider_id,
    update_provider_block,
)
from app.models import ScrapeRun

router = APIRouter()


class _LmStudioPrefs(BaseModel):
    model: str = Field("", max_length=2048)


class _GeminiPrefs(BaseModel):
    model: str = Field("", max_length=2048)


class _CustomPrefs(BaseModel):
    base_url: str = Field("", max_length=2048)
    model: str = Field("", max_length=2048)


class LlmPreferencesBody(BaseModel):
    provider: str = Field(..., min_length=1, max_length=64)
    lmstudio: _LmStudioPrefs | None = None
    gemini: _GeminiPrefs | None = None
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

    if body.gemini is not None and (model := body.gemini.model.strip()):
        update_provider_block(PROVIDER_GEMINI, {"model": model})

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


def _build_models_payload(provider) -> dict:
    """Shared response shape for ``/llm/{provider}/models`` endpoints.

    Includes the structured per-model metadata, the unique vendor list (so the
    dropdown can render a vendor filter without a second request), and the set
    of filter capabilities the provider declares — the UI uses this to decide
    which filter controls to render.
    """
    if hasattr(provider, "available_models_detailed"):
        infos, error = provider.available_models_detailed()
    else:
        ids, error = provider.available_models()
        from app.llm.base import ModelInfo, vendor_from_model_id

        infos = [ModelInfo(id=mid, vendor=vendor_from_model_id(mid)) for mid in ids]

    vendors_seen: list[str] = []
    seen: set[str] = set()
    for m in infos:
        v = m.vendor
        if v and v not in seen:
            seen.add(v)
            vendors_seen.append(v)

    return {
        "models": [m.to_dict() for m in infos],
        "vendors": vendors_seen,
        "supported_filters": sorted(provider.supported_filters),
        "list_error": error,
    }


@router.get("/llm/{provider_id}/models")
def llm_provider_models(provider_id: str, refresh: int = 0):
    """Generic provider model list.

    Providers without an enumerable catalog (e.g. ``custom``) return an empty
    ``models`` list with their ``supported_filters`` (typically empty). Providers
    that cache their model list (e.g. Gemini) honour ``?refresh=1`` to force a
    fresh fetch.
    """
    pid = (provider_id or "").strip().lower()
    if pid not in PROVIDER_IDS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown provider {pid!r}; expected one of {list(PROVIDER_IDS)}.",
        )
    if pid == PROVIDER_GEMINI and refresh:
        invalidate_gemini_models_cache()
    return _build_models_payload(get_provider(pid))


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
