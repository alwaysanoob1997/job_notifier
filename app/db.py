from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import database_url, ensure_data_dir
from app.models import AppSettings, Base, JobFilter

_engine = None
_SessionLocal = None


def reset_engine() -> None:
    """Dispose engine (e.g. between tests with different DB URLs)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        if not os.environ.get("LINKEDIN_JOBS_DB_URL"):
            ensure_data_dir()
        else:
            raw = os.environ["LINKEDIN_JOBS_DB_URL"]
            if raw.startswith("sqlite:///"):
                p = Path(raw.replace("sqlite:///", "", 1))
                p.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            database_url(),
            connect_args={"check_same_thread": False},
            future=True,
        )

        @event.listens_for(_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def _sqlite_table_columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f'PRAGMA table_info("{table}")')).fetchall()
    return {r[1] for r in rows}


def _read_legacy_search_settings(conn) -> dict:
    cols = _sqlite_table_columns(conn, "app_settings")
    if "job_title" not in cols:
        return {"job_title": "", "location": "Bengaluru", "runs_per_day": 0}
    row = conn.execute(
        text("SELECT job_title, location, runs_per_day FROM app_settings WHERE id = 1")
    ).mappings().first()
    if not row:
        return {"job_title": "", "location": "Bengaluru", "runs_per_day": 0}
    n = row["runs_per_day"]
    if n is None:
        n = 0
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(5, n))
    loc = (row["location"] or "").strip() or "Bengaluru"
    return {
        "job_title": (row["job_title"] or "").strip(),
        "location": loc,
        "runs_per_day": n,
    }


def _migrate_sqlite_schema(engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
        if "scrape_runs" in tables:
            cols = _sqlite_table_columns(conn, "scrape_runs")
            if "filter_id" not in cols:
                conn.execute(text("ALTER TABLE scrape_runs ADD COLUMN filter_id INTEGER REFERENCES job_filters(id)"))
            if "scrape_target_limit" not in cols:
                conn.execute(
                    text("ALTER TABLE scrape_runs ADD COLUMN scrape_target_limit INTEGER NOT NULL DEFAULT 100")
                )
            if "llm_compare_total" not in cols:
                conn.execute(text("ALTER TABLE scrape_runs ADD COLUMN llm_compare_total INTEGER"))
            if "llm_compare_done" not in cols:
                conn.execute(text("ALTER TABLE scrape_runs ADD COLUMN llm_compare_done INTEGER NOT NULL DEFAULT 0"))
        if "job_filters" in tables:
            jf_cols = _sqlite_table_columns(conn, "job_filters")
            if "schedule_times_json" not in jf_cols:
                conn.execute(text("ALTER TABLE job_filters ADD COLUMN schedule_times_json TEXT"))
        if "ideal_job_requirements" in tables:
            ijr_cols = _sqlite_table_columns(conn, "ideal_job_requirements")
            if "notify_threshold" not in ijr_cols:
                conn.execute(
                    text("ALTER TABLE ideal_job_requirements ADD COLUMN notify_threshold INTEGER NOT NULL DEFAULT 60")
                )
            if "notify_email" not in ijr_cols:
                conn.execute(text("ALTER TABLE ideal_job_requirements ADD COLUMN notify_email VARCHAR(512)"))
        if "system_prompt_versions" not in tables:
            conn.execute(
                text(
                    "CREATE TABLE system_prompt_versions ("
                    "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
                    "prompt TEXT NOT NULL, "
                    "created_at DATETIME NOT NULL"
                    ")"
                )
            )


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_schema(engine)

    with session_scope() as session:
        if session.get(AppSettings, 1) is None:
            session.add(AppSettings(id=1))

        n_filters = session.scalar(select(func.count()).select_from(JobFilter)) or 0
        if n_filters == 0:
            with engine.connect() as conn:
                legacy = _read_legacy_search_settings(conn)
            session.add(
                JobFilter(
                    id=1,
                    name="",
                    job_title=legacy["job_title"],
                    location=legacy["location"],
                    runs_per_day=legacy["runs_per_day"],
                )
            )
            session.flush()

        # Orphan runs: filter row was removed (e.g. manual DB edit) while FK was off.
        session.execute(
            text(
                "UPDATE scrape_runs SET filter_id = NULL "
                "WHERE filter_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM job_filters jf WHERE jf.id = scrape_runs.filter_id)"
            )
        )
        anchor_id = session.scalar(select(JobFilter.id).order_by(JobFilter.id).limit(1))
        if anchor_id is not None:
            session.execute(
                text("UPDATE scrape_runs SET filter_id = :aid WHERE filter_id IS NULL"),
                {"aid": anchor_id},
            )


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def new_session() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def get_session() -> Generator[Session, None, None]:
    session = new_session()
    try:
        yield session
    finally:
        session.close()
