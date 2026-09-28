"""
app/api/v1/endpoints/journal_entries.py
──────────────────────────────────────────
Journal entry REST endpoints.

GET    /api/v1/journal-entries/           — list
POST   /api/v1/journal-entries/           — create draft
GET    /api/v1/journal-entries/{id}/      — get detail
POST   /api/v1/journal-entries/{id}/post/ — post entry
POST   /api/v1/journal-entries/{id}/reverse/ — create reversal
DELETE /api/v1/journal-entries/{id}/      — cancel draft
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.deps import get_optional_company_id
from app.core.database import get_db
from app.core.exceptions import ANRBaseError
from app.core.logging import get_logger
from app.models.journal_entry import EntryState, JournalEntry, JournalItem
from app.services.double_entry import JournalEntryData, JournalItemData, double_entry_engine

logger = get_logger(__name__)
router = APIRouter()


def _item_to_dict(item: JournalItem) -> dict:
    return {
        "id": str(item.id),
        "account_id": str(item.account_id),
        "partner_id": str(item.partner_id) if item.partner_id else None,
        "analytic_account_id": str(item.analytic_account_id) if item.analytic_account_id else None,
        "debit_amount": float(item.debit_amount),
        "credit_amount": float(item.credit_amount),
        "name": item.name,
        "currency_code": item.currency_code,
        "amount_currency": float(item.amount_currency) if item.amount_currency else None,
        "currency_rate": float(item.currency_rate) if item.currency_rate else None,
        "reconciled": item.reconciled,
        "sequence": item.sequence,
    }


def _entry_to_dict(entry: JournalEntry) -> dict:
    return {
        "id": str(entry.id),
        "company_id": str(entry.company_id),
        "journal_id": str(entry.journal_id),
        "name": entry.name,
        "reference": entry.reference,
        "narration": entry.narration,
        "entry_date": entry.entry_date.isoformat() if entry.entry_date else None,
        "accounting_date": entry.accounting_date.isoformat() if entry.accounting_date else None,
        "state": entry.state,
        "amount_total": float(entry.amount_total),
        "currency_code": entry.currency_code,
        "is_reversal": entry.is_reversal,
        "reversed_entry_id": str(entry.reversed_entry_id) if entry.reversed_entry_id else None,
        "posted_at": entry.posted_at.isoformat() if entry.posted_at else None,
        "posted_by": entry.posted_by,
        "source_document_type": entry.source_document_type,
        "source_document_id": str(entry.source_document_id) if entry.source_document_id else None,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
        "items": [_item_to_dict(i) for i in (entry.items or [])],
    }


@router.get("/", summary="List journal entries")
async def list_journal_entries(
    company_id: UUID | None = Depends(get_optional_company_id),
    journal_id: UUID | None = Query(None),
    state: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    partner_id: UUID | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = select(JournalEntry)
    if company_id is not None:
        q = q.where(JournalEntry.company_id == company_id)

    if journal_id:
        q = q.where(JournalEntry.journal_id == journal_id)
    if state:
        q = q.where(JournalEntry.state == state)
    if date_from:
        q = q.where(JournalEntry.accounting_date >= date_from)
    if date_to:
        q = q.where(JournalEntry.accounting_date <= date_to)
    if partner_id:
        q = q.where(JournalEntry.partner_id == partner_id)
    if search:
        q = q.where(
            (JournalEntry.name.ilike(f"%{search}%")) |
            (JournalEntry.reference.ilike(f"%{search}%"))
        )

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    q = (
        q.options(selectinload(JournalEntry.items))
        .order_by(JournalEntry.accounting_date.desc(), JournalEntry.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(q)).scalars().all()

    return {
        "success": True,
        "data": {
            "items": [_entry_to_dict(e) for e in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        },
    }


from app.api.v1.endpoints.journal_items import list_journal_items

router.add_api_route(
    "/items/",
    list_journal_items,
    methods=["GET"],
    summary="List all individual journal items (all 3,115 line items)",
)


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create draft journal entry")
async def create_journal_entry(
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        raw_items = payload.get("items", [])
        items = [
            JournalItemData(
                account_id=UUID(str(i["account_id"])),
                debit=Decimal(str(i.get("debit", 0))),
                credit=Decimal(str(i.get("credit", 0))),
                name=i.get("name"),
                partner_id=UUID(str(i["partner_id"])) if i.get("partner_id") else None,
                analytic_account_id=UUID(str(i["analytic_account_id"])) if i.get("analytic_account_id") else None,
                currency_code=i.get("currency_code", "KWD"),
                sequence=i.get("sequence", 10),
            )
            for i in raw_items
        ]

        data = JournalEntryData(
            company_id=UUID(str(payload["company_id"])),
            journal_id=UUID(str(payload["journal_id"])),
            entry_date=date.fromisoformat(payload["entry_date"]),
            accounting_date=date.fromisoformat(payload["accounting_date"]) if payload.get("accounting_date") else None,
            items=items,
            name=payload.get("name"),
            reference=payload.get("reference"),
            narration=payload.get("narration"),
            partner_id=UUID(str(payload["partner_id"])) if payload.get("partner_id") else None,
            currency_code=payload.get("currency_code", "KWD"),
            source_document_type=payload.get("source_document_type"),
        )

        entry = await double_entry_engine.create_draft_entry(db, data)

        # Reload with items
        result = await db.execute(
            select(JournalEntry)
            .where(JournalEntry.id == entry.id)
            .options(selectinload(JournalEntry.items))
        )
        entry = result.scalar_one()

        return {"success": True, "data": _entry_to_dict(entry)}
    except ANRBaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/{entry_id}/", summary="Get journal entry")
async def get_journal_entry(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(JournalEntry)
        .where(JournalEntry.id == entry_id)
        .options(selectinload(JournalEntry.items))
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return {"success": True, "data": _entry_to_dict(entry)}


@router.post("/{entry_id}/post/", summary="Post journal entry")
async def post_journal_entry(
    entry_id: UUID,
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        result = await db.execute(
            select(JournalEntry)
            .where(JournalEntry.id == entry_id)
            .options(selectinload(JournalEntry.items))
        )
        entry = result.scalar_one_or_none()
        if not entry:
            raise HTTPException(status_code=404, detail="Journal entry not found")

        posted_by = (payload or {}).get("posted_by")
        entry = await double_entry_engine.post_entry(db, entry, posted_by=posted_by)

        return {"success": True, "data": _entry_to_dict(entry)}
    except ANRBaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/{entry_id}/reverse/", summary="Reverse journal entry")
async def reverse_journal_entry(
    entry_id: UUID,
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        result = await db.execute(
            select(JournalEntry)
            .where(JournalEntry.id == entry_id)
            .options(selectinload(JournalEntry.items))
        )
        entry = result.scalar_one_or_none()
        if not entry:
            raise HTTPException(status_code=404, detail="Journal entry not found")

        reversal_date = date.fromisoformat(payload["reversal_date"])
        reversal = await double_entry_engine.reverse_entry(
            db,
            entry,
            reversal_date=reversal_date,
            reversal_narration=payload.get("narration"),
            posted_by=payload.get("posted_by"),
        )

        # Reload reversal with items
        result2 = await db.execute(
            select(JournalEntry)
            .where(JournalEntry.id == reversal.id)
            .options(selectinload(JournalEntry.items))
        )
        reversal = result2.scalar_one()

        return {"success": True, "data": _entry_to_dict(reversal)}
    except ANRBaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.delete("/{entry_id}/", status_code=status.HTTP_204_NO_CONTENT, summary="Cancel draft entry")
async def cancel_journal_entry(
    entry_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(JournalEntry).where(JournalEntry.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    if entry.state == EntryState.POSTED.value:
        raise HTTPException(
            status_code=409,
            detail="Cannot cancel a posted journal entry. Create a reversal instead.",
        )
    entry.state = EntryState.CANCELLED.value
    db.add(entry)
