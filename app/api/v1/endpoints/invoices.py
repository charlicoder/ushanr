"""
app/api/v1/endpoints/invoices.py
──────────────────────────────────
Invoice / Bill / Credit-Note endpoints.

Routes
------
GET    /api/v1/invoices/                  — list with filters
POST   /api/v1/invoices/                  — create draft
GET    /api/v1/invoices/{id}/             — get with lines
PUT    /api/v1/invoices/{id}/             — update draft fields
POST   /api/v1/invoices/{id}/post/        — post → journal entry
POST   /api/v1/invoices/{id}/cancel/      — cancel (reversal if posted)
POST   /api/v1/invoices/{id}/credit-note/ — create credit note from posted invoice
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.deps import get_optional_company_id
from app.core.database import get_db
from app.core.exceptions import ANRBaseError, EntryAlreadyPostedError
from app.core.logging import get_logger
from app.core.security import require_permission
from app.models.account import Account
from app.models.invoice import Invoice, InvoiceLine, InvoiceState, InvoiceType
from app.models.journal_entry import JournalEntry
from app.models.partner import Partner
from app.services.double_entry import JournalEntryData, JournalItemData, double_entry_engine
from app.services.invoice_service import InvoiceService

logger = get_logger(__name__)
router = APIRouter()

ZERO = Decimal("0")


def _line_to_dict(line: InvoiceLine) -> dict:
    return {
        "id": str(line.id),
        "account_id": str(line.account_id),
        "analytic_account_id": str(line.analytic_account_id) if line.analytic_account_id else None,
        "tax_id": str(line.tax_id) if line.tax_id else None,
        "name": line.name,
        "description": line.description,
        "quantity": float(line.quantity),
        "unit_price": float(line.unit_price),
        "discount": float(line.discount),
        "tax_rate": float(line.tax_rate),
        "subtotal": float(line.subtotal),
        "tax_amount": float(line.tax_amount),
        "total": float(line.total),
        "product_id": line.product_id,
        "product_code": line.product_code,
        "sequence": line.sequence,
    }


# Invoice status semantics:
#   posted    = not linked with any payment
#   partial   = linked with payment(s), partially paid
#   paid      = linked with payment(s), fully paid
_STATE_LABELS = {
    "draft": "Draft",
    "confirmed": "Confirmed",
    "posted": "Posted",
    "partial": "Partially Paid",
    "paid": "Paid",
    "cancelled": "Cancelled",
}


def _invoice_to_dict(inv: Invoice) -> dict:
    return {
        "id": str(inv.id),
        "company_id": str(inv.company_id),
        "name": inv.name,
        "reference": inv.reference or inv.source_document_ref,
        "invoice_type": inv.invoice_type,
        "invoice_number": inv.name,
        "state": inv.state,
        "status_label": _STATE_LABELS.get(inv.state, str(inv.state).title()),
        "partner_id": str(inv.partner_id),
        "journal_id": str(inv.journal_id),
        "journal_entry_id": str(inv.journal_entry_id) if inv.journal_entry_id else None,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "accounting_date": inv.accounting_date.isoformat() if inv.accounting_date else None,
        "payment_terms": inv.payment_terms,
        "currency_code": inv.currency_code,
        "amount_untaxed": float(inv.amount_untaxed),
        "amount_tax": float(inv.amount_tax),
        "amount_total": float(inv.amount_total),
        "amount_paid": float(inv.amount_paid),
        "amount_residual": float(inv.amount_residual),
        "reversed_invoice_id": str(inv.reversed_invoice_id) if inv.reversed_invoice_id else None,
        "is_reversal": inv.is_reversal,
        "source_document_type": inv.source_document_type,
        "source_document_id": inv.source_document_id,
        "source_document_ref": inv.source_document_ref,
        "notes": inv.notes,
        "created_at": inv.created_at.isoformat(),
        "updated_at": inv.updated_at.isoformat(),
        "lines": [_line_to_dict(l) for l in (inv.lines or [])],
    }


def _compute_line_totals(
    quantity: Decimal,
    unit_price: Decimal,
    discount: Decimal,
    tax_rate: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """Returns (subtotal, tax_amount, total) — all Decimal."""
    subtotal = (quantity * unit_price * (1 - discount / 100)).quantize(
        Decimal("0.001"), rounding=ROUND_HALF_UP
    )
    tax_amount = (subtotal * tax_rate / 100).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    total = subtotal + tax_amount
    return subtotal, tax_amount, total


@router.get("/", summary="List invoices / bills")
async def list_invoices(
    _: dict = Depends(require_permission("invoices.list")),
    company_id: UUID | None = Depends(get_optional_company_id),
    invoice_type: str | None = Query(None),
    state: str | None = Query(None),
    partner_id: UUID | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = select(Invoice).where(Invoice.is_deleted == False)
    if company_id is not None:
        q = q.where(Invoice.company_id == company_id)
    if invoice_type:
        q = q.where(Invoice.invoice_type == invoice_type)
    if state:
        q = q.where(Invoice.state == state)
    if partner_id:
        q = q.where(Invoice.partner_id == partner_id)
    if date_from:
        q = q.where(Invoice.invoice_date >= date_from)
    if date_to:
        q = q.where(Invoice.invoice_date <= date_to)
    if search:
        q = q.where(
            (Invoice.name.ilike(f"%{search}%")) | (Invoice.reference.ilike(f"%{search}%"))
        )

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    q = (
        q.options(selectinload(Invoice.lines))
        .order_by(Invoice.invoice_date.desc(), Invoice.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(q)).scalars().all()

    # Auto-heal any existing invoices where name is missing
    needs_commit = False
    svc = InvoiceService(db)
    for inv in rows:
        if not inv.name:
            inv.name = await svc._generate_invoice_name(
                inv.company_id, inv.invoice_type, inv.invoice_date
            )
            needs_commit = True
        if not inv.reference and inv.source_document_ref:
            inv.reference = inv.source_document_ref
            needs_commit = True
    if needs_commit:
        await db.commit()

    return {
        "success": True,
        "data": {
            "items": [_invoice_to_dict(inv) for inv in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        },
    }


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create draft invoice / bill")
async def create_invoice(
    _: dict = Depends(require_permission("invoices.create")),
    payload: dict = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    raw_lines = payload.get("lines", [])
    if not raw_lines:
        raise HTTPException(status_code=422, detail="Invoice must have at least one line")

    inv_type = payload.get("invoice_type", InvoiceType.INVOICE.value)

    # Invoice date is always the creation date (never taken from the payload/source doc)
    today = date.today()

    # Generate sequential name if not provided
    svc = InvoiceService(db)
    inv_name = payload.get("name")
    if not inv_name:
        inv_name = await svc._generate_invoice_name(
            UUID(str(payload["company_id"])),
            inv_type,
            today,
        )
    ref = payload.get("reference") or payload.get("source_document_ref")

    invoice = Invoice(
        name=inv_name,
        company_id=UUID(str(payload["company_id"])),
        journal_id=UUID(str(payload["journal_id"])),
        partner_id=UUID(str(payload["partner_id"])),
        invoice_type=inv_type,
        state=InvoiceState.DRAFT.value,
        invoice_date=today,
        due_date=date.fromisoformat(payload["due_date"]) if payload.get("due_date") else None,
        accounting_date=date.fromisoformat(payload["accounting_date"]) if payload.get("accounting_date") else today,
        payment_terms=payload.get("payment_terms", "immediate"),
        payment_terms_days=payload.get("payment_terms_days", 0),
        currency_code=payload.get("currency_code", "KWD"),
        reference=ref,
        notes=payload.get("notes"),
        source_document_type=payload.get("source_document_type"),
        source_document_id=payload.get("source_document_id"),
        source_document_ref=payload.get("source_document_ref"),
    )
    db.add(invoice)
    await db.flush()  # Get invoice.id

    # Build lines and accumulate totals
    total_untaxed = ZERO
    total_tax = ZERO
    for i, raw in enumerate(raw_lines):
        qty = Decimal(str(raw.get("quantity", 1)))
        price = Decimal(str(raw.get("unit_price", 0)))
        discount = Decimal(str(raw.get("discount", 0)))
        tax_rate = Decimal(str(raw.get("tax_rate", 0)))

        subtotal, tax_amount, line_total = _compute_line_totals(qty, price, discount, tax_rate)

        line = InvoiceLine(
            company_id=invoice.company_id,
            invoice_id=invoice.id,
            account_id=UUID(str(raw["account_id"])),
            analytic_account_id=UUID(str(raw["analytic_account_id"])) if raw.get("analytic_account_id") else None,
            tax_id=UUID(str(raw["tax_id"])) if raw.get("tax_id") else None,
            name=raw["name"],
            description=raw.get("description"),
            quantity=float(qty),
            unit_price=float(price),
            discount=float(discount),
            tax_rate=float(tax_rate),
            subtotal=float(subtotal),
            tax_amount=float(tax_amount),
            total=float(line_total),
            product_id=raw.get("product_id"),
            product_code=raw.get("product_code"),
            sequence=(i + 1) * 10,
        )
        db.add(line)
        total_untaxed += subtotal
        total_tax += tax_amount

    amount_total = total_untaxed + total_tax
    invoice.amount_untaxed = float(total_untaxed)
    invoice.amount_tax = float(total_tax)
    invoice.amount_total = float(amount_total)

    is_paid = payload.get("is_paid")
    p_status = str(payload.get("payment_status") or "").strip().lower()
    amt_paid = payload.get("amount_paid")
    is_associated_payment = (
        is_paid is True
        or p_status in ("paid", "success", "completed", "successful", "rewarded")
        or bool(payload.get("payment_id"))
        or (amt_paid is not None and Decimal(str(amt_paid)) > ZERO)
    )

    if is_associated_payment:
        paid_val = Decimal(str(amt_paid)) if amt_paid is not None else amount_total
        res_val = max(ZERO, amount_total - paid_val)
        invoice.amount_paid = float(paid_val)
        invoice.amount_residual = float(res_val)
        if res_val == ZERO and paid_val > ZERO:
            invoice.state = InvoiceState.PAID.value
        elif paid_val > ZERO:
            invoice.state = InvoiceState.PARTIAL.value
    else:
        invoice.amount_paid = 0.0
        invoice.amount_residual = float(amount_total)

    await db.flush()

    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice.id).options(selectinload(Invoice.lines))
    )
    invoice = result.scalar_one()
    return {"success": True, "data": _invoice_to_dict(invoice)}


@router.get("/{invoice_id}/", summary="Get invoice detail")
async def get_invoice(
    invoice_id: UUID,
    _: dict = Depends(require_permission("invoices.view")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.is_deleted == False)
        .options(selectinload(Invoice.lines))
    )
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    needs_commit = False
    if not inv.name:
        svc = InvoiceService(db)
        inv.name = await svc._generate_invoice_name(
            inv.company_id, inv.invoice_type, inv.invoice_date
        )
        needs_commit = True
    if not inv.reference and inv.source_document_ref:
        inv.reference = inv.source_document_ref
        needs_commit = True
    if needs_commit:
        await db.commit()

    return {"success": True, "data": _invoice_to_dict(inv)}


@router.put("/{invoice_id}/", summary="Update draft invoice")
async def update_invoice(
    invoice_id: UUID,
    _: dict = Depends(require_permission("invoices.update")),
    payload: dict = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.is_deleted == False)
        .options(selectinload(Invoice.lines))
    )
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.state != InvoiceState.DRAFT.value:
        raise HTTPException(status_code=409, detail="Only draft invoices can be updated")

    updatable = ["reference", "notes", "due_date", "accounting_date", "payment_terms"]
    for f in updatable:
        if f in (payload or {}):
            val = payload[f]
            if f in ("due_date", "accounting_date") and val:
                val = date.fromisoformat(val)
            setattr(inv, f, val)

    db.add(inv)
    await db.flush()
    await db.refresh(inv)
    return {"success": True, "data": _invoice_to_dict(inv)}


@router.post("/{invoice_id}/post/", summary="Post invoice — creates journal entry")
async def post_invoice(
    invoice_id: UUID,
    _: dict = Depends(require_permission("invoices.post")),
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Post a draft invoice:
    - INVOICE/CREDIT_NOTE: Dr Accounts Receivable, Cr Revenue (per line) + Cr Tax
    - BILL/VENDOR_CREDIT:  Cr Accounts Payable, Dr Expense (per line) + Dr Tax
    """
    result = await db.execute(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.is_deleted == False)
        .options(selectinload(Invoice.lines))
    )
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.state != InvoiceState.DRAFT.value:
        raise HTTPException(status_code=409, detail=f"Invoice is already {inv.state}")

    posted_by = (payload or {}).get("posted_by")

    # Get the journal to find default accounts
    from app.models.journal import Journal
    journal_result = await db.execute(select(Journal).where(Journal.id == inv.journal_id))
    journal = journal_result.scalar_one_or_none()

    # Get partner for receivable/payable account
    partner_result = await db.execute(select(Partner).where(Partner.id == inv.partner_id))
    partner = partner_result.scalar_one_or_none()

    is_sale = inv.invoice_type in (InvoiceType.INVOICE.value, InvoiceType.CREDIT_NOTE.value)

    try:
        items: list[JournalItemData] = []

        # Counterpart account (AR or AP)
        if is_sale:
            counterpart_account_id = (
                partner.receivable_account_id if partner and partner.receivable_account_id
                else journal.default_account_id if journal else None
            )
        else:
            counterpart_account_id = (
                partner.payable_account_id if partner and partner.payable_account_id
                else journal.default_account_id if journal else None
            )

        if not counterpart_account_id:
            raise HTTPException(
                status_code=422,
                detail="No receivable/payable account configured. Set it on the partner or journal.",
            )

        # Ensure sequential name and reference are populated
        if not inv.name:
            svc = InvoiceService(db)
            inv.name = await svc._generate_invoice_name(
                inv.company_id, inv.invoice_type, inv.invoice_date
            )
        if not inv.reference and inv.source_document_ref:
            inv.reference = inv.source_document_ref

        # Line items: Revenue/Expense per line
        for line in inv.lines:
            line_amount = Decimal(str(line.subtotal))
            if is_sale:
                # Credit revenue, debit AR
                items.append(JournalItemData(
                    account_id=line.account_id,
                    credit=line_amount,
                    name=line.name,
                    partner_id=inv.partner_id,
                    analytic_account_id=line.analytic_account_id,
                    currency_code=inv.currency_code,
                    sequence=line.sequence,
                ))
            else:
                # Debit expense
                items.append(JournalItemData(
                    account_id=line.account_id,
                    debit=line_amount,
                    name=line.name,
                    partner_id=inv.partner_id,
                    analytic_account_id=line.analytic_account_id,
                    currency_code=inv.currency_code,
                    sequence=line.sequence,
                ))

        # Tax lines
        if inv.amount_tax and inv.amount_tax != 0:
            tax_amount = Decimal(str(abs(inv.amount_tax)))
            # Use journal default or suspense account for tax
            tax_account_id = journal.default_account_id if journal else counterpart_account_id
            if is_sale:
                items.append(JournalItemData(
                    account_id=tax_account_id,
                    credit=tax_amount,
                    name="Tax",
                    currency_code=inv.currency_code,
                    sequence=9000,
                ))
            else:
                items.append(JournalItemData(
                    account_id=tax_account_id,
                    debit=tax_amount,
                    name="Tax",
                    currency_code=inv.currency_code,
                    sequence=9000,
                ))

        # Counterpart (AR/AP) — balancing line
        total = Decimal(str(inv.amount_total))
        if is_sale:
            items.append(JournalItemData(
                account_id=counterpart_account_id,
                debit=total,
                name=f"Receivable — {inv.name or invoice_id}",
                partner_id=inv.partner_id,
                due_date=inv.due_date,
                currency_code=inv.currency_code,
                sequence=10000,
            ))
        else:
            items.append(JournalItemData(
                account_id=counterpart_account_id,
                credit=total,
                name=f"Payable — {inv.name or invoice_id}",
                partner_id=inv.partner_id,
                due_date=inv.due_date,
                currency_code=inv.currency_code,
                sequence=10000,
            ))

        entry_data = JournalEntryData(
            company_id=inv.company_id,
            journal_id=inv.journal_id,
            entry_date=inv.invoice_date,
            accounting_date=inv.accounting_date or inv.invoice_date,
            items=items,
            reference=inv.reference,
            narration=f"Invoice {inv.name or inv.id}",
            partner_id=inv.partner_id,
            currency_code=inv.currency_code,
            source_document_type="invoice",
            source_document_id=inv.id,
            posted_by=posted_by,
        )

        journal_entry = await double_entry_engine.create_and_post_entry(db, entry_data)

        inv.state = InvoiceState.POSTED.value
        inv.journal_entry_id = journal_entry.id
        inv.accounting_date = inv.accounting_date or inv.invoice_date
        db.add(inv)
        await db.flush()

        result = await db.execute(
            select(Invoice).where(Invoice.id == invoice_id).options(selectinload(Invoice.lines))
        )
        inv = result.scalar_one()
        return {"success": True, "data": _invoice_to_dict(inv)}

    except ANRBaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.post("/{invoice_id}/cancel/", summary="Cancel invoice")
