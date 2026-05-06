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
    dotenv_file_path,
    schedule_catchup_min_gap_before_next_slot_seconds,
    schedule_status_tzinfo,
)
from app.default_system_prompt import DEFAULT_SYSTEM_PROMPT
from app.env_user_settings import (
    default_values,
    form_values_for_template,
    merge_and_write_env,
    secrets_set_flags,
)
from app.dependencies import get_db
from app.llm_score_db import fetch_scores_for_job_ids
from app.models import AppSettings, IdealJobRequirement, Job, JobFilter, ScheduleAudit, ScrapeRun, SystemPromptVersion
from app.services.scheduler import (
    MAX_SCHEDULE_SLOTS,
    daily_run_times,
    effective_daily_slots,
    format_schedule_blurb,
    parse_schedule_time_values,
    refresh_schedule,
    restart_scheduler,
    schedule_option_label,
)
from app.services.filter_delete import get_filter_delete_context, try_delete_filter
from app.services.schedule_sync import clear_filter_schedule_and_audits
from app.services.run_delete import cleanup_llm_scores_for_jobs, delete_scrape_run
from app.services.ideal_job_requirements import get_active_requirement
from app.services.system_prompt_versions import (
    delete_all_system_prompt_versions,
    get_active_system_prompt_version,
)
from app.services.schedule_day_status import (
    STATUS_LABELS,
    compute_slot_day_statuses_for_slots,
    slots_hm_from_schedule_audit,
)
from app.services import run_cancel
from app.services.job_match_scoring import start_score_jobs_for_run_background
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
    has_schedule = bool(slot_strings)
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
            "has_schedule": has_schedule,
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

    if save_kind == "schedule":
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


