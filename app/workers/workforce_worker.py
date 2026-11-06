"""
app/workers/workforce_worker.py
────────────────────────────────
Entrypoint of the workforce worker (separate container from the API):

    python -m app.workers.workforce_worker              # consume the SQS queue
    python -m app.workers.workforce_worker --run-day 2026-11-05   # manual (re)compute one day
    python -m app.workers.workforce_worker --run-yesterday        # run the daily job now
"""
from __future__ import annotations

import argparse
import asyncio
import signal
from datetime import date

from app.core.config import get_settings
from app.core.database import dispose_engine, get_session_factory
from app.core.logging import configure_logging, get_logger
import app.models  # noqa: F401  (register models)

logger = get_logger(__name__)


async def _main(run_day: date | None, run_yesterday: bool) -> None:
    from app.events.workforce_consumer import WorkforceConsumer
    from app.services.workforce import service

    s = get_settings()
    configure_logging(log_level=s.LOG_LEVEL, is_development=s.is_development)
    try:
        if run_day or run_yesterday:
            done = await service.run_daily_job(get_session_factory(), run_day)
            logger.info("workforce_manual_run_done", days=[d.isoformat() for d in done])
            return
        consumer = WorkforceConsumer()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, consumer.stop)
        await consumer.run()
    finally:
        await dispose_engine()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run-day", type=date.fromisoformat)
    p.add_argument("--run-yesterday", action="store_true")
    a = p.parse_args()
    asyncio.run(_main(a.run_day, a.run_yesterday))


if __name__ == "__main__":
    main()