async def cancel_invoice(
    invoice_id: UUID,
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.is_deleted == False)
        .options(selectinload(Invoice.lines))
    )
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.state == InvoiceState.CANCELLED.value:
        raise HTTPException(status_code=409, detail="Invoice already cancelled")

    # If posted, create reversal journal entry
    if inv.state == InvoiceState.POSTED.value and inv.journal_entry_id:
        from sqlalchemy.orm import selectinload as sl
        entry_result = await db.execute(
            select(JournalEntry)
            .where(JournalEntry.id == inv.journal_entry_id)
            .options(sl(JournalEntry.items))
        )
        entry = entry_result.scalar_one_or_none()
        if entry:
            try:
                await double_entry_engine.reverse_entry(
                    db,
                    entry,
                    reversal_date=date.today(),
                    reversal_narration=f"Cancellation of {inv.name or inv.id}",
                    posted_by=(payload or {}).get("cancelled_by"),
                )
            except ANRBaseError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.message)

    inv.state = InvoiceState.CANCELLED.value
    db.add(inv)
    await db.flush()
    return {"success": True, "data": _invoice_to_dict(inv)}


@router.post(
    "/{invoice_id}/credit-note/",
    status_code=status.HTTP_201_CREATED,
    summary="Create credit note from posted invoice",
)
async def create_credit_note(
    invoice_id: UUID,
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Invoice)
        .where(Invoice.id == invoice_id, Invoice.is_deleted == False)
        .options(selectinload(Invoice.lines))
    )
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if inv.state != InvoiceState.POSTED.value:
        raise HTTPException(status_code=422, detail="Can only create credit note from posted invoice")

    cn_type = (
        InvoiceType.CREDIT_NOTE.value
        if inv.invoice_type == InvoiceType.INVOICE.value
        else InvoiceType.VENDOR_CREDIT.value
    )

    cn_date = (payload or {}).get("credit_date") and date.fromisoformat((payload or {})["credit_date"]) or date.today()
    svc = InvoiceService(db)
    cn_name = await svc._generate_invoice_name(inv.company_id, cn_type, cn_date)

    credit_note = Invoice(
        name=cn_name,
        company_id=inv.company_id,
        journal_id=inv.journal_id,
        partner_id=inv.partner_id,
        invoice_type=cn_type,
        state=InvoiceState.DRAFT.value,
        invoice_date=cn_date,
        currency_code=inv.currency_code,
        reference=f"Credit note for {inv.name or inv.id}",
        reversed_invoice_id=inv.id,
        is_reversal=True,
        amount_untaxed=inv.amount_untaxed,
        amount_tax=inv.amount_tax,
        amount_total=inv.amount_total,
        amount_residual=inv.amount_total,
        notes=(payload or {}).get("reason"),
        source_document_type="invoice",
        source_document_id=str(inv.id),
    )
    db.add(credit_note)
    await db.flush()

    # Copy lines
    for line in inv.lines:
        cn_line = InvoiceLine(
            company_id=inv.company_id,
            invoice_id=credit_note.id,
            account_id=line.account_id,
            analytic_account_id=line.analytic_account_id,
            tax_id=line.tax_id,
            name=line.name,
            quantity=line.quantity,
            unit_price=line.unit_price,
            discount=line.discount,
            tax_rate=line.tax_rate,
            subtotal=line.subtotal,
            tax_amount=line.tax_amount,
            total=line.total,
            sequence=line.sequence,
        )
        db.add(cn_line)

    await db.flush()

    result2 = await db.execute(
        select(Invoice).where(Invoice.id == credit_note.id).options(selectinload(Invoice.lines))
    )
    credit_note = result2.scalar_one()
    return {"success": True, "data": _invoice_to_dict(credit_note)}
