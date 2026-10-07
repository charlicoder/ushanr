"""
app/utils/timezone.py
─────────────────────
Timezone conversion helpers for ushanr.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = ZoneInfo("Asia/Kuwait")


def to_local_tz(dt: datetime | None, tz: ZoneInfo = DEFAULT_TIMEZONE) -> datetime | None:
    """Normalise to the platform convention (Kuwait wall-clock labelled UTC). Stored values already follow it; naive values are labelled UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
