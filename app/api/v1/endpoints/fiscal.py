"""
app/api/v1/endpoints/fiscal.py
────────────────────────────────
Fiscal year and accounting period endpoints.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.fiscal import AccountingPeriod, FiscalYear, FiscalYearState, PeriodState

router = APIRouter()


def _fy_to_dict(fy: FiscalYear) -> dict:
    return {
        "id": str(fy.id),
        "company_id": str(fy.company_id),
        "name": fy.name,
        "date_from": fy.date_from.isoformat(),
        "date_to": fy.date_to.isoformat(),
        "state": fy.state,
        "is_active": fy.is_active,
        "created_at": fy.created_at.isoformat(),
        "updated_at": fy.updated_at.isoformat(),
    }


def _period_to_dict(p: AccountingPeriod) -> dict:
    return {
        "id": str(p.id),
        "company_id": str(p.company_id),
        "fiscal_year_id": str(p.fiscal_year_id),
        "name": p.name,
        "date_from": p.date_from.isoformat(),
        "date_to": p.date_to.isoformat(),
        "state": p.state,
        "created_at": p.created_at.isoformat(),
    }


# ── Fiscal Years ──────────────────────────────────────────────────────────────

@router.get("/years/", summary="List fiscal years")
async def list_fiscal_years(
    company_id: UUID = Query(...),
    state: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = select(FiscalYear).where(FiscalYear.company_id == company_id)
    if state:
        q = q.where(FiscalYear.state == state)
    rows = (await db.execute(q.order_by(FiscalYear.date_from.desc()))).scalars().all()
    return {"success": True, "data": {"items": [_fy_to_dict(f) for f in rows]}}


@router.post("/years/", status_code=status.HTTP_201_CREATED, summary="Create fiscal year")
async def create_fiscal_year(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    fy = FiscalYear(
        company_id=UUID(str(payload["company_id"])),
        name=payload["name"],
        date_from=date.fromisoformat(payload["date_from"]),
        date_to=date.fromisoformat(payload["date_to"]),
        state=FiscalYearState.OPEN.value,
    )
    db.add(fy)
    await db.flush()
    await db.refresh(fy)
    return {"success": True, "data": _fy_to_dict(fy)}


@router.get("/years/{year_id}/", summary="Get fiscal year")
async def get_fiscal_year(year_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(FiscalYear).where(FiscalYear.id == year_id))
    fy = result.scalar_one_or_none()
    if not fy:
        raise HTTPException(status_code=404, detail="Fiscal year not found")
    return {"success": True, "data": _fy_to_dict(fy)}


@router.put("/years/{year_id}/", summary="Update fiscal year")
async def update_fiscal_year(year_id: UUID, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(FiscalYear).where(FiscalYear.id == year_id))
    fy = result.scalar_one_or_none()
    if not fy:
        raise HTTPException(status_code=404, detail="Fiscal year not found")
    if fy.state == FiscalYearState.LOCKED.value:
        raise HTTPException(status_code=409, detail="Cannot modify a locked fiscal year")
    for f in ["name", "is_active"]:
        if f in payload:
            setattr(fy, f, payload[f])
    db.add(fy)
    await db.flush()
    await db.refresh(fy)
    return {"success": True, "data": _fy_to_dict(fy)}


@router.post("/years/{year_id}/close/", summary="Close fiscal year")
async def close_fiscal_year(year_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(FiscalYear).where(FiscalYear.id == year_id))
    fy = result.scalar_one_or_none()
    if not fy:
        raise HTTPException(status_code=404, detail="Fiscal year not found")
    if fy.state != FiscalYearState.OPEN.value:
        raise HTTPException(status_code=409, detail=f"Fiscal year is already {fy.state}")
    fy.state = FiscalYearState.CLOSED.value
    # Also lock all open periods in this year
    periods_result = await db.execute(
        select(AccountingPeriod).where(
            AccountingPeriod.fiscal_year_id == year_id,
            AccountingPeriod.state == PeriodState.OPEN.value,
        )
    )
    for period in periods_result.scalars().all():
        period.state = PeriodState.CLOSED.value
        db.add(period)
    db.add(fy)
    await db.flush()
    return {"success": True, "data": _fy_to_dict(fy)}


# ── Accounting Periods ────────────────────────────────────────────────────────

@router.get("/periods/", summary="List accounting periods")
async def list_periods(
    company_id: UUID = Query(...),
    fiscal_year_id: UUID | None = Query(None),
    state: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = select(AccountingPeriod).where(AccountingPeriod.company_id == company_id)
    if fiscal_year_id:
        q = q.where(AccountingPeriod.fiscal_year_id == fiscal_year_id)
    if state:
        q = q.where(AccountingPeriod.state == state)
    rows = (await db.execute(q.order_by(AccountingPeriod.date_from))).scalars().all()
    return {"success": True, "data": {"items": [_period_to_dict(p) for p in rows]}}


@router.post("/periods/", status_code=status.HTTP_201_CREATED, summary="Create accounting period")
async def create_period(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    period = AccountingPeriod(
        company_id=UUID(str(payload["company_id"])),
        fiscal_year_id=UUID(str(payload["fiscal_year_id"])),
        name=payload["name"],
        date_from=date.fromisoformat(payload["date_from"]),
        date_to=date.fromisoformat(payload["date_to"]),
        state=PeriodState.OPEN.value,
    )
    db.add(period)
    await db.flush()
    await db.refresh(period)
    return {"success": True, "data": _period_to_dict(period)}


@router.get("/periods/{period_id}/", summary="Get accounting period")
async def get_period(period_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(AccountingPeriod).where(AccountingPeriod.id == period_id))
    period = result.scalar_one_or_none()
    if not period:
        raise HTTPException(status_code=404, detail="Period not found")
    return {"success": True, "data": _period_to_dict(period)}


@router.patch("/periods/{period_id}/state/", summary="Lock or close accounting period")
async def update_period_state(period_id: UUID, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(AccountingPeriod).where(AccountingPeriod.id == period_id))
    period = result.scalar_one_or_none()
    if not period:
        raise HTTPException(status_code=404, detail="Period not found")
    new_state = payload.get("state")
    if new_state not in [PeriodState.OPEN.value, PeriodState.CLOSED.value, PeriodState.LOCKED.value]:
        raise HTTPException(status_code=422, detail=f"Invalid state: {new_state}")
    period.state = new_state
    db.add(period)
    await db.flush()
    return {"success": True, "data": _period_to_dict(period)}
