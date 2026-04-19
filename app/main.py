from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import ensure_data_dir
from app.db import init_db
from app.llm_score_db import init_llm_scores_db
from app.routers import api as api_router
from app.routers import pages as pages_router  # noqa: I001
from app.services.scheduler import (
    reconcile_missed_scheduled_runs,
    refresh_schedule,
    shutdown_scheduler,
    start_scheduler,
)

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_data_dir()
    init_db()
    init_llm_scores_db()
    start_scheduler()
    reconcile_missed_scheduled_runs()
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(title="LinkedIn Jobs Scraper", lifespan=lifespan)

_STATIC = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
app.include_router(pages_router.router)
app.include_router(api_router.router, prefix="/api")
