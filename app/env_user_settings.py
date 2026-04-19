"""Read/write the subset of `.env` keys managed from Settings (schedule, SMTP, LMS CLI)."""

from __future__ import annotations

import os
import re
from pathlib import Path

# Order preserved when writing the managed block.
MANAGED_KEYS: tuple[str, ...] = (
    "LINKEDIN_SCHEDULE_CATCHUP_MIN_GAP_SEC",
    "LINKEDIN_SCHEDULE_STATUS_TZ",
    "LINKEDIN_SMTP_HOST",
    "LINKEDIN_SMTP_PORT",
    "LINKEDIN_SMTP_USER",
    "LINKEDIN_SMTP_FROM",
    "LINKEDIN_SMTP_PASSWORD",
    "LINKEDIN_LMS_CLI",
)


def default_values() -> dict[str, str]:
    """Defaults aligned with app/config.py when variables are unset."""
    return {
        "LINKEDIN_SCHEDULE_CATCHUP_MIN_GAP_SEC": "1800",
        "LINKEDIN_SCHEDULE_STATUS_TZ": "Asia/Kolkata",
        "LINKEDIN_SMTP_HOST": "smtp.gmail.com",
        "LINKEDIN_SMTP_PORT": "587",
        "LINKEDIN_SMTP_USER": "",
        "LINKEDIN_SMTP_FROM": "",
        "LINKEDIN_SMTP_PASSWORD": "",
        "LINKEDIN_LMS_CLI": "lms",
    }


def _strip_quotes(raw: str) -> str:
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


_ENV_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def parse_env_lines(content: str) -> tuple[list[str], dict[str, str]]:
    """Return (non-managed lines in order, managed key -> raw value from file)."""
    managed: dict[str, str] = {}
    kept: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            kept.append(line)
            continue
        m = _ENV_LINE_RE.match(stripped)
        if not m:
            kept.append(line)
            continue
        key, val = m.group(1), m.group(2)
        if key in MANAGED_KEYS:
            managed[key] = _strip_quotes(val)
        else:
            kept.append(line)
    return kept, managed


def render_merged_env(non_managed_lines: list[str], values: dict[str, str]) -> str:
    parts: list[str] = []
    if non_managed_lines:
        parts.append("\n".join(r.rstrip() for r in non_managed_lines).rstrip())
        if parts[0]:
            parts.append("")
    parts.append("# Managed from Settings (LinkedIn Jobs app)")
    for k in MANAGED_KEYS:
        v = values.get(k, "")
        parts.append(f"{k}={_escape_env_value(v)}")
    return "\n".join(parts).rstrip() + "\n"


def _escape_env_value(v: str) -> str:
    if not v:
        return ""
    if any(c in v for c in " \t\n\"#'"):
        return repr(v)
    return v


def read_managed_from_environ() -> dict[str, str]:
    d = default_values()
    for k in MANAGED_KEYS:
        raw = os.environ.get(k)
        if raw is not None:
            d[k] = raw.strip() if isinstance(raw, str) else str(raw)
    return d


def write_default_env_file_if_missing(path: Path) -> None:
    if path.is_file():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    body = render_merged_env([], default_values())
    path.write_text(body, encoding="utf-8")


def merge_and_write_env(path: Path, updates: dict[str, str], *, preserve_blank_smtp_password: bool) -> None:
    """Write `updates` for managed keys; merge with existing file. Non-managed lines preserved."""
    kept: list[str] = []
    from_file: dict[str, str] = {}
    if path.is_file():
        raw = path.read_text(encoding="utf-8")
        kept, from_file = parse_env_lines(raw)
    vals = default_values()
    for k in MANAGED_KEYS:
        if k in from_file:
            vals[k] = from_file[k]
    for k in MANAGED_KEYS:
        if k in updates:
            vals[k] = updates[k]
    pwd_form = (updates.get("LINKEDIN_SMTP_PASSWORD") or "").strip()
    if pwd_form:
        vals["LINKEDIN_SMTP_PASSWORD"] = pwd_form
    elif preserve_blank_smtp_password:
        prev = from_file.get("LINKEDIN_SMTP_PASSWORD")
        if prev is None or prev == "":
            prev = os.environ.get("LINKEDIN_SMTP_PASSWORD", "")
        vals["LINKEDIN_SMTP_PASSWORD"] = prev or ""
    else:
        vals["LINKEDIN_SMTP_PASSWORD"] = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_merged_env(kept, vals), encoding="utf-8")


def form_values_for_template() -> dict[str, str]:
    """Values for Settings form; password always empty string for display."""
    d = read_managed_from_environ()
    d["LINKEDIN_SMTP_PASSWORD"] = ""
    return d
