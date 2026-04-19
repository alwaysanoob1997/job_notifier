"""Persist LM Studio preferred model id in ~/LinkedInJobs/lmstudio_prefs.json."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

_PREFS_FILENAME = "lmstudio_prefs.json"


def prefs_file_path() -> Path:
    from app.config import ensure_data_dir, linkedin_jobs_dir

    ensure_data_dir()
    return linkedin_jobs_dir() / _PREFS_FILENAME


def load_prefs() -> dict[str, Any]:
    path = prefs_file_path()
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        _logger.warning("Could not read %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_prefs(prefs: dict[str, Any]) -> None:
    path = prefs_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(prefs, indent=2, sort_keys=True) + "\n"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def get_preferred_model_id() -> str | None:
    """Stored preferred model id (may be empty string in file — treated as unset)."""
    v = load_prefs().get("preferred_model_id")
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def set_preferred_model_id(model_id: str) -> None:
    prefs = load_prefs()
    prefs["preferred_model_id"] = model_id.strip()
    save_prefs(prefs)
