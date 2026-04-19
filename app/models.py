from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AppSettings(Base):
    """Global Chrome paths only; search/schedule live on JobFilter."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    chrome_executable_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    chrome_binary_location: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
        onupdate=lambda: dt.datetime.now(dt.timezone.utc),
    )


class IdealJobRequirement(Base):
    """Append-only versions of the user's ideal job description; latest row is active."""

    __tablename__ = "ideal_job_requirements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    notify_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    notify_email: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )


class SystemPromptVersion(Base):
    """Append-only versions of the LLM system prompt for job match scoring; latest row is active."""

    __tablename__ = "system_prompt_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )


class JobFilter(Base):
    __tablename__ = "job_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    job_title: Mapped[str] = mapped_column(String(512), default="")
    location: Mapped[str] = mapped_column(String(512), default="Bengaluru")
    runs_per_day: Mapped[int] = mapped_column(Integer, default=0)
    schedule_times_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
        onupdate=lambda: dt.datetime.now(dt.timezone.utc),
    )


class ScheduleAudit(Base):
    """Log of saved schedule snapshots (same DB as main app)."""

    __tablename__ = "schedule_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schedule_id: Mapped[str] = mapped_column(String(36), nullable=False)
    logged_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filter_id: Mapped[int] = mapped_column(Integer, ForeignKey("job_filters.id"), nullable=False)
    runs_count: Mapped[int] = mapped_column(Integer, nullable=False)
    schedule_times_json: Mapped[str] = mapped_column(Text, nullable=False)
    runs_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    filter_name: Mapped[str] = mapped_column(String(256), default="")
    job_title: Mapped[str] = mapped_column(String(512), default="")
    location: Mapped[str] = mapped_column(String(512), default="")
    timezone_name: Mapped[str] = mapped_column(String(256), default="")


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filter_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("job_filters.id"), nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    job_title: Mapped[str] = mapped_column(String(512), default="")
    location: Mapped[str] = mapped_column(String(512), default="")
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    jobs_returned: Mapped[int] = mapped_column(Integer, default=0)
    jobs_new: Mapped[int] = mapped_column(Integer, default=0)
    jobs_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    scrape_target_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    llm_compare_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    llm_compare_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen_run_id: Mapped[int] = mapped_column(Integer, ForeignKey("scrape_runs.id"), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    query: Mapped[str] = mapped_column(String(512), default="")
    location: Mapped[str] = mapped_column(String(512), default="")
    title: Mapped[str] = mapped_column(String(1024), default="")
    company: Mapped[str] = mapped_column(String(512), default="")
    place: Mapped[str] = mapped_column(String(512), default="")
    link: Mapped[str] = mapped_column(String(2048), default="")
    apply_link: Mapped[str] = mapped_column(String(2048), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    description_html: Mapped[str] = mapped_column(Text, default="")
    date: Mapped[str] = mapped_column(String(128), default="")
    date_text: Mapped[str] = mapped_column(String(512), default="")
    salary: Mapped[str] = mapped_column(String(512), default="")
