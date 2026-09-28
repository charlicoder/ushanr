"""
app/api/v1/endpoints/analytic.py — Analytic Plans and Accounts
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.analytic import AnalyticAccount, AnalyticPlan

router = APIRouter()


def _plan_to_dict(p: AnalyticPlan) -> dict:
    return {
        "id": str(p.id),
        "company_id": str(p.company_id),
        "name": p.name,
        "code": p.code,
        "description": p.description,
        "is_active": p.is_active,
        "sequence": p.sequence,
        "default_applicability": float(p.default_applicability),
        "created_at": p.created_at.isoformat(),
    }


def _account_to_dict(a: AnalyticAccount) -> dict:
    return {
        "id": str(a.id),
        "company_id": str(a.company_id),
        "plan_id": str(a.plan_id),
        "parent_id": str(a.parent_id) if a.parent_id else None,
        "code": a.code,
        "name": a.name,
        "description": a.description,
        "is_active": a.is_active,
        "created_at": a.created_at.isoformat(),
    }


@router.get("/plans/", summary="List analytic plans")
async def list_plans(company_id: UUID = Query(...), db: AsyncSession = Depends(get_db)) -> dict:
    rows = (await db.execute(select(AnalyticPlan).where(AnalyticPlan.company_id == company_id).order_by(AnalyticPlan.sequence))).scalars().all()
    return {"success": True, "data": {"items": [_plan_to_dict(p) for p in rows]}}


@router.post("/plans/", status_code=status.HTTP_201_CREATED, summary="Create analytic plan")
async def create_plan(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    plan = AnalyticPlan(
        company_id=UUID(str(payload["company_id"])),
        name=payload["name"],
        code=payload.get("code"),
        description=payload.get("description"),
        sequence=payload.get("sequence", 10),
        default_applicability=float(payload.get("default_applicability", 100)),
    )
    db.add(plan)
    await db.flush()
    await db.refresh(plan)
    return {"success": True, "data": _plan_to_dict(plan)}


@router.get("/plans/{plan_id}/", summary="Get analytic plan")
async def get_plan(plan_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(AnalyticPlan).where(AnalyticPlan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Analytic plan not found")
    return {"success": True, "data": _plan_to_dict(plan)}


@router.put("/plans/{plan_id}/", summary="Update analytic plan")
async def update_plan(plan_id: UUID, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(AnalyticPlan).where(AnalyticPlan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Analytic plan not found")
    for f in ["name", "description", "is_active", "sequence"]:
        if f in payload:
            setattr(plan, f, payload[f])
    db.add(plan)
    await db.flush()
    await db.refresh(plan)
    return {"success": True, "data": _plan_to_dict(plan)}


@router.get("/accounts/", summary="List analytic accounts")
async def list_analytic_accounts(
    company_id: UUID = Query(...),
    plan_id: UUID | None = Query(None),
    is_active: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = select(AnalyticAccount).where(AnalyticAccount.company_id == company_id)
    if plan_id:
        q = q.where(AnalyticAccount.plan_id == plan_id)
    if is_active is not None:
        q = q.where(AnalyticAccount.is_active == is_active)
    rows = (await db.execute(q.order_by(AnalyticAccount.name))).scalars().all()
    return {"success": True, "data": {"items": [_account_to_dict(a) for a in rows]}}


@router.post("/accounts/", status_code=status.HTTP_201_CREATED, summary="Create analytic account")
async def create_analytic_account(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    account = AnalyticAccount(
        company_id=UUID(str(payload["company_id"])),
        plan_id=UUID(str(payload["plan_id"])),
        parent_id=UUID(str(payload["parent_id"])) if payload.get("parent_id") else None,
        code=payload.get("code"),
        name=payload["name"],
        description=payload.get("description"),
    )
    db.add(account)
    await db.flush()
    await db.refresh(account)
    return {"success": True, "data": _account_to_dict(account)}


@router.get("/accounts/{account_id}/", summary="Get analytic account")
async def get_analytic_account(account_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(AnalyticAccount).where(AnalyticAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Analytic account not found")
    return {"success": True, "data": _account_to_dict(account)}


@router.put("/accounts/{account_id}/", summary="Update analytic account")
async def update_analytic_account(account_id: UUID, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(AnalyticAccount).where(AnalyticAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Analytic account not found")
    for f in ["name", "description", "is_active", "code"]:
        if f in payload:
            setattr(account, f, payload[f])
    db.add(account)
    await db.flush()
    await db.refresh(account)
    return {"success": True, "data": _account_to_dict(account)}
