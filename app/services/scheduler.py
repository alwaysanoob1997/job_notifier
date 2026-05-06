from __future__ import annotations

import datetime as dt
import json
import logging
import os
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from sqlalchemy import select

from app.config import (
    dotenv_file_path,
    schedule_catchup_min_gap_before_next_slot_seconds,
    schedule_status_tzinfo,
)
from app.db import session_scope
from app.models import JobFilter, ScrapeRun
from app.services.scrape_runner import start_scrape_if_idle_for_filter

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

MAX_SCHEDULE_SLOTS = 5


def _tz_label(tz: dt.tzinfo) -> str:
    key = getattr(tz, "key", None)
    if isinstance(key, str) and key:
        return key
    return str(tz)


def _resolved_zone_looks_like_utc(tz: dt.tzinfo) -> bool:
    key = (getattr(tz, "key", None) or "").upper()
    if key in ("UTC", "ETC/UTC", "GMT", "ETC/GMT", "ETC/GMT+0", "ETC/GMT-0"):
        return True
    return str(tz).upper() == "UTC"


def daily_run_times(runs_per_day: int) -> list[tuple[int, int]]:
    """Evenly spaced (hour, minute) in local day, first run at 00:00. Zero means no schedule."""
    if runs_per_day <= 0:
        return []
    n = max(1, min(MAX_SCHEDULE_SLOTS, runs_per_day))
    out: list[tuple[int, int]] = []
    for i in range(n):
        minute_of_day = (24 * 60 * i) // n
        out.append((minute_of_day // 60, minute_of_day % 60))
    return out


def schedule_option_label(runs_per_day: int) -> str:
    n = max(1, min(MAX_SCHEDULE_SLOTS, runs_per_day))
    parts = [f"{h:02d}:{m:02d}" for h, m in daily_run_times(n)]
    return f"{n}× evenly spaced — " + ", ".join(parts)


def _parse_hh_mm_token(s: str) -> tuple[int, int] | None:
    s = (s or "").strip()
    if not s:
        return None
    if "T" in s:
        s = s.split("T", 1)[-1]
    parts = s.replace(".", ":").split(":")
    if len(parts) < 2:
        return None
    try:
        h = int(parts[0])
        m = int(parts[1])
    except ValueError:
        return None
    if not (0 <= h < 24 and 0 <= m < 60):
        return None
    return h, m


def custom_times_from_json(json_str: str | None) -> list[tuple[int, int]] | None:
    if not json_str or not str(json_str).strip():
        return None
    try:
        arr = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(arr, list) or len(arr) == 0:
        return None
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int]] = []
    for item in arr:
        t = _parse_hh_mm_token(str(item))
        if t is None or t in seen:
            continue
        seen.add(t)
        out.append(t)
    out.sort()
    return out if out else None


def effective_daily_slots(job: JobFilter) -> list[tuple[int, int]]:
    c = custom_times_from_json(job.schedule_times_json)
    if c:
        return c[:MAX_SCHEDULE_SLOTS]
    return daily_run_times(job.runs_per_day or 0)


def format_schedule_blurb(job: JobFilter) -> str:
    slots = effective_daily_slots(job)
    if not slots:
        return "No automatic runs"
    parts = [f"{h:02d}:{m:02d}" for h, m in slots]
    return "Runs at " + ", ".join(parts)


def parse_schedule_time_values(values: list[str]) -> list[tuple[int, int]]:
    seen: set[tuple[int, int]] = set()
    out: list[tuple[int, int]] = []
    for v in values:
        t = _parse_hh_mm_token(v)
        if t is None or t in seen:
            continue
        seen.add(t)
        out.append(t)
    out.sort()
    return out[:MAX_SCHEDULE_SLOTS]


def scheduled_scrape_for_filter(filter_id: int) -> None:
    rid = start_scrape_if_idle_for_filter(filter_id, "scheduled")
    if rid is None:
        logger.info("Scheduled scrape skipped filter_id=%s (already running)", filter_id)
    else:
        logger.info("Scheduled scrape started filter_id=%s run_id=%s", filter_id, rid)


