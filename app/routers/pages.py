from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from app.config import (
    schedule_catchup_min_gap_before_next_slot_seconds,
    schedule_status_tzinfo,
)
from app.dependencies import get_db
from app.llm_score_db import fetch_scores_for_job_ids
from app.models import AppSettings, IdealJobRequirement, Job, JobFilter, ScheduleAudit, ScrapeRun
from app.services.scheduler import (
    MAX_SCHEDULE_SLOTS,
    daily_run_times,
    effective_daily_slots,
    format_schedule_blurb,
    parse_schedule_time_values,
    refresh_schedule,
    schedule_option_label,
)
from app.services.filter_delete import get_filter_delete_context, try_delete_filter
from app.services.run_delete import cleanup_llm_scores_for_jobs, delete_scrape_run
from app.services.ideal_job_requirements import get_active_requirement
from app.services.schedule_day_status import (
    STATUS_LABELS,
    compute_slot_day_statuses_for_slots,
    slots_hm_from_schedule_audit,
)
from app.services.scrape_runner import start_scrape_if_idle_for_filter
from app.templating import templates

router = APIRouter()


def _parse_schedules_day(value: str | None, fallback: date) -> date:
    if not value or not str(value).strip():
        return fallback
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return fallback


def _tz_storage_name(tz: tzinfo) -> str:
    key = getattr(tz, "key", None)
    if isinstance(key, str) and key:
        return key
    return str(tz)


def _timezone_from_audit_field(timezone_name: str | None) -> tzinfo:
    n = (timezone_name or "").strip()
    if n:
        try:
            return ZoneInfo(n)
        except Exception:
            pass
    return schedule_status_tzinfo()


def _clamp_runs_per_day(n: int) -> int:
    return max(1, min(MAX_SCHEDULE_SLOTS, int(n)))


def _matching_even_preset_key(slot_strings: list[str], even_presets: dict[str, list[str]]) -> str:
    """Return '1'..'5' if slots match an even-spacing preset (order-independent), else ''."""
    if not slot_strings:
        return ""
    ss_sorted = sorted(slot_strings)
    for n in range(1, MAX_SCHEDULE_SLOTS + 1):
        k = str(n)
        arr = even_presets.get(k) or []
        if sorted(arr) == ss_sorted:
            return k
    return ""


@router.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    filters = list(db.scalars(select(JobFilter).order_by(JobFilter.id)))
    schedule_blurbs = {f.id: format_schedule_blurb(f) for f in filters}
    active_req = get_active_requirement(db)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "filters": filters,
            "schedule_blurbs": schedule_blurbs,
            "ideal_job_description": active_req.description if active_req else "",
            "ideal_job_active_id": active_req.id if active_req else None,
            "ideal_job_active_at": active_req.created_at if active_req else None,
            "ideal_job_notify_threshold": active_req.notify_threshold if active_req else 60,
            "ideal_job_notify_email": (active_req.notify_email or "") if active_req else "",
        },
    )


def _clamp_notify_threshold(raw: str | None) -> int:
    if raw is None or not str(raw).strip():
        return 60
    try:
        n = int(str(raw).strip())
    except ValueError:
        return 60
    return max(0, min(100, n))


def _normalize_notify_email(raw: str | None) -> str | None:
    s = (raw or "").strip()
    return s or None


@router.post("/ideal-job-requirements")
def save_ideal_job_requirements(
    description: str | None = Form(None),
    notify_threshold: str | None = Form(None),
    notify_email: str | None = Form(None),
    db: Session = Depends(get_db),
):
    text = "" if description is None else str(description)
    thr = _clamp_notify_threshold(notify_threshold)
    email_norm = _normalize_notify_email(notify_email)

    active = get_active_requirement(db)
    base_desc = active.description if active is not None else ""
    base_thr = active.notify_threshold if active is not None else 60
    base_email = active.notify_email if active is not None else None

    if not text.strip() and active is None:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    if text == base_desc and thr == base_thr and email_norm == base_email:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    row = IdealJobRequirement(description=text, notify_threshold=thr, notify_email=email_norm)
    db.add(row)
    db.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/filters/new")
