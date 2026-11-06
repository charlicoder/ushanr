"""
app/api/v1/endpoints/workforce.py
──────────────────────────────────
Read-only reporting API over the workforce history tables, plus a manual recompute hook.

All list endpoints cap the date range at ``MAX_RANGE_DAYS`` and are served from small,
indexed per-day tables (``wf_therapist_daily_summary`` ≈ therapists × days).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.internal import _verify_internal_key
from app.core.database import get_db, get_session_factory
from app.core.security import require_employee_user
from app.models.workforce import (
    ScheduleChangeLog,
    TherapistActivitySegment,
    TherapistDailyRoster,
    TherapistDailySummary,
)
from app.services.workforce import service
from app.services.workforce.day_close import minute_to_dt
router = APIRouter()

MAX_RANGE_DAYS = 92


def _range(date_from: date, date_to: date) -> tuple[date, date]:
    if date_to < date_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "`to` must be on/after `from`.")
    if (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Range is limited to {MAX_RANGE_DAYS} days.")
    return date_from, date_to


def _summary_dict(r: TherapistDailySummary) -> dict[str, Any]:
    return {
        "date": r.local_date.isoformat(),
        "therapist_id": str(r.therapist_id),
        "therapist_name": r.therapist_name,
        "branch_id": str(r.branch_id) if r.branch_id else None,
        "scheduled_minutes": r.scheduled_minutes,
        "break_minutes": r.break_minutes,
        "leave_minutes": r.leave_minutes,
        "extra_minutes": r.extra_minutes,
        "available_minutes": r.available_minutes,
        "worked_minutes": r.worked_minutes,
        "idle_minutes": r.idle_minutes,
        "overtime_minutes": r.overtime_minutes,
        "sessions_completed": r.sessions_completed,
        "utilization_pct": float(r.utilization_pct),
        "is_day_off": r.is_day_off,
        "on_leave": r.on_leave,
    }


def _roster_dict(r: TherapistDailyRoster) -> dict[str, Any]:
    t = lambda v: v.isoformat() if v else None  # noqa: E731
    return {
        "is_day_off": r.is_day_off,
        "shift_start": t(r.shift_start),
        "shift_end": t(r.shift_end),
        "break_start": t(r.break_start),
        "break_end": t(r.break_end),
        "leaves": r.leaves,
        "extra_hours": r.extra_segments,
        "therapist_status": r.therapist_status,
        "data_quality": r.data_quality,
    }


@router.get("/therapists/{therapist_id}/daily/", summary="Daily roster + summary per day")
async def therapist_daily(
    therapist_id: UUID,
    date_from: date = Query(..., alias="from"),
    date_to: date = Query(..., alias="to"),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_employee_user),
) -> dict[str, Any]:
    date_from, date_to = _range(date_from, date_to)
    sums = (
        await db.execute(
            select(TherapistDailySummary)
            .where(
                TherapistDailySummary.therapist_id == therapist_id,
                TherapistDailySummary.local_date.between(date_from, date_to),
            )
            .order_by(TherapistDailySummary.local_date)
        )
    ).scalars().all()
    rosters = {
        r.local_date: r
        for r in (
            await db.execute(
                select(TherapistDailyRoster).where(
                    TherapistDailyRoster.therapist_id == therapist_id,
                    TherapistDailyRoster.local_date.between(date_from, date_to),
                )
            )
        ).scalars()
    }
    days = []
    for s in sums:
        item = _summary_dict(s)
        r = rosters.get(s.local_date)
        item["roster"] = _roster_dict(r) if r else None
        days.append(item)
    return {"success": True, "therapist_id": str(therapist_id), "from": date_from, "to": date_to, "days": days}


@router.get("/therapists/{therapist_id}/timeline/", summary="Detailed timeline of one day")
async def therapist_timeline(
    therapist_id: UUID,
    day: date = Query(..., alias="date"),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_employee_user),
) -> dict[str, Any]:
    segs = (
        await db.execute(
            select(TherapistActivitySegment)
            .where(
                TherapistActivitySegment.therapist_id == therapist_id,
                TherapistActivitySegment.local_date == day,
            )
            .order_by(TherapistActivitySegment.start_at, TherapistActivitySegment.end_at)
        )
    ).scalars().all()
    summary = (
        await db.execute(
            select(TherapistDailySummary).where(
                TherapistDailySummary.therapist_id == therapist_id, TherapistDailySummary.local_date == day
            )
        )
    ).scalar_one_or_none()
    return {
        "success": True,
        "date": day,
        "summary": _summary_dict(summary) if summary else None,
        "segments": [
            {
                "type": s.segment_type,
                "start": s.start_at,
                "end": s.end_at,
                "minutes": s.minutes,
                "is_overtime": s.is_overtime,
                "booking_id": s.booking_id,
                "booking_number": s.booking_number,
                "session_id": s.session_id,
                "service_id": s.service_id,
                "service_name": s.service_name,
                "customer_id": s.customer_id,
                "detail": s.detail,
            }
            for s in segs
        ],
    }


@router.get("/therapists/{therapist_id}/schedule-history/", summary="Every schedule edit (audit trail)")
async def therapist_schedule_history(
    therapist_id: UUID,
    date_from: date = Query(..., alias="from"),
    date_to: date = Query(..., alias="to"),
    entity_type: str | None = Query(None, description="working_hours|leave|extra_hours|therapist"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_employee_user),
) -> dict[str, Any]:
    date_from, date_to = _range(date_from, date_to)
    start = minute_to_dt(date_from, 0)
    end = minute_to_dt(date_to + timedelta(days=1), 0)
    q = select(ScheduleChangeLog).where(
        ScheduleChangeLog.therapist_id == therapist_id,
        ScheduleChangeLog.occurred_at >= start,
        ScheduleChangeLog.occurred_at < end,
    )
    if entity_type:
        q = q.where(ScheduleChangeLog.entity_type == entity_type)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (
        await db.execute(q.order_by(ScheduleChangeLog.occurred_at.desc()).limit(limit).offset(offset))
    ).scalars().all()
    return {
        "success": True,
        "total": total,
        "items": [
            {
                "occurred_at": r.occurred_at,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "action": r.action,
                "before": r.before,
                "after": r.after,
                "changed_by": r.changed_by,
            }
            for r in rows
        ],
    }


@router.get("/summary/", summary="Aggregated hours by day / week / month / therapist")
async def workforce_summary(
    date_from: date = Query(..., alias="from"),
    date_to: date = Query(..., alias="to"),
    branch_id: UUID | None = None,
    therapist_id: UUID | None = None,
    group_by: Literal["day", "week", "month", "therapist"] = "day",
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_employee_user),
) -> dict[str, Any]:
    date_from, date_to = _range(date_from, date_to)
    S = TherapistDailySummary
    if group_by == "therapist":
        keys = [S.therapist_id, func.max(S.therapist_name).label("name")]
        group_cols = [S.therapist_id]
    else:
        bucket = S.local_date if group_by == "day" else func.date_trunc(group_by, S.local_date)
        keys = [bucket.label("period")]
        group_cols = [bucket]
    q = (
        select(
            *keys,
            func.sum(S.scheduled_minutes).label("scheduled"),
            func.sum(S.break_minutes).label("break"),
            func.sum(S.leave_minutes).label("leave"),
            func.sum(S.extra_minutes).label("extra"),
            func.sum(S.available_minutes).label("available"),
            func.sum(S.worked_minutes).label("worked"),
            func.sum(S.idle_minutes).label("idle"),
            func.sum(S.overtime_minutes).label("overtime"),
            func.sum(S.sessions_completed).label("sessions"),
        )
        .where(S.local_date.between(date_from, date_to))
        .group_by(*group_cols)
        .order_by(*group_cols)
    )
    if branch_id:
        q = q.where(S.branch_id == branch_id)
    if therapist_id:
        q = q.where(S.therapist_id == therapist_id)
    rows = (await db.execute(q)).all()
    out = []
    for r in rows:
        m = r._mapping
        avail = int(m["available"] or 0)
        worked = int(m["worked"] or 0)
        item = {
            "scheduled_minutes": int(m["scheduled"] or 0),
            "break_minutes": int(m["break"] or 0),
            "leave_minutes": int(m["leave"] or 0),
            "extra_minutes": int(m["extra"] or 0),
            "available_minutes": avail,
            "worked_minutes": worked,
            "idle_minutes": int(m["idle"] or 0),
            "overtime_minutes": int(m["overtime"] or 0),
            "sessions_completed": int(m["sessions"] or 0),
            "worked_ratio_pct": round(worked * 100 / avail, 2) if avail else 0.0,
        }
        if group_by == "therapist":
            item.update(therapist_id=str(m["therapist_id"]), therapist_name=m["name"])
        else:
            p = m["period"]
            item["period"] = p.date().isoformat() if hasattr(p, "date") else p.isoformat()
        out.append(item)
    return {"success": True, "group_by": group_by, "from": date_from, "to": date_to, "items": out}


@router.get("/utilization/", summary="Therapist utilization leaderboard (worked vs idle)")
async def utilization(
    date_from: date = Query(..., alias="from"),
    date_to: date = Query(..., alias="to"),
    branch_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(require_employee_user),
) -> dict[str, Any]:
    data = await workforce_summary(
        date_from, date_to, branch_id, None, "therapist", db, claims  # type: ignore[arg-type]
    )
    data["items"].sort(key=lambda i: i["worked_ratio_pct"], reverse=True)
    return data


@router.post("/internal/recompute/", summary="Manually (re)compute a day (internal)")
async def recompute(
    day: date = Query(..., alias="date"),
    refresh_roster: bool = False,
    _: None = Depends(_verify_internal_key),
) -> dict[str, Any]:
    async with get_session_factory()() as db:
        async with db.begin():
            stats = await service.close_day(db, day, refresh_roster=refresh_roster)
    return {"success": True, "date": day, **stats}
