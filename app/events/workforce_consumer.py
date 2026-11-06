"""
app/events/workforce_consumer.py
─────────────────────────────────
SQS consumer for the workforce queue (runs in its own worker process, never in the API).

Messages handled
────────────────
* ``workforce.working_hours.changed`` / ``workforce.leave.changed`` /
  ``workforce.extra_hours.changed`` / ``workforce.therapist.changed``
      → appended to ``wf_schedule_change_log`` (idempotent by ``event_id``).
* ``workforce.day_close.requested``  (EventBridge Scheduler, daily 01:00 Asia/Kuwait)
      → runs the daily job.  Naturally idempotent, so no event-id dedupe is applied.

A message is deleted only after successful processing.  Failures are left on the queue and
retried after the visibility timeout; the queue's redrive policy moves poison messages to
the DLQ.  Unknown / malformed messages are deleted (logged) so they never block the queue.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.core.logging import get_logger
from app.models.workforce import ProcessedEvent
from app.services.workforce import service
from app.services.workforce.day_close import KUWAIT_TZ, parse_dt

logger = get_logger(__name__)

CHANGE_EVENTS = frozenset(
    {
        "workforce.working_hours.changed",
        "workforce.leave.changed",
        "workforce.extra_hours.changed",
        "workforce.therapist.changed",
    }
)
DAY_CLOSE_EVENT = "workforce.day_close.requested"


async def handle_message(envelope: dict[str, Any]) -> None:
    """Process one decoded envelope. Raises on failure (message will be retried)."""
    event_type = envelope.get("event_type")
    factory = get_session_factory()

    if event_type in CHANGE_EVENTS:
        async with factory() as db:
            async with db.begin():
                claimed = await db.execute(
                    pg_insert(ProcessedEvent)
                    .values(event_id=envelope["event_id"], event_type=event_type)
                    .on_conflict_do_nothing()
                    .returning(ProcessedEvent.event_id)
                )
                if claimed.first() is None:
                    logger.info("workforce_event_duplicate", event_id=str(envelope["event_id"]))
                    return
                await service.record_change(db, envelope)
        return

    if event_type == DAY_CLOSE_EVENT:
        if not get_settings().WORKFORCE_DAILY_JOB_ENABLED:
            logger.info("workforce_daily_job_disabled", reason="WORKFORCE_DAILY_JOB_ENABLED=False")
            return
        data = envelope.get("data") or {}
        if data.get("date"):
            target = datetime.fromisoformat(str(data["date"])).date()
        else:
            ref = envelope.get("occurred_at")
            target = service.yesterday_local(parse_dt(ref) if ref else None)
        await service.run_daily_job(factory, target)
        return

    logger.warning("workforce_event_unknown", event_type=event_type)


class WorkforceConsumer:
    """Long-polling SQS loop with bounded concurrency."""

    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        s = get_settings()
        self._s = s
        kwargs: dict[str, Any] = {
            "region_name": s.AWS_REGION,
            "config": Config(retries={"max_attempts": 5, "mode": "standard"}),
        }
        if s.AWS_ACCESS_KEY_ID and s.AWS_SECRET_ACCESS_KEY:
            kwargs["aws_access_key_id"] = s.AWS_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = s.AWS_SECRET_ACCESS_KEY
        self._sqs = boto3.client("sqs", **kwargs)
        self._queue = s.AWS_SQS_WORKFORCE_QUEUE_URL
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def _process(self, sem: asyncio.Semaphore, msg: dict[str, Any]) -> None:
        async with sem:
            handle = msg["ReceiptHandle"]
            try:
                envelope = json.loads(msg["Body"])
                if not isinstance(envelope, dict) or "event_type" not in envelope:
                    raise ValueError("missing event_type")
                if envelope["event_type"] in CHANGE_EVENTS and "event_id" not in envelope:
                    raise ValueError("missing event_id")
            except (ValueError, TypeError) as exc:
                logger.error("workforce_message_invalid", error=str(exc), body=str(msg.get("Body"))[:300])
                await asyncio.to_thread(self._sqs.delete_message, QueueUrl=self._queue, ReceiptHandle=handle)
                return
            try:
                await handle_message(envelope)
            except Exception as exc:  # noqa: BLE001 - retried via visibility timeout → DLQ
                logger.error("workforce_message_failed", event_type=envelope.get("event_type"), error=str(exc))
                return
            await asyncio.to_thread(self._sqs.delete_message, QueueUrl=self._queue, ReceiptHandle=handle)

    async def run(self) -> None:
        s = self._s
        if not s.WORKFORCE_CONSUMER_ENABLED:
            logger.warning("workforce_consumer_disabled", reason="WORKFORCE_CONSUMER_ENABLED=False")
            return
        if not self._queue:
            logger.warning("workforce_consumer_disabled", reason="AWS_SQS_WORKFORCE_QUEUE_URL not set")
            return
        sem = asyncio.Semaphore(s.WORKFORCE_SQS_MAX_CONCURRENCY)
        logger.info("workforce_consumer_started", queue=self._queue)
        while not self._stop.is_set():
            try:
                resp = await asyncio.to_thread(
                    self._sqs.receive_message,
                    QueueUrl=self._queue,
                    MaxNumberOfMessages=s.WORKFORCE_SQS_MAX_MESSAGES,
                    WaitTimeSeconds=s.WORKFORCE_SQS_WAIT_SECONDS,
                    VisibilityTimeout=s.WORKFORCE_SQS_VISIBILITY_TIMEOUT,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("workforce_receive_failed", error=str(exc))
                await asyncio.sleep(5)
                continue
            messages = resp.get("Messages", [])
            if messages:
                await asyncio.gather(*(self._process(sem, m) for m in messages))
        logger.info("workforce_consumer_stopped")


__all__ = ["WorkforceConsumer", "handle_message", "KUWAIT_TZ"]
