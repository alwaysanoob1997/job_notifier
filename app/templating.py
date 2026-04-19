from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import schedule_status_tzinfo

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def format_dt(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, dt.datetime):
        return str(value)
    tz = schedule_status_tzinfo()
    v = value
    if v.tzinfo is None:
        v = v.replace(tzinfo=dt.timezone.utc)
    return v.astimezone(tz).strftime("%Y-%m-%d %H:%M")


templates.env.filters["format_dt"] = format_dt
