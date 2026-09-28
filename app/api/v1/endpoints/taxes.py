"""
app/api/v1/endpoints/taxes.py — Tax and TaxGroup CRUD
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.tax import Tax, TaxGroup

router = APIRouter()


def _tax_group_to_dict(tg: TaxGroup) -> dict:
    return {
        "id": str(tg.id),
        "company_id": str(tg.company_id),
        "name": tg.name,
        "sequence": tg.sequence,
        "created_at": tg.created_at.isoformat(),
    }


def _tax_to_dict(t: Tax) -> dict:
    return {
        "id": str(t.id),
        "company_id": str(t.company_id),
        "name": t.name,
        "description": t.description,
        "tax_type": t.tax_type,
        "computation": t.computation,
        "amount": float(t.amount),
        "tax_account_id": str(t.tax_account_id) if t.tax_account_id else None,
        "tax_refund_account_id": str(t.tax_refund_account_id) if t.tax_refund_account_id else None,
        "group_id": str(t.group_id) if t.group_id else None,
        "is_active": t.is_active,
        "include_in_price": t.include_in_price,
        "sequence": t.sequence,
        "created_at": t.created_at.isoformat(),
    }


# ── Tax Groups ────────────────────────────────────────────────────────────────

@router.get("/groups/", summary="List tax groups")
async def list_tax_groups(company_id: UUID = Query(...), db: AsyncSession = Depends(get_db)) -> dict:
    rows = (await db.execute(select(TaxGroup).where(TaxGroup.company_id == company_id).order_by(TaxGroup.sequence))).scalars().all()
    return {"success": True, "data": {"items": [_tax_group_to_dict(tg) for tg in rows]}}


@router.post("/groups/", status_code=status.HTTP_201_CREATED, summary="Create tax group")
async def create_tax_group(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    tg = TaxGroup(company_id=UUID(str(payload["company_id"])), name=payload["name"], sequence=payload.get("sequence", 10))
    db.add(tg)
    await db.flush()
    await db.refresh(tg)
    return {"success": True, "data": _tax_group_to_dict(tg)}


# ── Taxes ─────────────────────────────────────────────────────────────────────

@router.get("/", summary="List taxes")
async def list_taxes(
    company_id: UUID = Query(...),
    tax_type: str | None = Query(None),
    is_active: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = select(Tax).where(Tax.company_id == company_id)
    if tax_type:
        q = q.where(Tax.tax_type == tax_type)
    if is_active is not None:
        q = q.where(Tax.is_active == is_active)
    rows = (await db.execute(q.order_by(Tax.sequence, Tax.name))).scalars().all()
    return {"success": True, "data": {"items": [_tax_to_dict(t) for t in rows]}}


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create tax")
async def create_tax(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    tax = Tax(
        company_id=UUID(str(payload["company_id"])),
        name=payload["name"],
        description=payload.get("description"),
        tax_type=payload["tax_type"],
        computation=payload.get("computation", "percentage"),
        amount=float(payload.get("amount", 0)),
        tax_account_id=UUID(str(payload["tax_account_id"])) if payload.get("tax_account_id") else None,
        tax_refund_account_id=UUID(str(payload["tax_refund_account_id"])) if payload.get("tax_refund_account_id") else None,
        group_id=UUID(str(payload["group_id"])) if payload.get("group_id") else None,
        include_in_price=payload.get("include_in_price", False),
        sequence=payload.get("sequence", 10),
    )
    db.add(tax)
    await db.flush()
    await db.refresh(tax)
    return {"success": True, "data": _tax_to_dict(tax)}


@router.get("/{tax_id}/", summary="Get tax")
async def get_tax(tax_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Tax).where(Tax.id == tax_id))
    tax = result.scalar_one_or_none()
    if not tax:
        raise HTTPException(status_code=404, detail="Tax not found")
    return {"success": True, "data": _tax_to_dict(tax)}


@router.put("/{tax_id}/", summary="Update tax")
async def update_tax(tax_id: UUID, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Tax).where(Tax.id == tax_id))
    tax = result.scalar_one_or_none()
    if not tax:
        raise HTTPException(status_code=404, detail="Tax not found")
    for f in ["name", "description", "amount", "is_active", "include_in_price", "sequence"]:
        if f in payload:
            setattr(tax, f, payload[f])
    db.add(tax)
    await db.flush()
    await db.refresh(tax)
    return {"success": True, "data": _tax_to_dict(tax)}
