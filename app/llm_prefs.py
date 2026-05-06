"""Persist LLM provider choice and per-provider non-secret settings.

Stored at ``~/LinkedInJobs/llm_prefs.json``. Secrets (API keys) live in ``.env`` via
``app.env_user_settings`` so they follow the same handling as ``APP_SMTP_PASSWORD``.

Schema::

    {
      "provider": "lmstudio" | "gemini" | "custom",
      "lmstudio": {"preferred_model_id": "..."},
      "gemini": {"model": "..."},
      "custom": {"base_url": "http://...", "model": "..."}
    }

Older installs may have ``~/LinkedInJobs/lmstudio_prefs.json``; the first read migrates the
``preferred_model_id`` it contains into the new ``lmstudio`` block.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

_PREFS_FILENAME = "llm_prefs.json"
_LEGACY_FILENAME = "lmstudio_prefs.json"

PROVIDER_LMSTUDIO = "lmstudio"
PROVIDER_GEMINI = "gemini"
PROVIDER_CUSTOM = "custom"

VALID_PROVIDERS: tuple[str, ...] = (PROVIDER_LMSTUDIO, PROVIDER_GEMINI, PROVIDER_CUSTOM)


def prefs_file_path() -> Path:
    from app.config import ensure_data_dir, linkedin_jobs_dir

    ensure_data_dir()
    return linkedin_jobs_dir() / _PREFS_FILENAME


def _legacy_prefs_file_path() -> Path:
    from app.config import linkedin_jobs_dir

    return linkedin_jobs_dir() / _LEGACY_FILENAME


def _empty_prefs() -> dict[str, Any]:
    return {
        "provider": PROVIDER_LMSTUDIO,
        PROVIDER_LMSTUDIO: {"preferred_model_id": ""},
        PROVIDER_GEMINI: {"model": ""},
        PROVIDER_CUSTOM: {"base_url": "", "model": ""},
    }


def _coerce_loaded(data: object) -> dict[str, Any]:
    """Merge ``data`` over the empty template so missing keys don't break readers."""
    base = _empty_prefs()
    if not isinstance(data, dict):
        return base
    prov = str(data.get("provider", "")).strip()
    if prov in VALID_PROVIDERS:
        base["provider"] = prov
    legacy = data.get("preferred_model_id")
    if isinstance(legacy, str) and legacy.strip():
        base[PROVIDER_LMSTUDIO]["preferred_model_id"] = legacy.strip()
    for key in VALID_PROVIDERS:
        block = data.get(key)
        if isinstance(block, dict):
            for sub_key, sub_val in block.items():
                if isinstance(sub_val, str):
                    base[key][sub_key] = sub_val.strip()
    return base


def _read_json(path: Path) -> object | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _logger.warning("Could not read %s: %s", path, e)
        return None


def load_prefs() -> dict[str, Any]:
    """Read the prefs file, migrating the legacy ``lmstudio_prefs.json`` on first call."""
    path = prefs_file_path()
    data = _read_json(path)
    if data is None:
        legacy = _read_json(_legacy_prefs_file_path())
        if legacy is None:
            return _empty_prefs()
        return _coerce_loaded(legacy)
    return _coerce_loaded(data)


def save_prefs(prefs: dict[str, Any]) -> None:
    path = prefs_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = _coerce_loaded(prefs)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(merged, indent=2, sort_keys=True) + "\n"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def get_active_provider_id() -> str:
    """Saved provider id, defaulting to ``lmstudio`` for back-compat."""
    return load_prefs().get("provider", PROVIDER_LMSTUDIO)


def set_active_provider_id(provider_id: str) -> None:
    if provider_id not in VALID_PROVIDERS:
        raise ValueError(f"unknown provider id: {provider_id!r}")
    prefs = load_prefs()
    prefs["provider"] = provider_id
    save_prefs(prefs)


def get_provider_block(provider_id: str) -> dict[str, str]:
    if provider_id not in VALID_PROVIDERS:
        raise ValueError(f"unknown provider id: {provider_id!r}")
    block = load_prefs().get(provider_id, {})
    return {k: str(v) for k, v in block.items() if isinstance(v, str)}


def update_provider_block(provider_id: str, updates: dict[str, str]) -> None:
    if provider_id not in VALID_PROVIDERS:
        raise ValueError(f"unknown provider id: {provider_id!r}")
    prefs = load_prefs()
    block = prefs.get(provider_id) or {}
    for k, v in updates.items():
        block[k] = (v or "").strip()
    prefs[provider_id] = block
    save_prefs(prefs)


def get_preferred_model_id() -> str | None:
    """LM Studio preferred model id, or ``None`` when unset (back-compat shim)."""
    val = get_provider_block(PROVIDER_LMSTUDIO).get("preferred_model_id", "").strip()
    return val or None


def set_preferred_model_id(model_id: str) -> None:
    """LM Studio preferred model id (back-compat shim)."""
    update_provider_block(PROVIDER_LMSTUDIO, {"preferred_model_id": model_id})