def create_filter(db: Session = Depends(get_db)):
    row = JobFilter(name="", job_title="", location="Bengaluru", runs_per_day=0)
    db.add(row)
    db.commit()
    db.refresh(row)
    refresh_schedule()
    return RedirectResponse(url=f"/filters/{row.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/filters/{filter_id}/delete")
def filter_delete_confirm(
    request: Request,
    filter_id: int,
    db: Session = Depends(get_db),
    blocked: str | None = None,
):
    ctx = get_filter_delete_context(db, filter_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Filter not found")
    warn_schedules = (
        ctx["schedule_audit_count"] > 0
        or ctx["scrape_run_count"] > 0
        or ctx["has_active_job_title"]
    )
    return templates.TemplateResponse(
        request,
        "filter_delete_confirm.html",
        {
            "ctx": ctx,
            "warn_schedules": warn_schedules,
            "blocked_running": blocked == "running",
        },
    )


@router.post("/filters/{filter_id}/delete")
def filter_delete_execute(filter_id: int, db: Session = Depends(get_db)):
    ok, err = try_delete_filter(db, filter_id)
    if not ok:
        if err == "not_found":
            raise HTTPException(status_code=404, detail="Filter not found")
        if err == "running":
            db.rollback()
            return RedirectResponse(
                url=f"/filters/{filter_id}/delete?blocked=running",
                status_code=status.HTTP_303_SEE_OTHER,
            )
    db.commit()
    refresh_schedule()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/filters/{filter_id}")
def filter_detail(request: Request, filter_id: int, db: Session = Depends(get_db)):
    filt = db.get(JobFilter, filter_id)
    if filt is None:
        raise HTTPException(status_code=404, detail="Filter not found")
    slot_strings = [f"{h:02d}:{m:02d}" for h, m in effective_daily_slots(filt)]
    schedule_slots_json = json.dumps(slot_strings)
    even_presets = {
        str(n): [f"{h:02d}:{m:02d}" for h, m in daily_run_times(n)]
        for n in range(1, MAX_SCHEDULE_SLOTS + 1)
    }
    even_presets_json = json.dumps(even_presets)
    schedule_options = [(n, schedule_option_label(n)) for n in range(1, MAX_SCHEDULE_SLOTS + 1)]
    initial_schedule_preset = _matching_even_preset_key(slot_strings, even_presets)
    schedule_blurb = format_schedule_blurb(filt)
    return templates.TemplateResponse(
        request,
        "filter_detail.html",
        {
            "filter": filt,
            "schedule_options": schedule_options,
            "schedule_slot_strings": slot_strings,
            "schedule_slots_json": schedule_slots_json,
            "even_presets_json": even_presets_json,
            "max_schedule_slots": MAX_SCHEDULE_SLOTS,
            "initial_schedule_preset": initial_schedule_preset,
            "schedule_blurb": schedule_blurb,
        },
    )


@router.post("/filters/{filter_id}")
def save_filter(
    filter_id: int,
    name: str = Form(""),
    job_title: str = Form(""),
    location: str = Form("Bengaluru"),
    schedule_payload: str = Form("[]"),
    save_kind: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    filt = db.get(JobFilter, filter_id)
    if filt is None:
        raise HTTPException(status_code=404, detail="Filter not found")
    filt.name = name.strip()
    filt.job_title = job_title.strip()
    filt.location = (location or "").strip() or "Bengaluru"

    if save_kind != "search":
        prev_stored = (filt.schedule_times_json, filt.runs_per_day)
        try:
            arr = json.loads(schedule_payload) if (schedule_payload or "").strip() else []
        except json.JSONDecodeError:
            arr = []
        if not isinstance(arr, list):
            arr = []
        parsed = parse_schedule_time_values([str(x) for x in arr])
        if parsed:
            filt.schedule_times_json = json.dumps([f"{h:02d}:{m:02d}" for h, m in parsed])
            filt.runs_per_day = _clamp_runs_per_day(len(parsed))
        else:
            filt.schedule_times_json = None
            filt.runs_per_day = 0
        after_stored = (filt.schedule_times_json, filt.runs_per_day)
        if prev_stored != after_stored:
            after_slots = tuple(effective_daily_slots(filt))
            times_snapshot = filt.schedule_times_json or json.dumps(
                [f"{h:02d}:{m:02d}" for h, m in after_slots]
            )
            db.execute(delete(ScheduleAudit).where(ScheduleAudit.filter_id == filt.id))
            db.add(
                ScheduleAudit(
                    schedule_id=str(uuid.uuid4()),
                    logged_at=datetime.now(timezone.utc),
                    filter_id=filt.id,
                    runs_count=len(after_slots),
                    schedule_times_json=times_snapshot,
                    runs_per_day=filt.runs_per_day,
                    filter_name=filt.name or "",
                    job_title=filt.job_title or "",
                    location=filt.location or "",
                    timezone_name=_tz_storage_name(schedule_status_tzinfo()),
                )
            )

    db.commit()
    refresh_schedule()
    return RedirectResponse(url=f"/filters/{filter_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/filters/{filter_id}/runs")
def start_run_for_filter(request: Request, filter_id: int, db: Session = Depends(get_db)):
    filt = db.get(JobFilter, filter_id)
    if filt is None:
        raise HTTPException(status_code=404, detail="Filter not found")
    if not (filt.job_title or "").strip():
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<p class="error">Save a job title for this filter first.</p>',
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set a job title for this filter before running.",
        )
    rid = start_scrape_if_idle_for_filter(filter_id, "manual")
    if rid is None:
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                '<p class="error">A scrape is already running.</p>',
                status_code=status.HTTP_409_CONFLICT,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A scrape is already running.",
        )
    if request.headers.get("HX-Request"):
        return Response(
            status_code=200,
            headers={"HX-Redirect": f"/runs/{rid}"},
        )
    return RedirectResponse(url=f"/runs/{rid}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/schedules")
