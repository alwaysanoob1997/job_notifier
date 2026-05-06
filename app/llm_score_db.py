from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Integer, String, Text, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import ensure_data_dir, llm_scores_database_url

_engine = None
_SessionLocal = None


class LlmScoreBase(DeclarativeBase):
    pass


class JobLlmScore(LlmScoreBase):
    __tablename__ = "job_llm_scores"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")


def reset_llm_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_llm_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = llm_scores_database_url()
        if not os.environ.get("APP_LLM_SCORES_DB_URL"):
            ensure_data_dir()
        elif url.startswith("sqlite:///"):
            p = Path(url.replace("sqlite:///", "", 1))
            p.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            url,
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


def init_llm_scores_db() -> None:
    engine = get_llm_engine()
    LlmScoreBase.metadata.create_all(bind=engine)


@contextmanager
def session_scope_llm() -> Generator[Session, None, None]:
    get_llm_engine()
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


def fetch_scores_for_job_ids(job_ids: list[str]) -> dict[str, JobLlmScore]:
    """Return map job_id -> row for UI; missing keys mean no score yet."""
    if not job_ids:
        return {}
    with session_scope_llm() as session:
        rows = list(
            session.scalars(select(JobLlmScore).where(JobLlmScore.job_id.in_(job_ids)))
        )
        for r in rows:
            session.expunge(r)
    return {r.job_id: r for r in rows}
