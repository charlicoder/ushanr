"""Consumer routing tests (no database / AWS)."""
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.events import workforce_consumer as wc
from app.services.workforce.day_close import KUWAIT_TZ
from app.services.workforce.service import yesterday_local
from datetime import datetime


def test_yesterday_local_uses_kuwait_date():
    # 2026-11-05 22:30 UTC is already 2026-11-06 01:30 in Kuwait -> yesterday = 11-05
    from datetime import timezone

    now = datetime(2026, 11, 5, 22, 30, tzinfo=timezone.utc)
    assert yesterday_local(datetime(2026, 11, 6, 1, 30, tzinfo=KUWAIT_TZ)) == date(2026, 11, 5)
    assert yesterday_local(now) == date(2026, 11, 5)


@pytest.mark.asyncio
async def test_day_close_message_skipped_when_disabled():
    env = {"event_type": wc.DAY_CLOSE_EVENT, "occurred_at": "2026-11-06T01:00:00+03:00", "data": {}}
    with patch.object(wc.service, "run_daily_job", AsyncMock(return_value=[])) as job:
        await wc.handle_message(env)
    job.assert_not_awaited()


@pytest.mark.asyncio
async def test_day_close_message_runs_daily_job_for_yesterday():
    from types import SimpleNamespace

    env = {"event_type": wc.DAY_CLOSE_EVENT, "occurred_at": "2026-11-06T01:00:00+03:00", "data": {}}
    with (
        patch.object(wc, "get_settings", return_value=SimpleNamespace(WORKFORCE_DAILY_JOB_ENABLED=True)),
        patch.object(wc.service, "run_daily_job", AsyncMock(return_value=[])) as job,
        patch.object(wc, "get_session_factory", return_value="factory"),
    ):
        await wc.handle_message(env)
    job.assert_awaited_once_with("factory", date(2026, 11, 5))


@pytest.mark.asyncio
async def test_day_close_message_with_explicit_date():
    from types import SimpleNamespace

    env = {"event_type": wc.DAY_CLOSE_EVENT, "data": {"date": "2026-10-01"}}
    with (
        patch.object(wc, "get_settings", return_value=SimpleNamespace(WORKFORCE_DAILY_JOB_ENABLED=True)),
        patch.object(wc.service, "run_daily_job", AsyncMock(return_value=[])) as job,
        patch.object(wc, "get_session_factory", return_value="f"),
    ):
        await wc.handle_message(env)
    job.assert_awaited_once_with("f", date(2026, 10, 1))


@pytest.mark.asyncio
async def test_unknown_event_is_ignored():
    await wc.handle_message({"event_type": "something.else"})
