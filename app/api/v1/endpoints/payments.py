"""
app/api/v1/endpoints/payments.py
──────────────────────────────────
Payment REST endpoints.

Routes
------
GET    /api/v1/payments/                — list
POST   /api/v1/payments/                — create payment
GET    /api/v1/payments/{id}/           — get with allocations
POST   /api/v1/payments/{id}/post/      — post → journal entry
POST   /api/v1/payments/{id}/allocate/  — allocate to invoice
POST   /api/v1/payments/{id}/cancel/    — cancel payment
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
from app.core.exceptions import ANRBaseError, AllocationExceedsBalanceError
from app.core.logging import get_logger
from app.core.security import require_permission
from app.models.invoice import Invoice, InvoiceState
from app.models.journal import Journal
from app.models.payment import Payment, PaymentAllocation, PaymentState, PaymentType
from app.services.double_entry import JournalEntryData, JournalItemData, double_entry_engine

logger = get_logger(__name__)
router = APIRouter()

ZERO = Decimal("0")


def _alloc_to_dict(a: PaymentAllocation) -> dict:
    return {
        "id": str(a.id),
        "payment_id": str(a.payment_id),
        "invoice_id": str(a.invoice_id),
        "amount": float(a.amount),
        "currency_code": a.currency_code,
        "allocation_date": a.allocation_date.isoformat(),
    }


def _payment_to_dict(pmt: Payment) -> dict:
    return {
        "id": str(pmt.id),
        "company_id": str(pmt.company_id),
        "name": pmt.name,
        "reference": pmt.reference,
        "payment_type": pmt.payment_type,
        "state": pmt.state,
        "partner_id": str(pmt.partner_id) if pmt.partner_id else None,
        "partner_name": pmt.partner_name,
        "journal_id": str(pmt.journal_id),
        "journal_entry_id": str(pmt.journal_entry_id) if pmt.journal_entry_id else None,
        "payment_date": pmt.payment_date.isoformat() if pmt.payment_date else None,
        "amount": float(pmt.amount),
        "amount_residual": float(pmt.amount_residual),
        "currency_code": pmt.currency_code,
        "payment_method": pmt.payment_method,
        "payment_provider": pmt.payment_provider,
        "payment_id_external": pmt.payment_id_external,
        "transaction_id": pmt.transaction_id,
        "invoice_id_external": pmt.invoice_id_external,
        "reference_id": pmt.reference_id,
        "track_id": pmt.track_id,
        "payment_data": pmt.payment_data,
        "created_by": pmt.created_by,
        "created_by_name": pmt.created_by_name,
        "notes": pmt.notes,
        "created_at": pmt.created_at.isoformat(),
        "updated_at": pmt.updated_at.isoformat(),
        "allocations": [_alloc_to_dict(a) for a in (pmt.allocations or [])],
    }


@router.get("/", summary="List payments")
async def list_payments(
    company_id: UUID | None = Depends(get_optional_company_id),
    payment_type: str | None = Query(None),
    state: str | None = Query(None),
    partner_id: UUID | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("payments.list")),
) -> dict:
    q = select(Payment)
    if company_id is not None:
        q = q.where(Payment.company_id == company_id)
    if payment_type:
        q = q.where(Payment.payment_type == payment_type)
    if state:
        q = q.where(Payment.state == state)
    if partner_id:
        q = q.where(Payment.partner_id == partner_id)
    if date_from:
        q = q.where(Payment.payment_date >= date_from)
    if date_to:
        q = q.where(Payment.payment_date <= date_to)

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    q = (
        q.options(selectinload(Payment.allocations))
        .order_by(Payment.payment_date.desc(), Payment.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(q)).scalars().all()
    return {
        "success": True,
        "data": {
            "items": [_payment_to_dict(p) for p in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        },
    }


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create payment")
async def create_payment(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("payments.create")),
) -> dict:
    amount = Decimal(str(payload["amount"]))
    if amount <= ZERO:
        raise HTTPException(status_code=422, detail="Payment amount must be positive")

    pmt = Payment(
        company_id=UUID(str(payload["company_id"])),
        journal_id=UUID(str(payload["journal_id"])),
        partner_id=UUID(str(payload["partner_id"])) if payload.get("partner_id") else None,
        partner_name=payload.get("partner_name"),
        payment_type=payload.get("payment_type", PaymentType.INBOUND.value),
        state=PaymentState.DRAFT.value,
        payment_date=date.fromisoformat(payload["payment_date"]),
        amount=float(amount),
        amount_residual=float(amount),
        currency_code=payload.get("currency_code", "KWD"),
        payment_method=payload.get("payment_method"),
        payment_provider=payload.get("payment_provider"),
        payment_id_external=payload.get("payment_id_external"),
        transaction_id=payload.get("transaction_id"),
        invoice_id_external=payload.get("invoice_id_external"),
        reference_id=payload.get("reference_id"),
        track_id=payload.get("track_id"),
        reference=payload.get("reference"),
        payment_data=payload.get("payment_data"),
        created_by=payload.get("created_by"),
        created_by_name=payload.get("created_by_name"),
        notes=payload.get("notes"),
    )
    db.add(pmt)
    await db.flush()

    result = await db.execute(
        select(Payment).where(Payment.id == pmt.id).options(selectinload(Payment.allocations))
    )
    pmt = result.scalar_one()
    return {"success": True, "data": _payment_to_dict(pmt)}


@router.get("/{payment_id}/", summary="Get payment detail")
async def get_payment(
    payment_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("payments.view")),
) -> dict:
    result = await db.execute(
        select(Payment)
        .where(Payment.id == payment_id)
        .options(selectinload(Payment.allocations))
    )
    pmt = result.scalar_one_or_none()
    if not pmt:
        raise HTTPException(status_code=404, detail="Payment not found")
    return {"success": True, "data": _payment_to_dict(pmt)}


@router.post("/{payment_id}/post/", summary="Post payment — creates journal entry")
async def post_payment(
    payment_id: UUID,
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("payments.post")),
) -> dict:
    """
    Post a payment:
    - INBOUND:  Dr Bank/Cash account (journal.payment_debit_account_id), Cr AR
    - OUTBOUND: Cr Bank/Cash account (journal.payment_credit_account_id), Dr AP
    """
    result = await db.execute(
        select(Payment)
        .where(Payment.id == payment_id)
        .options(selectinload(Payment.allocations))
    )
    pmt = result.scalar_one_or_none()
    if not pmt:
        raise HTTPException(status_code=404, detail="Payment not found")
    if pmt.state != PaymentState.DRAFT.value:
        raise HTTPException(status_code=409, detail=f"Payment is already {pmt.state}")

    # Load journal
    journal_result = await db.execute(select(Journal).where(Journal.id == pmt.journal_id))
    journal = journal_result.scalar_one_or_none()
    if not journal:
        raise HTTPException(status_code=422, detail="Journal not found")

    posted_by = (payload or {}).get("posted_by")
    amount = Decimal(str(pmt.amount))

    # Determine account IDs
    if pmt.payment_type == PaymentType.INBOUND.value:
        bank_account_id = journal.payment_debit_account_id or journal.default_account_id
        ar_account_id = journal.payment_credit_account_id or journal.suspense_account_id
    else:
        bank_account_id = journal.payment_credit_account_id or journal.default_account_id
        ar_account_id = journal.payment_debit_account_id or journal.suspense_account_id

    if not bank_account_id or not ar_account_id:
        raise HTTPException(
            status_code=422,
            detail="Journal missing payment debit/credit account configuration.",
        )

    try:
        if pmt.payment_type == PaymentType.INBOUND.value:
            # Dr Bank, Cr AR
            items = [
                JournalItemData(
                    account_id=bank_account_id,
                    debit=amount,
                    name=pmt.reference or f"Payment {pmt.id}",
                    partner_id=pmt.partner_id,
                    currency_code=pmt.currency_code,
                ),
                JournalItemData(
                    account_id=ar_account_id,
                    credit=amount,
                    name=pmt.reference or f"Payment {pmt.id}",
                    partner_id=pmt.partner_id,
                    currency_code=pmt.currency_code,
                ),
            ]
        else:
            # Cr Bank, Dr AP
            items = [
                JournalItemData(
                    account_id=ar_account_id,
                    debit=amount,
                    name=pmt.reference or f"Payment {pmt.id}",
                    partner_id=pmt.partner_id,
                    currency_code=pmt.currency_code,
                ),
                JournalItemData(
                    account_id=bank_account_id,
                    credit=amount,
                    name=pmt.reference or f"Payment {pmt.id}",
                    partner_id=pmt.partner_id,
                    currency_code=pmt.currency_code,
                ),
            ]

        entry_data = JournalEntryData(
            company_id=pmt.company_id,
            journal_id=pmt.journal_id,
            entry_date=pmt.payment_date,
            items=items,
            reference=pmt.reference,
            partner_id=pmt.partner_id,
            currency_code=pmt.currency_code,
            source_document_type="payment",
            source_document_id=pmt.id,
            posted_by=posted_by,
        )

        journal_entry = await double_entry_engine.create_and_post_entry(db, entry_data)
        pmt.state = PaymentState.POSTED.value
        pmt.journal_entry_id = journal_entry.id
        db.add(pmt)
        await db.flush()

        result = await db.execute(
            select(Payment).where(Payment.id == payment_id).options(selectinload(Payment.allocations))
        )
        pmt = result.scalar_one()
        return {"success": True, "data": _payment_to_dict(pmt)}

    except ANRBaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/{payment_id}/allocate/", summary="Allocate payment to invoice")
async def allocate_payment(
    payment_id: UUID,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("payments.update")),
) -> dict:
    """Link a payment to an invoice (partial or full reconciliation)."""
    result = await db.execute(
        select(Payment)
        .where(Payment.id == payment_id)
        .options(selectinload(Payment.allocations))
    )
    pmt = result.scalar_one_or_none()
    if not pmt:
        raise HTTPException(status_code=404, detail="Payment not found")
    if pmt.state != PaymentState.POSTED.value:
        raise HTTPException(status_code=422, detail="Only posted payments can be allocated")

    invoice_id = UUID(str(payload["invoice_id"]))
    alloc_amount = Decimal(str(payload["amount"]))

    residual = Decimal(str(pmt.amount_residual))
    if alloc_amount > residual:
        raise HTTPException(
            status_code=422,
            detail=f"Allocation amount {alloc_amount} exceeds payment residual {residual}",
        )

    # Load invoice
    inv_result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    inv = inv_result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.state not in (InvoiceState.POSTED.value, InvoiceState.PARTIAL.value):
        raise HTTPException(status_code=422, detail="Invoice must be posted to allocate")

    inv_residual = Decimal(str(inv.amount_residual))
    if alloc_amount > inv_residual:
        raise HTTPException(
            status_code=422,
            detail=f"Allocation {alloc_amount} exceeds invoice residual {inv_residual}",
        )

    # Create allocation
    alloc = PaymentAllocation(
        payment_id=pmt.id,
        invoice_id=inv.id,
        amount=float(alloc_amount),
        currency_code=pmt.currency_code,
        allocation_date=date.today(),
    )
    db.add(alloc)

    # Update residuals
    new_pmt_residual = float(residual - alloc_amount)
    new_inv_residual = float(inv_residual - alloc_amount)
    new_inv_paid = float(Decimal(str(inv.amount_paid)) + alloc_amount)

    pmt.amount_residual = new_pmt_residual
    if new_pmt_residual <= 0:
        pmt.state = PaymentState.RECONCILED.value

    inv.amount_paid = new_inv_paid
    inv.amount_residual = new_inv_residual
    if new_inv_residual <= 0:
        inv.state = InvoiceState.PAID.value
    else:
        inv.state = InvoiceState.PARTIAL.value

    db.add(pmt)
    db.add(inv)
    await db.flush()

    return {
        "success": True,
        "data": {
            "allocation_id": str(alloc.id),
            "payment_id": str(pmt.id),
            "invoice_id": str(inv.id),
            "allocated_amount": float(alloc_amount),
            "payment_residual": new_pmt_residual,
            "invoice_residual": new_inv_residual,
        },
    }


@router.post("/{payment_id}/cancel/", summary="Cancel payment")
async def cancel_payment(
    payment_id: UUID,
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("payments.delete")),
) -> dict:
    from app.models.journal_entry import JournalEntry
    from sqlalchemy.orm import selectinload as sl

    result = await db.execute(
        select(Payment).where(Payment.id == payment_id).options(selectinload(Payment.allocations))
    )
    pmt = result.scalar_one_or_none()
    if not pmt:
        raise HTTPException(status_code=404, detail="Payment not found")
    if pmt.state == PaymentState.CANCELLED.value:
        raise HTTPException(status_code=409, detail="Payment already cancelled")

    # Reverse journal entry if posted
    if pmt.state == PaymentState.POSTED.value and pmt.journal_entry_id:
        entry_result = await db.execute(
            select(JournalEntry)
            .where(JournalEntry.id == pmt.journal_entry_id)
            .options(sl(JournalEntry.items))
        )
        entry = entry_result.scalar_one_or_none()
        if entry:
            try:
                await double_entry_engine.reverse_entry(
                    db, entry, reversal_date=date.today(),
                    reversal_narration=f"Cancellation of payment {pmt.id}",
                    posted_by=(payload or {}).get("cancelled_by"),
                )
            except ANRBaseError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.message)

    pmt.state = PaymentState.CANCELLED.value
    db.add(pmt)
    await db.flush()
    return {"success": True, "data": _payment_to_dict(pmt)}
