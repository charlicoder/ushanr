"""
app/services/workforce/sources.py
──────────────────────────────────
HTTP clients used by the daily job to pull one batched payload per source:

* ushauth      GET /api/v1/internal/workforce/roster/?date=YYYY-MM-DD
* ushbooknpay  GET /api/v1/internal/workforce/completed-sessions/?date=YYYY-MM-DD

Both calls are authenticated with the shared ``USHSPA-TOKEN`` header and are made once per
processed day (off-peak, from the worker process – never from the API process).
"""
from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class WorkforceSourceError(RuntimeError):
    """A source service could not be reached or returned an error."""


async def _get_json(urls: list[str], path: str, params: dict[str, Any], service: str) -> dict[str, Any]:
    settings = get_settings()
    headers = {"USHSPA-TOKEN": settings.USHSPA_TOKEN, "Accept": "application/json"}
    last: Exception | None = None
    async with httpx.AsyncClient(timeout=settings.WORKFORCE_SOURCE_TIMEOUT) as client:
        for base in urls:
            try:
                resp = await client.get(f"{base}{path}", params=params, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                last = exc
                logger.warning("workforce_source_failed", service=service, base=base, error=str(exc))
    raise WorkforceSourceError(f"{service} unreachable: {last}")


async def fetch_roster(day: date) -> list[dict[str, Any]]:
    """All therapists' resolved schedule for ``day`` from ushauth."""
    data = await _get_json(
        get_settings().ushauth_urls,
        "/api/v1/internal/workforce/roster/",
        {"date": day.isoformat()},
        "ushauth",
    )
    return list(data.get("therapists") or data.get("data", {}).get("therapists") or [])


async def fetch_completed_sessions(day: date) -> list[dict[str, Any]]:
    """COMPLETED booking sessions (one entry per serving therapist) for ``day``."""
    data = await _get_json(
        get_settings().ushbooknpay_urls,
        "/api/v1/internal/workforce/completed-sessions/",
        {"date": day.isoformat()},
        "ushbooknpay",
    )
    return list(data.get("sessions") or data.get("data", {}).get("sessions") or [])