@router.post("/filters/{filter_id}/schedule/delete", name="delete_filter_schedule")
def delete_filter_schedule(filter_id: int, db: Session = Depends(get_db)):
    ok = clear_filter_schedule_and_audits(db, filter_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Filter not found")
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
    fid = row.filter_id
    ok = clear_filter_schedule_and_audits(db, fid)
    if not ok:
        raise HTTPException(status_code=404, detail="Filter not found")
    db.commit()
    refresh_schedule()
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


@router.post("/runs/{run_id}/rescore")
def run_rescore(run_id: int, db: Session = Depends(get_db)):
    run = db.get(ScrapeRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == "running":
        return RedirectResponse(
            url=f"/runs/{run_id}?blocked=rescore_running",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not start_score_jobs_for_run_background(run_id):
        return RedirectResponse(
            url=f"/runs/{run_id}?blocked=rescore_busy",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/runs/{run_id}?rescored=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _run_is_active(run: ScrapeRun) -> bool:
    """True while the scrape phase or LLM scoring is still working on this run."""
    if run.status == "running":
        return True
    total = run.llm_compare_total
    if total is not None and total > 0 and run.llm_compare_done < total:
        return True
    return False


@router.post("/runs/{run_id}/stop", name="run_stop")
def run_stop(run_id: int, db: Session = Depends(get_db)):
    run = db.get(ScrapeRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not _run_is_active(run):
        return RedirectResponse(
            url=f"/runs/{run_id}?stopped=not_active",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    run_cancel.request_cancel(run_id)
    return RedirectResponse(
        url=f"/runs/{run_id}?stopped=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _parse_managed_config_form(
    gap_s: str,
    tz_s: str,
    host_s: str,
    port_s: str,
    user_s: str,
    from_s: str,
    pwd_s: str,
    cli_s: str,
    lms_auto_start_s: str,
    lms_bind_s: str,
    lms_server_port_s: str,
    openrouter_key_s: str = "",
    custom_key_s: str = "",
) -> tuple[dict[str, str] | None, str | None]:
    gs = (gap_s or "").strip()
    try:
        gap = int(gs if gs else "1800")
    except ValueError:
        return None, "catchup"
    gap = max(0, gap)
    ps = (port_s or "").strip()
    try:
        port = int(ps if ps else "587")
    except ValueError:
        return None, "port"
    if not (1 <= port <= 65535):
        return None, "port"
    lps = (lms_server_port_s or "").strip()
    try:
        lms_port = int(lps if lps else "1234")
    except ValueError:
        return None, "lms_port"
    if not (1 <= lms_port <= 65535):
        return None, "lms_port"
    auto_raw = (lms_auto_start_s or "").strip().lower()
    if auto_raw in ("1", "true", "yes", "on"):
        auto_start_val = "1"
    else:
        auto_start_val = "0"
    bind_val = (lms_bind_s or "").strip() or "0.0.0.0"
    updates = {
        "APP_SCHEDULE_CATCHUP_MIN_GAP_SEC": str(gap),
        "APP_SCHEDULE_STATUS_TZ": (tz_s or "").strip(),
        "APP_SMTP_HOST": (host_s or "").strip(),
        "APP_SMTP_PORT": str(port),
        "APP_SMTP_USER": (user_s or "").strip(),
        "APP_SMTP_FROM": (from_s or "").strip(),
        "APP_SMTP_PASSWORD": (pwd_s or "").strip(),
        "APP_LMS_CLI": ((cli_s or "").strip() or "lms"),
        "APP_LMS_AUTO_START_SERVER": auto_start_val,
        "APP_LMS_SERVER_BIND": bind_val,
        "APP_LMS_SERVER_PORT": str(lms_port),
        "APP_OPENROUTER_API_KEY": (openrouter_key_s or "").strip(),
        "APP_LLM_CUSTOM_API_KEY": (custom_key_s or "").strip(),
    }
    return updates, None


def _reload_dotenv_and_restart_scheduler() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(dotenv_file_path(), override=True)
    restart_scheduler()


@router.get("/settings/advanced")
def advanced_settings(
    request: Request,
    db: Session = Depends(get_db),
    system_prompt_error: str | None = Query(None),
    config_error: str | None = Query(None),
    config_saved: str | None = Query(None),
):
    settings = db.get(AppSettings, 1)
    active_sp = get_active_system_prompt_version(db)
    editor_prompt = DEFAULT_SYSTEM_PROMPT
    if active_sp is not None and (active_sp.prompt or "").strip():
        editor_prompt = (active_sp.prompt or "").strip()
    cfg = form_values_for_template()
    return templates.TemplateResponse(
        request,
        "advanced_settings.html",
        {
            "settings": settings,
            "system_prompt_editor_value": editor_prompt,
            "system_prompt_active_id": active_sp.id if active_sp else None,
            "system_prompt_active_at": active_sp.created_at if active_sp else None,
            "system_prompt_using_builtin": active_sp is None,
            "system_prompt_error": system_prompt_error,
            "open_system_prompt_editor": system_prompt_error == "blank",
            "config_values": cfg,
            "config_error": config_error,
            "config_saved": config_saved,
            "secrets_set": secrets_set_flags(),
        },
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


@router.post("/settings/system-prompt")
def save_system_prompt(
    prompt: str | None = Form(None),
    db: Session = Depends(get_db),
):
    text = (prompt or "").strip()
    if not text:
        return RedirectResponse(
            url="/settings/advanced?system_prompt_error=blank",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    active = get_active_system_prompt_version(db)
    if active is not None and (active.prompt or "").strip() == text:
        return RedirectResponse(url="/settings/advanced", status_code=status.HTTP_303_SEE_OTHER)
    db.add(SystemPromptVersion(prompt=text))
    db.commit()
    return RedirectResponse(url="/settings/advanced", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/system-prompt/reset")
def reset_system_prompt(db: Session = Depends(get_db)):
    delete_all_system_prompt_versions(db)
    db.commit()
    return RedirectResponse(url="/settings/advanced", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/config")
def save_settings_config(
    _db: Session = Depends(get_db),
    APP_SCHEDULE_CATCHUP_MIN_GAP_SEC: str = Form(""),
    APP_SCHEDULE_STATUS_TZ: str = Form(""),
    APP_SMTP_HOST: str = Form(""),
    APP_SMTP_PORT: str = Form(""),
    APP_SMTP_USER: str = Form(""),
    APP_SMTP_FROM: str = Form(""),
    APP_SMTP_PASSWORD: str = Form(""),
    APP_LMS_CLI: str = Form(""),
    APP_LMS_AUTO_START_SERVER: str = Form(""),
    APP_LMS_SERVER_BIND: str = Form(""),
    APP_LMS_SERVER_PORT: str = Form(""),
    APP_OPENROUTER_API_KEY: str = Form(""),
    APP_LLM_CUSTOM_API_KEY: str = Form(""),
):
    parsed, err = _parse_managed_config_form(
        APP_SCHEDULE_CATCHUP_MIN_GAP_SEC,
        APP_SCHEDULE_STATUS_TZ,
        APP_SMTP_HOST,
        APP_SMTP_PORT,
        APP_SMTP_USER,
        APP_SMTP_FROM,
        APP_SMTP_PASSWORD,
        APP_LMS_CLI,
        APP_LMS_AUTO_START_SERVER,
        APP_LMS_SERVER_BIND,
        APP_LMS_SERVER_PORT,
        APP_OPENROUTER_API_KEY,
        APP_LLM_CUSTOM_API_KEY,
    )
    if err is not None or parsed is None:
        return RedirectResponse(
            url=f"/settings/advanced?config_error={err}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    merge_and_write_env(
        dotenv_file_path(),
        parsed,
        preserve_blank_secrets=True,
    )
    _reload_dotenv_and_restart_scheduler()
    return RedirectResponse(url="/settings/advanced?config_saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/config/reset")
def reset_settings_config(_db: Session = Depends(get_db)):
    merge_and_write_env(
        dotenv_file_path(),
        default_values(),
        preserve_blank_secrets=False,
    )
    _reload_dotenv_and_restart_scheduler()
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
def run_detail(
    request: Request,
    run_id: int,
    db: Session = Depends(get_db),
    blocked: str | None = Query(None),
    rescored: str | None = Query(None),
    stopped: str | None = Query(None),
):
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
        {
            "run": run,
            "new_jobs": new_jobs,
            "scores_by_job_id": scores_by_job_id,
            "blocked": blocked,
            "rescored": rescored,
            "stopped": stopped,
            "is_active": _run_is_active(run),
        },
    )


@router.get("/partials/run-jobs/{run_id}", name="run_jobs_partial")
def run_jobs_partial(request: Request, run_id: int, db: Session = Depends(get_db)):
    run = db.get(ScrapeRun, run_id)
    if run is None:
        # 200 so HTMX swaps content instead of erroring; matches the run-status partial.
        return HTMLResponse(
            '<p class="error">This run no longer exists. <a href="/runs">Open past runs</a>.</p>',
            headers={"Cache-Control": "no-store"},
        )
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
        "partials/run_jobs_table.html",
        {
            "run": run,
            "new_jobs": new_jobs,
            "scores_by_job_id": scores_by_job_id,
            "is_active": _run_is_active(run),
        },
        headers={"Cache-Control": "no-store"},
    )