def schedules_list(request: Request, db: Session = Depends(get_db)):
    rows = list(
        db.scalars(select(ScheduleAudit).order_by(ScheduleAudit.logged_at.desc()).limit(300))
    )
    return templates.TemplateResponse(
        request,
        "schedules_list.html",
        {"audits": rows},
    )


@router.get("/schedules/audit/{audit_id}/slots", name="schedule_audit_slots")
def schedule_audit_slots_partial(
    request: Request,
    audit_id: int,
    db: Session = Depends(get_db),
    day: str | None = None,
):
    """HTMX fragment: per-run status for one saved schedule snapshot (click row on /schedules)."""
    audit = db.get(ScheduleAudit, audit_id)
    if audit is None:
        return HTMLResponse('<p class="error">Schedule log not found.</p>', status_code=404)

    tz = _timezone_from_audit_field(audit.timezone_name)
    logged = audit.logged_at
    if logged.tzinfo is None:
        logged = logged.replace(tzinfo=timezone.utc)
    default_day = logged.astimezone(tz).date()
    chosen_day = _parse_schedules_day(day, default_day)

    slots_hm = slots_hm_from_schedule_audit(audit)
    gap_sec = schedule_catchup_min_gap_before_next_slot_seconds()
    now_utc = datetime.now(timezone.utc)

    runs: list[ScrapeRun] = []
    if slots_hm:
        day0_local = datetime.combine(chosen_day, datetime.min.time(), tzinfo=tz)
        lo_utc = day0_local.astimezone(timezone.utc) - timedelta(days=1)
        hi_utc = (day0_local + timedelta(days=2)).astimezone(timezone.utc)
        runs = list(
            db.scalars(
                select(ScrapeRun)
                .where(
                    and_(
                        ScrapeRun.filter_id == audit.filter_id,
                        ScrapeRun.started_at >= lo_utc,
                        ScrapeRun.started_at < hi_utc,
                    )
                )
                .order_by(ScrapeRun.started_at)
            )
        )

    slot_rows = compute_slot_day_statuses_for_slots(
        slots_hm, runs, chosen_day, now_utc, tz, gap_sec
    )
    tz_label = getattr(tz, "key", None) or str(tz)

    return templates.TemplateResponse(
        request,
        "partials/schedule_audit_slots.html",
        {
            "rows": slot_rows,
            "status_labels": STATUS_LABELS,
            "day_label": chosen_day.isoformat(),
            "tz_label": tz_label,
            "filter_id": audit.filter_id,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/schedules/audit/{audit_id}/delete")
def delete_schedule_audit(audit_id: int, db: Session = Depends(get_db)):
    row = db.get(ScheduleAudit, audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule log entry not found")
    db.delete(row)
    db.commit()
    return RedirectResponse(url="/schedules", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/runs")
def runs_list(
    request: Request,
    db: Session = Depends(get_db),
    blocked: str | None = Query(None),
    deleted: str | None = Query(None),
):
    runs = list(db.scalars(select(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(100)))
    return templates.TemplateResponse(
        request,
        "runs_list.html",
        {
            "runs": runs,
            "blocked": blocked,
            "deleted": deleted,
        },
    )


@router.post("/runs/{run_id}/delete")
def run_delete(run_id: int, db: Session = Depends(get_db)):
    ok, err, job_ids = delete_scrape_run(db, run_id)
    if not ok:
        if err == "not_found":
            raise HTTPException(status_code=404, detail="Run not found")
        db.rollback()
        return RedirectResponse(
            url="/runs?blocked=running",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    db.commit()
    cleanup_llm_scores_for_jobs(job_ids)
    return RedirectResponse(
        url="/runs?deleted=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/settings/advanced")
def advanced_settings(request: Request, db: Session = Depends(get_db)):
    settings = db.get(AppSettings, 1)
    return templates.TemplateResponse(
        request,
        "advanced_settings.html",
        {"settings": settings},
    )


@router.post("/settings/advanced")
def save_advanced_settings(
    chrome_executable_path: str = Form(""),
    chrome_binary_location: str = Form(""),
    db: Session = Depends(get_db),
):
    row = db.get(AppSettings, 1)
    if row is None:
        row = AppSettings(id=1)
        db.add(row)
    row.chrome_executable_path = chrome_executable_path.strip() or None
    row.chrome_binary_location = chrome_binary_location.strip() or None
    db.commit()
    return RedirectResponse(url="/settings/advanced", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/partials/run-status/{run_id}")
def run_status_partial(request: Request, run_id: int, db: Session = Depends(get_db)):
    run = db.get(ScrapeRun, run_id)
    if run is None:
        # 200 so HTMX swaps content instead of erroring; avoids log spam when a stale
        # /runs/{id} tab keeps polling after the DB was wiped or the run was removed.
        return HTMLResponse(
            '<p class="error">This run no longer exists (for example the database was reset). '
            '<a href="/runs">Open past runs</a> or <a href="/">home</a>.</p>',
            headers={"Cache-Control": "no-store"},
        )
    return templates.TemplateResponse(
        request,
        "partials/run_status_inner.html",
        {"run": run},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/runs/{run_id}")
def run_detail(request: Request, run_id: int, db: Session = Depends(get_db)):
    run = db.get(ScrapeRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    new_jobs = list(
        db.scalars(
            select(Job)
            .where(Job.first_seen_run_id == run_id)
            .order_by(Job.created_at.desc())
        )
    )
    scores_by_job_id = fetch_scores_for_job_ids([j.job_id for j in new_jobs])
    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {"run": run, "new_jobs": new_jobs, "scores_by_job_id": scores_by_job_id},
    )
