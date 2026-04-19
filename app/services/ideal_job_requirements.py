from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import IdealJobRequirement


def get_active_requirement(session: Session) -> IdealJobRequirement | None:
    return session.scalar(
        select(IdealJobRequirement)
        .order_by(IdealJobRequirement.created_at.desc(), IdealJobRequirement.id.desc())
        .limit(1)
    )
