"""
app/services/workforce/service.py
──────────────────────────────────
Database orchestration of the daily workforce job and the change-log ingestion.

``close_day`` is idempotent: re-running a date replaces that date's sessions, segments and
summary inside one transaction (guarded by a per-date advisory lock).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.workforce import (
    CompletedSession,
    ScheduleChangeLog,
    TherapistActivitySegment,
    TherapistDailyRoster,
    TherapistDailySummary,
)
from app.services.workforce import sources
from app.services.workforce.day_close import (
    KUWAIT_TZ,
    DayResult,
    RosterInput,
    SessionInput,
    compute_day,
    minute_to_dt,
    parse_dt,
    parse_time,
)

logger = get_logger(__name__)


def _uuid(v: Any) -> uuid.UUID | None:
    return None if v in (None, "") else uuid.UUID(str(v))


def yesterday_local(now: datetime | None = None) -> date:
    now = (now or datetime.now(KUWAIT_TZ)).astimezone(KUWAIT_TZ)
    return now.date() - timedelta(days=1)


# ── change log ────────────────────────────────────────────────────────────────


async def record_change(db: AsyncSession, envelope: dict[str, Any]) -> bool:
    """Persist one ``workforce.*.changed`` event. Returns False if it was a duplicate."""
    data = envelope.get("data") or {}
    stmt = (
        pg_insert(ScheduleChangeLog)
        .values(
            id=uuid.uuid4(),
            event_id=uuid.UUID(str(envelope["event_id"])),
            event_type=envelope["event_type"],
            entity_type=data.get("entity_type") or envelope["event_type"].split(".")[1],
            entity_id=_uuid(data.get("entity_id")),
            employee_id=_uuid(data.get("employee_id")),
            therapist_id=_uuid(data.get("therapist_id")),
            branch_id=_uuid(data.get("branch_id")),
            action=data.get("action") or "updated",
            before=data.get("before"),
            after=data.get("after"),
            changed_by=data.get("changed_by"),
            occurred_at=parse_dt(envelope.get("occurred_at") or datetime.now(KUWAIT_TZ)),
        )
        .on_conflict_do_nothing(constraint="uq_wf_change_log_event_id")
        .returning(ScheduleChangeLog.id)
    )
    return (await db.execute(stmt)).first() is not None


# ── daily job ─────────────────────────────────────────────────────────────────


async def _lock_day(db: AsyncSession, day: date) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": 7_300_000 + day.toordinal()})


def _roster_row_values(r: RosterInput, res: DayResult) -> dict[str, Any]:
    return dict(
        therapist_id=uuid.UUID(r.therapist_id),
        employee_id=_uuid(r.employee_id),
        branch_id=_uuid(r.branch_id),
        therapist_name=r.therapist_name,
        therapist_status=r.therapist_status,
        local_date=r.local_date,
        is_day_off=r.is_day_off,
        shift_start=r.shift_start,
        shift_end=r.shift_end,
        break_start=r.break_start,
        break_end=r.break_end,
        break_duration_config=r.break_duration_config,
        working_hours_id=_uuid(r.working_hours_id),
        leaves=r.leaves,
        extra_segments=r.extra_segments,
        scheduled_minutes=res.scheduled_minutes,
        break_minutes=res.break_minutes,
        leave_minutes=res.leave_minutes,
        extra_minutes=res.extra_minutes,
        available_minutes=res.available_minutes,
        data_quality=r.data_quality,
    )


def _roster_from_row(row: TherapistDailyRoster) -> RosterInput:
    return RosterInput(
        therapist_id=str(row.therapist_id),
        local_date=row.local_date,
        employee_id=_s(row.employee_id),
        branch_id=_s(row.branch_id),
        therapist_name=row.therapist_name,
        therapist_status=row.therapist_status,
        is_day_off=row.is_day_off,
        shift_start=row.shift_start,
        shift_end=row.shift_end,
        break_start=row.break_start,
        break_end=row.break_end,
        break_duration_config=row.break_duration_config,
        working_hours_id=_s(row.working_hours_id),
        leaves=list(row.leaves or []),
        extra_segments=list(row.extra_segments or []),
        data_quality=row.data_quality,
    )


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


async def close_day(db: AsyncSession, day: date, *, refresh_roster: bool) -> dict[str, int]:
    """Compute and store the full history of ``day``.

    ``refresh_roster=False`` re-uses the stored roster (history must not be rewritten with
    today's schedule) and only re-pulls completed sessions.
    """
    await _lock_day(db, day)

    existing = (
        await db.execute(select(TherapistDailyRoster).where(TherapistDailyRoster.local_date == day))
    ).scalars().all()

    if refresh_roster or not existing:
        payload = await sources.fetch_roster(day)
        rosters = {str(p["therapist_id"]): RosterInput.from_payload(p, day) for p in payload}
    else:
        rosters = {str(r.therapist_id): _roster_from_row(r) for r in existing}

    session_payload = await sources.fetch_completed_sessions(day)
    sessions = [SessionInput.from_payload(p) for p in session_payload]

    by_therapist: dict[str, list[SessionInput]] = {}
    for s in sessions:
        by_therapist.setdefault(s.therapist_id, []).append(s)
        if s.therapist_id not in rosters:  # worked but absent from roster
            rosters[s.therapist_id] = RosterInput(
                therapist_id=s.therapist_id,
                local_date=day,
                branch_id=s.branch_id,
                is_day_off=True,
                data_quality="session_only",
            )

    # Replace the day's derived rows.
    await db.execute(delete(CompletedSession).where(CompletedSession.local_date == day))
    await db.execute(delete(TherapistActivitySegment).where(TherapistActivitySegment.local_date == day))

    session_rows = [
        dict(
            id=uuid.uuid4(),
            session_id=uuid.UUID(s.session_id),
            booking_id=uuid.UUID(s.booking_id),
            booking_number=s.booking_number,
            therapist_id=uuid.UUID(s.therapist_id),
            role=s.role,
            branch_id=_uuid(s.branch_id),
            customer_id=_uuid(s.customer_id),
            booking_type=s.booking_type,
            local_date=day,
            start_at=s.start,
            end_at=s.end,
            duration_minutes=int((s.end - s.start).total_seconds() // 60),
            services=s.services,
            extra_minutes=s.extra_minutes,
            addons_minutes=s.addons_minutes,
        )
        for s in sessions
    ]
    if session_rows:
        await db.execute(
            pg_insert(CompletedSession).values(session_rows).on_conflict_do_nothing(
                constraint="uq_wf_completed_session_therapist"
            )
        )

    segment_rows: list[dict[str, Any]] = []
    for tid, roster in rosters.items():
        res = compute_day(roster, by_therapist.get(tid, []))
        rv = _roster_row_values(roster, res)
        await db.execute(
            pg_insert(TherapistDailyRoster)
            .values(id=uuid.uuid4(), **rv)
            .on_conflict_do_update(
                constraint="uq_wf_roster_therapist_date",
                set_={**{k: v for k, v in rv.items() if k not in ("therapist_id", "local_date")},
                      "snapshot_at": datetime.now(KUWAIT_TZ)},
            )
        )
        sv = dict(
            therapist_id=rv["therapist_id"], branch_id=rv["branch_id"], therapist_name=roster.therapist_name,
            local_date=day, scheduled_minutes=res.scheduled_minutes, break_minutes=res.break_minutes,
            leave_minutes=res.leave_minutes, extra_minutes=res.extra_minutes,
            available_minutes=res.available_minutes, worked_minutes=res.worked_minutes,
            idle_minutes=res.idle_minutes, overtime_minutes=res.overtime_minutes,
            sessions_completed=res.sessions_completed, utilization_pct=res.utilization_pct,
            is_day_off=roster.is_day_off, on_leave=res.on_leave,
        )
        await db.execute(
            pg_insert(TherapistDailySummary)
            .values(id=uuid.uuid4(), **sv)
            .on_conflict_do_update(
                constraint="uq_wf_summary_therapist_date",
                set_={**{k: v for k, v in sv.items() if k not in ("therapist_id", "local_date")},
                      "computed_at": datetime.now(KUWAIT_TZ)},
            )
        )
        for seg in res.segments:
            sess = seg.session
            segment_rows.append(
                dict(
                    id=uuid.uuid4(),
                    therapist_id=rv["therapist_id"],
                    branch_id=rv["branch_id"],
                    local_date=day,
                    segment_type=seg.segment_type,
                    start_at=minute_to_dt(day, seg.start_min),
                    end_at=minute_to_dt(day, seg.end_min),
                    minutes=seg.minutes,
                    is_overtime=seg.is_overtime,
                    booking_id=_uuid(sess.booking_id) if sess else None,
                    booking_number=sess.booking_number if sess else None,
                    session_id=_uuid(sess.session_id) if sess else None,
                    service_id=_uuid(seg.service_id),
                    service_name=seg.service_name,
                    customer_id=_uuid(sess.customer_id) if sess else None,
                    detail=seg.detail,
                )
            )
    if segment_rows:
        await db.execute(pg_insert(TherapistActivitySegment).values(segment_rows))

    logger.info("workforce_day_closed", date=day.isoformat(), therapists=len(rosters), sessions=len(sessions))
    return {"therapists": len(rosters), "sessions": len(sessions), "segments": len(segment_rows)}


async def run_daily_job(session_factory, target: date | None = None) -> list[date]:
    """Process ``target`` (default yesterday) plus catch-up and re-check windows.

    * target                         → roster refreshed from ushauth (first run for the day)
    * last N days with no summary    → processed (roster fetched, nothing better exists)
    * last K days already processed  → completed sessions re-pulled, stored roster kept
    """
    s = get_settings()
    target = target or yesterday_local()
    processed: list[date] = []

    async def _run(d: date, refresh: bool) -> None:
        async with session_factory() as db:
            async with db.begin():
                await close_day(db, d, refresh_roster=refresh)
        processed.append(d)

    await _run(target, refresh=True)

    window = [target - timedelta(days=i) for i in range(1, s.WORKFORCE_DAY_CLOSE_CATCHUP_DAYS + 1)]
    async with session_factory() as db:
        done = set(
            (
                await db.execute(
                    select(TherapistDailySummary.local_date)
                    .where(TherapistDailySummary.local_date.in_(window))
                    .distinct()
                )
            ).scalars()
        )
    recheck = {target - timedelta(days=i) for i in range(1, s.WORKFORCE_DAY_CLOSE_RECHECK_DAYS + 1)}
    for d in sorted(window):
        if d not in done:
            await _run(d, refresh=True)
        elif d in recheck:
            await _run(d, refresh=False)
    return processed
