"""Detect LM Studio CLI (`lms`) and list downloaded models via `lms ls --json`."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.config import lms_cli_executable

logger = logging.getLogger(__name__)

_LMS_LS_TIMEOUT_SEC = 20.0


def _darwin_path_for_which() -> str | None:
    """PATH augmented with common CLI locations.

    Finder-launched apps on macOS inherit a minimal PATH from launchd (not the user's shell
    profile), so tools installed under Homebrew or similar are often invisible to ``which``.
    """
    if sys.platform != "darwin":
        return None
    home = Path.home()
    extra = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(home / "bin"),
        str(home / ".local" / "bin"),
    ]
    existing = os.environ.get("PATH", "")
    merged = ":".join([*extra, existing] if existing else extra)
    return merged


def resolved_lms_path() -> str | None:
    """Absolute path to the `lms` executable if found."""
    exe = lms_cli_executable().strip()
    if not exe:
        return None
    p = Path(exe)
    if p.is_file():
        return str(p.resolve())
    path = _darwin_path_for_which()
    w = shutil.which(exe, path=path) if path else shutil.which(exe)
    return w


def lms_executable_for_subprocess() -> str:
    """Executable string for ``subprocess`` (prefer absolute path so macOS GUI apps find ``lms``)."""
    return resolved_lms_path() or lms_cli_executable()


def lms_cli_available() -> bool:
    return resolved_lms_path() is not None


def _run_lms_ls_json() -> tuple[str | None, str | None]:
    """Return (stdout, error_message). stdout is None on failure."""
    exe = resolved_lms_path()
    if not exe:
        return None, "LM Studio CLI not found in PATH (install LM Studio or set LINKEDIN_LMS_CLI)."
    try:
        r = subprocess.run(
            [exe, "ls", "--json"],
            capture_output=True,
            text=True,
            timeout=_LMS_LS_TIMEOUT_SEC,
            shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("lms ls --json failed: %s", e)
        return None, str(e)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        msg = err or out or f"exit code {r.returncode}"
        logger.warning("lms ls --json non-zero: %s", msg[:500])
        return None, msg
    return out, None


def parse_lms_ls_json_payload(raw: str) -> list[str]:
    """Extract model identifiers from `lms ls --json` output."""
    data = json.loads(raw)
    ids: list[str] = []

    def take_obj(obj: object) -> None:
        if isinstance(obj, str) and obj.strip():
            ids.append(obj.strip())
        elif isinstance(obj, dict):
            for key in ("id", "modelKey", "path", "name"):
                v = obj.get(key)
                if isinstance(v, str) and v.strip():
                    ids.append(v.strip())
                    return

    if isinstance(data, list):
        for item in data:
            take_obj(item)
    elif isinstance(data, dict):
        for key in ("models", "data", "items"):
            arr = data.get(key)
            if isinstance(arr, list):
                for item in arr:
                    take_obj(item)
                break
        else:
            take_obj(data)

    # Unique, stable order
    seen: set[str] = set()
    ordered: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            ordered.append(i)
    return ordered


def list_downloaded_models() -> tuple[list[str], str | None]:
    """Return (model_ids, error_message). error_message is None on success."""
    raw, err = _run_lms_ls_json()
    if raw is None:
        return [], err or "lms ls failed"
    try:
        models = parse_lms_ls_json_payload(raw)
    except json.JSONDecodeError as e:
        return [], f"Invalid JSON from lms ls: {e}"
    return models, None