def reconcile_missed_scheduled_runs(
    now_utc: dt.datetime | None = None,
    tz_override: dt.tzinfo | None = None,
    min_gap_before_next_slot_sec: int | None = None,
) -> None:
    """If a slot was missed since the last run, start one catch-up unless the next slot is too soon.

    The minimum gap before the next slot defaults to 30 minutes (see
    schedule_catchup_min_gap_before_next_slot_seconds); set to 0 to always catch up when a slot was missed.
    """
    gap_sec = (
        min_gap_before_next_slot_sec
        if min_gap_before_next_slot_sec is not None
        else schedule_catchup_min_gap_before_next_slot_seconds()
    )
    if now_utc is None:
        now_utc = dt.datetime.now(dt.timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=dt.timezone.utc)
    tz = tz_override if tz_override is not None else schedule_status_tzinfo()
    now_local = now_utc.astimezone(tz)

    catch_filter_ids: list[int] = []
    with session_scope() as session:
        filters = list(session.scalars(select(JobFilter).order_by(JobFilter.id)))
        for filt in filters:
            slots = effective_daily_slots(filt)
            if not slots or not (filt.job_title or "").strip():
                continue
            last_run = session.scalar(
                select(ScrapeRun)
                .where(ScrapeRun.filter_id == filt.id)
                .order_by(ScrapeRun.started_at.desc())
                .limit(1)
            )
            if last_run is None:
                continue
            last_utc = last_run.started_at
            if last_utc.tzinfo is None:
                last_utc = last_utc.replace(tzinfo=dt.timezone.utc)
            last_local = last_utc.astimezone(tz)
            start_date = last_local.date()
            end_date = now_local.date() + dt.timedelta(days=1)
            if (end_date - start_date).days > 31:
                start_date = end_date - dt.timedelta(days=31)
            instances: list[dt.datetime] = []
            day = start_date
            one = dt.timedelta(days=1)
            while day <= end_date:
                for h, m in slots:
                    instances.append(
                        dt.datetime.combine(day, dt.time(hour=h, minute=m), tzinfo=tz)
                    )
                day += one
            instances.sort()
            missed = False
            for s in instances:
                s_utc = s.astimezone(dt.timezone.utc)
                if last_utc < s_utc < now_utc:
                    missed = True
                    break
            if not missed:
                continue
            next_after_utc: dt.datetime | None = None
            for s in instances:
                s_utc = s.astimezone(dt.timezone.utc)
                if s_utc > now_utc:
                    next_after_utc = s_utc
                    break
            if next_after_utc is None:
                continue
            if gap_sec > 0 and (next_after_utc - now_utc).total_seconds() <= gap_sec:
                continue
            catch_filter_ids.append(filt.id)

    for fid in catch_filter_ids:
        rid = start_scrape_if_idle_for_filter(fid, "catch_up")
        if rid is not None:
            logger.info(
                "Catch-up scrape started filter_id=%s run_id=%s (missed slot; min_gap_before_next_sec=%s)",
                fid,
                rid,
                gap_sec,
            )


def refresh_schedule() -> None:
    global _scheduler
    if _scheduler is None:
        return
    for j in list(_scheduler.get_jobs()):
        jid = str(j.id)
        if jid.startswith("scrape_filter_"):
            _scheduler.remove_job(j.id)
    with session_scope() as session:
        filter_slots: list[tuple[int, list[tuple[int, int]]]] = [
            (f.id, effective_daily_slots(f))
            for f in session.scalars(select(JobFilter).order_by(JobFilter.id))
        ]
    tz = schedule_status_tzinfo()
    total = 0
    for filt_id, times in filter_slots:
        for i, (h, m) in enumerate(times):
            trig = CronTrigger(hour=h, minute=m, timezone=tz)
            _scheduler.add_job(
                scheduled_scrape_for_filter,
                trig,
                id=f"scrape_filter_{filt_id}_slot_{i}",
                args=[filt_id],
                replace_existing=True,
            )
            total += 1
    logger.info("APScheduler: registered %s job(s) across %s filter(s)", total, len(filter_slots))


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    env_path = dotenv_file_path()
    env_exists = env_path.is_file()
    raw_tz = os.environ.get("APP_SCHEDULE_STATUS_TZ", "").strip()
    tz = schedule_status_tzinfo()
    tz_label = _tz_label(tz)
    logger.info(
        "Schedule zone: dotenv_path=%s exists=%s APP_SCHEDULE_STATUS_TZ=%r resolved=%s",
        env_path,
        env_exists,
        raw_tz or "(unset)",
        tz_label,
    )
    if env_exists and not raw_tz and _resolved_zone_looks_like_utc(tz):
        logger.warning(
            "Schedule zone resolved to %s but APP_SCHEDULE_STATUS_TZ is unset. "
            "On WSL the system zone is often UTC while you want local run times — set "
            "APP_SCHEDULE_STATUS_TZ (e.g. Asia/Kolkata) in .env.",
            tz_label,
        )
    _scheduler = BackgroundScheduler(
        timezone=tz,
        job_defaults={"misfire_grace_time": 300, "coalesce": True},
    )
    refresh_schedule()
    _scheduler.add_job(
        reconcile_missed_scheduled_runs,
        "interval",
        seconds=60,
        id="reconcile_missed_interval",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("APScheduler started (timezone=%s)", tz_label)


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("APScheduler shut down")


def restart_scheduler() -> None:
    """Rebuild the scheduler so schedule timezone and env-driven catch-up use current os.environ."""
    shutdown_scheduler()
    start_scheduler()
    reconcile_missed_scheduled_runs()
