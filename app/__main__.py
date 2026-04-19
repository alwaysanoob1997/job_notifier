"""
Headless scrape once (cron / launchd / systemd).

  python -m app

Uses the same database under ~/LinkedInJobs/ as the web app.
"""

from __future__ import annotations

import logging

logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)


def main() -> None:
    from app.db import init_db
    from app.llm_score_db import init_llm_scores_db
    from app.services.scrape_runner import run_scheduled_once_sync

    init_db()
    init_llm_scores_db()
    rid = run_scheduled_once_sync()
    logger.info("CLI scrape finished run_id=%s", rid)


if __name__ == "__main__":
    main()
