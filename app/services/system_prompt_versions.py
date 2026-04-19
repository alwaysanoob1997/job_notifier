from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.default_system_prompt import DEFAULT_SYSTEM_PROMPT
from app.models import SystemPromptVersion


def get_active_system_prompt_version(session: Session) -> SystemPromptVersion | None:
    return session.scalar(
        select(SystemPromptVersion)
        .order_by(SystemPromptVersion.created_at.desc(), SystemPromptVersion.id.desc())
        .limit(1)
    )


def effective_system_prompt_for_scoring(session: Session) -> str:
    row = get_active_system_prompt_version(session)
    if row is None:
        return DEFAULT_SYSTEM_PROMPT
    t = (row.prompt or "").strip()
    return t if t else DEFAULT_SYSTEM_PROMPT


def delete_all_system_prompt_versions(session: Session) -> None:
    session.execute(delete(SystemPromptVersion))
