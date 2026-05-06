"""Read/write the subset of `.env` keys managed from Settings (schedule, SMTP, LMS CLI)."""

from __future__ import annotations

import os
import re
from pathlib import Path

# Order preserved when writing the managed block.
MANAGED_KEYS: tuple[str, ...] = (
    "APP_SCHEDULE_CATCHUP_MIN_GAP_SEC",
    "APP_SCHEDULE_STATUS_TZ",
    "APP_SMTP_HOST",
    "APP_SMTP_PORT",
    "APP_SMTP_USER",
    "APP_SMTP_FROM",
    "APP_SMTP_PASSWORD",
    "APP_LMS_CLI",
    "APP_LMS_AUTO_START_SERVER",
    "APP_LMS_SERVER_BIND",
    "APP_LMS_SERVER_PORT",
    "APP_OPENROUTER_API_KEY",
    "APP_LLM_CUSTOM_API_KEY",
)

# Secret keys whose form value is blank by default; submit-blank means "keep existing value"
# rather than "clear", same UX as APP_SMTP_PASSWORD.
SECRET_KEYS: tuple[str, ...] = (
    "APP_SMTP_PASSWORD",
    "APP_OPENROUTER_API_KEY",
    "APP_LLM_CUSTOM_API_KEY",
)


def default_values() -> dict[str, str]:
    """Defaults aligned with app/config.py when variables are unset."""
    return {
        "APP_SCHEDULE_CATCHUP_MIN_GAP_SEC": "1800",
        "APP_SCHEDULE_STATUS_TZ": "Asia/Kolkata",
        "APP_SMTP_HOST": "smtp.gmail.com",
        "APP_SMTP_PORT": "587",
        "APP_SMTP_USER": "",
        "APP_SMTP_FROM": "",
        "APP_SMTP_PASSWORD": "",
        "APP_LMS_CLI": "lms",
        "APP_LMS_AUTO_START_SERVER": "1",
        "APP_LMS_SERVER_BIND": "0.0.0.0",
        "APP_LMS_SERVER_PORT": "1234",
        "APP_OPENROUTER_API_KEY": "",
        "APP_LLM_CUSTOM_API_KEY": "",
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
    parts.append("# Managed from Settings")
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


def merge_and_write_env(path: Path, updates: dict[str, str], *, preserve_blank_secrets: bool) -> None:
    """Write `updates` for managed keys; merge with existing file. Non-managed lines preserved.

    For ``SECRET_KEYS`` (passwords, API tokens), a blank value in ``updates`` means "keep the
    current secret" when ``preserve_blank_secrets`` is True; otherwise the secret is cleared.
    """
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
        if k in updates and k not in SECRET_KEYS:
            vals[k] = updates[k]
    for k in SECRET_KEYS:
        submitted = (updates.get(k) or "").strip()
        if submitted:
            vals[k] = submitted
        elif preserve_blank_secrets:
            prev = from_file.get(k)
            if prev is None or prev == "":
                prev = os.environ.get(k, "")
            vals[k] = prev or ""
        else:
            vals[k] = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_merged_env(kept, vals), encoding="utf-8")


def form_values_for_template() -> dict[str, str]:
    """Values for Settings form; secret fields always empty for display."""
    d = read_managed_from_environ()
    for k in SECRET_KEYS:
        d[k] = ""
    return d


def secrets_set_flags() -> dict[str, bool]:
    """Whether each secret key currently has a non-empty value (for "is set" hints in the UI)."""
    raw = read_managed_from_environ()
    return {k: bool((raw.get(k) or "").strip()) for k in SECRET_KEYS}
