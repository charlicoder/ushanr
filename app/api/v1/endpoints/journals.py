"""
app/api/v1/endpoints/journals.py — Journal CRUD
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_optional_company_id
from app.core.database import get_db
from app.models.journal import Journal

router = APIRouter()


def _journal_to_dict(j: Journal) -> dict:
    return {
        "id": str(j.id),
        "company_id": str(j.company_id),
        "name": j.name,
        "code": j.code,
        "journal_type": j.journal_type,
        "default_account_id": str(j.default_account_id) if j.default_account_id else None,
        "suspense_account_id": str(j.suspense_account_id) if j.suspense_account_id else None,
        "payment_debit_account_id": str(j.payment_debit_account_id) if j.payment_debit_account_id else None,
        "payment_credit_account_id": str(j.payment_credit_account_id) if j.payment_credit_account_id else None,
        "sequence_prefix": j.sequence_prefix,
        "currency_code": j.currency_code,
        "is_active": j.is_active,
        "description": j.description,
        "sequence": j.sequence,
        "created_at": j.created_at.isoformat(),
        "updated_at": j.updated_at.isoformat(),
    }


@router.get("/", summary="List journals")
async def list_journals(
    company_id: UUID | None = Depends(get_optional_company_id),
    journal_type: str | None = Query(None),
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = select(Journal)
    if company_id is not None:
        q = q.where(Journal.company_id == company_id)
    if journal_type:
        q = q.where(Journal.journal_type == journal_type)
    if is_active is not None:
        q = q.where(Journal.is_active == is_active)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(Journal.sequence, Journal.name).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {
        "success": True,
        "data": {
            "items": [_journal_to_dict(j) for j in rows],
            "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        },
    }


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create journal")
async def create_journal(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    journal = Journal(
        company_id=UUID(str(payload["company_id"])),
        name=payload["name"],
        code=payload["code"],
        journal_type=payload["journal_type"],
        default_account_id=UUID(str(payload["default_account_id"])) if payload.get("default_account_id") else None,
        suspense_account_id=UUID(str(payload["suspense_account_id"])) if payload.get("suspense_account_id") else None,
        payment_debit_account_id=UUID(str(payload["payment_debit_account_id"])) if payload.get("payment_debit_account_id") else None,
        payment_credit_account_id=UUID(str(payload["payment_credit_account_id"])) if payload.get("payment_credit_account_id") else None,
        sequence_prefix=payload.get("sequence_prefix"),
        currency_code=payload.get("currency_code"),
        description=payload.get("description"),
        sequence=payload.get("sequence", 10),
    )
    db.add(journal)
    await db.flush()
    await db.refresh(journal)
    return {"success": True, "data": _journal_to_dict(journal)}


@router.get("/{journal_id}/", summary="Get journal")
async def get_journal(journal_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Journal).where(Journal.id == journal_id))
    journal = result.scalar_one_or_none()
    if not journal:
        raise HTTPException(status_code=404, detail="Journal not found")
    return {"success": True, "data": _journal_to_dict(journal)}


@router.put("/{journal_id}/", summary="Update journal")
async def update_journal(journal_id: UUID, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Journal).where(Journal.id == journal_id))
    journal = result.scalar_one_or_none()
    if not journal:
        raise HTTPException(status_code=404, detail="Journal not found")
    updatable = ["name", "description", "is_active", "sequence", "currency_code",
                 "sequence_prefix", "default_account_id", "suspense_account_id",
                 "payment_debit_account_id", "payment_credit_account_id"]
    for f in updatable:
        if f in payload:
            val = payload[f]
            if f.endswith("_account_id") and val:
                val = UUID(str(val))
            setattr(journal, f, val)
    db.add(journal)
    await db.flush()
    await db.refresh(journal)
    return {"success": True, "data": _journal_to_dict(journal)}
