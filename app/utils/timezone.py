"""
app/utils/timezone.py
─────────────────────
Timezone helpers for ushanr.

Standard used across all USH services:
  * Storage    : PostgreSQL ``timestamptz`` (absolute instants, real "now").
  * Business tz: ``Asia/Kuwait`` (UTC+03:00, no DST).
  * API output : ISO-8601 with explicit offset, rendered in Asia/Kuwait
                 (e.g. ``2026-10-10T10:00:00+03:00``) - see ``LocalDateTime``.
  * Naive input: interpreted as Asia/Kuwait wall-clock (never as UTC).
  * Business dates (invoice_date, entry_date ...) are Asia/Kuwait dates -
    use ``local_today()``, never ``date.today()`` (containers run in UTC).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import PlainSerializer

DEFAULT_TIMEZONE = ZoneInfo("Asia/Kuwait")


def to_local_tz(dt: datetime | None, tz: ZoneInfo = DEFAULT_TIMEZONE) -> datetime | None:
    """Return *dt* as an aware Asia/Kuwait datetime (naive values = Kuwait wall-clock)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def utc_now() -> datetime:
    """Current instant (timezone-aware UTC) - use for stored timestamps."""
    return datetime.now(timezone.utc)


def local_now(tz: ZoneInfo = DEFAULT_TIMEZONE) -> datetime:
    """Current instant expressed in Asia/Kuwait."""
    return datetime.now(tz)


def local_today(tz: ZoneInfo = DEFAULT_TIMEZONE) -> date:
    """Today's business date in Asia/Kuwait (containers run in UTC)."""
    return datetime.now(tz).date()


def _serialize_local(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        return dt  # type: ignore[return-value]
    return to_local_tz(dt).isoformat()


# JSON output: ISO-8601 with explicit +03:00 offset. Validation unchanged.
LocalDateTime = Annotated[datetime, PlainSerializer(_serialize_local, when_used="json")]
