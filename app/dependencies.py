from collections.abc import Generator

from sqlalchemy.orm import Session

from app.db import new_session


def get_db() -> Generator[Session, None, None]:
    db = new_session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
