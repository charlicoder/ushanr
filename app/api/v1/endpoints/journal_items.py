"""
app/api/v1/endpoints/journal_items.py
──────────────────────────────────────
Individual journal items / line items REST endpoints.
Allows querying all individual debit/credit records (all 3,115 items in the ledger).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_optional_company_id
from app.core.database import get_db
from app.models.account import Account
from app.models.journal_entry import JournalEntry, JournalItem
from app.models.partner import Partner

router = APIRouter()


@router.get("/", summary="List individual journal items (line items)")
async def list_journal_items(
    company_id: UUID | None = Depends(get_optional_company_id),
    entry_number: str | None = Query(None, description="Filter by entry number (e.g. BILL/2026/09/0009)"),
    account_id: UUID | None = Query(None, description="Filter by account ID"),
    account_code: str | None = Query(None, description="Filter by account code (e.g. 100621)"),
    partner_id: UUID | None = Query(None, description="Filter by partner ID"),
    partner_name: str | None = Query(None, description="Filter by partner name"),
    matching_number: str | None = Query(None, description="Filter by reconciliation matching #"),
    reconciled: bool | None = Query(None, description="Filter by reconciliation status"),
    date_from: date | None = Query(None, description="Filter items on or after date"),
    date_to: date | None = Query(None, description="Filter items on or before date"),
    search: str | None = Query(None, description="Search entry number, label, account code, partner"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Returns individual journal line items (all 3,115 lines from double-entry accounting).
    Each record represents one debit or credit side of a journal entry.
    """
    q = (
        select(
            JournalItem.id,
            JournalItem.date,
            JournalItem.entry_id,
            JournalEntry.name.label("entry_number"),
            JournalEntry.state.label("entry_state"),
            JournalItem.account_id,
            Account.code.label("account_code"),
            Account.name.label("account_name"),
            JournalItem.partner_id,
            Partner.name.label("partner_name"),
            JournalItem.name.label("label"),
            JournalItem.debit_amount,
            JournalItem.credit_amount,
            JournalItem.reference.label("matching_number"),
            JournalItem.reconciled,
            JournalItem.currency_code,
            JournalItem.sequence,
        )
        .join(JournalEntry, JournalItem.entry_id == JournalEntry.id)
        .join(Account, JournalItem.account_id == Account.id)
        .outerjoin(Partner, JournalItem.partner_id == Partner.id)
    )

    if company_id is not None:
        q = q.where(JournalItem.company_id == company_id)

    if entry_number:
        q = q.where(JournalEntry.name.ilike(f"%{entry_number.strip()}%"))

    if account_id:
        q = q.where(JournalItem.account_id == account_id)

    if account_code:
        q = q.where(Account.code == account_code.strip())

    if partner_id:
        q = q.where(JournalItem.partner_id == partner_id)

    if partner_name:
        q = q.where(Partner.name.ilike(f"%{partner_name.strip()}%"))

    if matching_number:
        q = q.where(JournalItem.reference == matching_number.strip())

    if reconciled is not None:
        q = q.where(JournalItem.reconciled == reconciled)

    if date_from:
        q = q.where(JournalItem.date >= date_from)

    if date_to:
        q = q.where(JournalItem.date <= date_to)

    if search:
        search_term = f"%{search.strip()}%"
        q = q.where(
            (JournalEntry.name.ilike(search_term))
            | (JournalItem.name.ilike(search_term))
            | (Account.code.ilike(search_term))
            | (Account.name.ilike(search_term))
            | (Partner.name.ilike(search_term))
            | (JournalItem.reference.ilike(search_term))
        )

    # Aggregations for total count and monetary sums
    count_subq = q.subquery()
    stats_q = select(
        func.count(count_subq.c.id),
        func.coalesce(func.sum(count_subq.c.debit_amount), 0),
        func.coalesce(func.sum(count_subq.c.credit_amount), 0),
    )
    total_count, total_debit, total_credit = (await db.execute(stats_q)).first()

    # Paginate and order by date desc, sequence asc
    q = q.order_by(JournalItem.date.desc(), JournalEntry.name.desc(), JournalItem.sequence.asc())
    q = q.offset((page - 1) * page_size).limit(page_size)

    rows = (await db.execute(q)).all()

    items = [
        {
            "id": str(r.id),
            "date": r.date.isoformat() if r.date else None,
            "entry_id": str(r.entry_id),
            "entry_number": r.entry_number,
            "entry_state": r.entry_state,
            "account_id": str(r.account_id),
            "account_code": r.account_code,
            "account_name": r.account_name,
            "partner_id": str(r.partner_id) if r.partner_id else None,
            "partner_name": r.partner_name,
            "label": r.label,
            "debit": float(Decimal(str(r.debit_amount))),
            "credit": float(Decimal(str(r.credit_amount))),
            "matching_number": r.matching_number,
            "reconciled": r.reconciled,
            "currency_code": r.currency_code,
            "sequence": r.sequence,
        }
        for r in rows
    ]

    return {
        "success": True,
        "data": {
            "items": items,
            "total": total_count,
            "total_debit": float(Decimal(str(total_debit))),
            "total_credit": float(Decimal(str(total_credit))),
            "page": page,
            "page_size": page_size,
            "pages": (total_count + page_size - 1) // page_size if total_count else 0,
        },
    }
