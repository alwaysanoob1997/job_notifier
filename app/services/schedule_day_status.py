"""Per-slot status for a calendar day (local timezone), aligned with scheduler windows."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from app.models import JobFilter, ScheduleAudit, ScrapeRun
from app.services.scheduler import MAX_SCHEDULE_SLOTS, custom_times_from_json, daily_run_times, effective_daily_slots

SlotStatusKey = Literal["upcoming", "done", "missed", "skipped_catchup"]

STATUS_LABELS: dict[SlotStatusKey, str] = {
    "upcoming": "Upcoming",
    "done": "Done",
    "missed": "Missed",
    "skipped_catchup": "Skipping due to catch-up",
}


@dataclass(frozen=True)
class SlotDayStatusRow:
    """One scheduled local time on `day` and its derived status."""

    slot_label: str
    slot_start_local: dt.datetime
    window_end_local: dt.datetime
    status: SlotStatusKey
    run_id: int | None
    run_trigger: str | None


def _slot_starts_for_day(
    day: dt.date, slots_hm: list[tuple[int, int]], tz: dt.tzinfo
) -> list[dt.datetime]:
    out: list[dt.datetime] = []
    for h, m in slots_hm:
        out.append(dt.datetime.combine(day, dt.time(hour=h, minute=m), tzinfo=tz))
    out.sort()
    return out


def _first_slot_next_day(day: dt.date, slots_hm: list[tuple[int, int]], tz: dt.tzinfo) -> dt.datetime:
    h0, m0 = min(slots_hm)
    return dt.datetime.combine(day + dt.timedelta(days=1), dt.time(hour=h0, minute=m0), tzinfo=tz)


def _started_utc(r: ScrapeRun) -> dt.datetime:
    st = r.started_at
    if st.tzinfo is None:
        st = st.replace(tzinfo=dt.timezone.utc)
    return st.astimezone(dt.timezone.utc)


def slots_hm_from_schedule_audit(audit: ScheduleAudit) -> list[tuple[int, int]]:
    """Slot times stored on a schedule snapshot row."""
    c = custom_times_from_json(audit.schedule_times_json)
    if c:
        return c[:MAX_SCHEDULE_SLOTS]
    n = audit.runs_per_day or 0
    if n <= 0:
        return []
    return daily_run_times(n)


def compute_slot_day_statuses_for_slots(
    slots_hm: list[tuple[int, int]],
    runs_for_filter: list[ScrapeRun],
    day: dt.date,
    now_utc: dt.datetime,
    tz: dt.tzinfo,
    gap_before_next_slot_sec: int,
) -> list[SlotDayStatusRow]:
    """Build one row per local slot time on `day` (same window rules as the live scheduler)."""
    if not slots_hm:
        return []
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=dt.timezone.utc)
    now_local = now_utc.astimezone(tz)

    starts = _slot_starts_for_day(day, slots_hm, tz)
    if not starts:
        return []

    next_day_first = _first_slot_next_day(day, slots_hm, tz)
    rows: list[SlotDayStatusRow] = []
    for i, slot_start in enumerate(starts):
        if i + 1 < len(starts):
            window_end = starts[i + 1]
        else:
            window_end = next_day_first

        label = slot_start.strftime("%H:%M")
        slot_start_utc = slot_start.astimezone(dt.timezone.utc)
        window_end_utc = window_end.astimezone(dt.timezone.utc)

        run_in_window: ScrapeRun | None = None
        best: dt.datetime | None = None
        for r in runs_for_filter:
            st = _started_utc(r)
            if slot_start_utc <= st < window_end_utc:
                if best is None or st < best:
                    best = st
                    run_in_window = r

        if now_local < slot_start:
            status: SlotStatusKey = "upcoming"
        elif run_in_window is not None:
            status = "done"
        elif now_local < window_end:
            status = "upcoming"
        else:
            window_sec = (window_end - slot_start).total_seconds()
            if gap_before_next_slot_sec > 0 and window_sec <= gap_before_next_slot_sec:
                status = "skipped_catchup"
            else:
                status = "missed"

        rt = run_in_window.trigger if run_in_window else None
        rid = run_in_window.id if run_in_window else None
        rows.append(
            SlotDayStatusRow(
                slot_label=label,
                slot_start_local=slot_start,
                window_end_local=window_end,
                status=status,
                run_id=rid,
                run_trigger=rt,
            )
        )
    return rows


def compute_slot_day_statuses(
    filt: JobFilter,
    runs_for_filter: list[ScrapeRun],
    day: dt.date,
    now_utc: dt.datetime,
    tz: dt.tzinfo,
    gap_before_next_slot_sec: int,
) -> list[SlotDayStatusRow]:
    """Build one row per schedule slot on `day` for this filter."""
    slots_hm = effective_daily_slots(filt)
    return compute_slot_day_statuses_for_slots(
        slots_hm, runs_for_filter, day, now_utc, tz, gap_before_next_slot_sec
    )


def filter_slot_rows(
    rows: list[SlotDayStatusRow], status_key: str | None
) -> list[SlotDayStatusRow]:
    if not status_key or status_key == "all":
        return rows
    allowed: set[SlotStatusKey] = {"upcoming", "done", "missed", "skipped_catchup"}
    if status_key not in allowed:
        return rows
    sk: SlotStatusKey = status_key  # type: ignore[assignment]
    return [r for r in rows if r.status == sk]
