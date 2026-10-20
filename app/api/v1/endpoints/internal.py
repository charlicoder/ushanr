"""
app/api/v1/endpoints/internal.py
──────────────────────────────────
Internal service-to-service endpoints for ushnotice invoice creation.

Authenticated via shared X-Internal-Key header (INTERNAL_API_KEY setting).
Never expose these routes publicly — they must be behind the internal network.

Endpoints:
  POST /api/v1/internal/partners/ensure/
      Idempotently get or create a Partner record for a customer.

  POST /api/v1/internal/invoices/from-source/
      Create and immediately post an invoice from a source document
      (booking, shop_order, gift_voucher, gift_voucher_purchase).

  POST /api/v1/internal/invoices/credit-note/
      Create and post a credit note reversing an existing invoice.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.account import Account, AccountType
from app.models.company import Company
from app.models.invoice import Invoice, InvoiceState, InvoiceType
from app.models.journal import Journal, JournalType
from app.models.journal_entry import EntryState, JournalEntry
from app.models.partner import Partner, PartnerType
from app.services.invoice_service import (
    CreateInvoiceData,
    InvoiceLineData,
    InvoiceService,
)
from app.utils.timezone import local_today

logger = get_logger(__name__)
router = APIRouter()


# ── Auth dependency ───────────────────────────────────────────────────────────


def _verify_internal_key(request: Request) -> None:
    """Verify the X-Internal-Key or X-App-Token header matches INTERNAL_API_KEY or USHSPA_TOKEN."""
    settings = get_settings()
    expected_keys = {k for k in (settings.INTERNAL_API_KEY, settings.USHSPA_TOKEN) if k}
    if not expected_keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API key / USHSPA_TOKEN not configured on this service.",
        )

    provided = (
        request.headers.get("X-Internal-Key")
        or request.headers.get("X-App-Token")
        or ""
    ).strip()

    # Also support "Bearer <token>" if provided in Authorization header
    auth_header = request.headers.get("Authorization", "").strip()
    if not provided and auth_header.startswith("Bearer "):
        provided = auth_header[7:].strip()

    if not provided or provided not in expected_keys:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing X-Internal-Key / X-App-Token header.",
        )


InternalAuth = Depends(_verify_internal_key)


# ── Schemas ───────────────────────────────────────────────────────────────────


class EnsurePartnerRequest(BaseModel):
    """Idempotently get or create a Partner by external_id."""
    company_id: UUID | str | None = Field(default=None, description="Company UUID")
    external_id: str = Field(..., description="Customer UUID from ushauth/ushbooknpay")
    name: str | None = Field(default="", description="Customer display name")
    phone: str | None = Field(default="", description="Customer phone")
    email: str | None = Field(default="", description="Customer email")
    currency_code: str | None = Field(default="KWD", description="Currency code")
    partner_type: str | None = Field(default="customer", description="Partner type")


class EnsurePartnerResponse(BaseModel):
    partner_id: UUID
    created: bool


class InvoiceLineIn(BaseModel):
    account_id: UUID | str | None = None
    name: str = "Service"
    quantity: Decimal = Decimal("1")
    unit_price: Decimal = Decimal("0")
    discount: Decimal = Decimal("0")
    tax_id: UUID | str | None = None
    analytic_account_id: UUID | str | None = None
    product_id: str | None = None


class CreateInvoiceFromSourceRequest(BaseModel):
    """Create + auto-post an invoice from a source document."""
    company_id: UUID | str | None = Field(default=None)
    partner_id: UUID | str
    journal_id: UUID | str | None = Field(default=None)
    invoice_date: date
    source_document_type: str  # booking | shop_order | gift_voucher | gift_voucher_purchase
    source_document_id: str    # UUID string of the originating entity
    source_document_ref: str = Field(default="", description="Human-readable ref e.g. B260921001")
    currency_code: str = Field(default="KWD")
    notes: str | None = None
    lines: list[InvoiceLineIn]
    is_paid: bool | None = Field(default=None, description="Whether the invoice is already paid")
    payment_status: str | None = Field(default=None, description="Payment status e.g. paid, success")
    amount_paid: Decimal | None = Field(default=None, description="Amount already paid")
    payment_id: str | None = Field(default=None, description="Payment UUID / external payment reference")
    payment_reference: str | None = Field(default=None, description="Payment reference")


class InvoiceCreatedResponse(BaseModel):
    invoice_id: UUID
    invoice_name: str | None
    state: str
    amount_total: Decimal


class CreditNoteRequest(BaseModel):
    """Create a credit note reversing an invoice for the given source document."""
    company_id: UUID | None = None
    journal_id: UUID | None = None
    source_document_type: str
    source_document_id: str
    notes: str | None = None
    cancellation_date: date | None = None
    cancellation_fee: Decimal = Decimal("0.000")
    refund_amount: Decimal | None = None
    refund_method: str | None = None
    refund_number: str | None = None
    branch_id: UUID | None = None
    processed_by: str | None = None
    record_refund_payment: bool = False  # When True, also post outbound refund payment


# ── Helpers ───────────────────────────────────────────────────────────────────


def _safe_uuid(val: Any) -> UUID | None:
    """Safely parse UUID without raising ValueError on arbitrary strings."""
    if not val:
        return None
    s = str(val).strip()
    if not s or s.lower() in ("none", "null", "undefined"):
        return None
    try:
        return UUID(s)
    except (ValueError, TypeError, AttributeError):
        return None


async def _ensure_partner(
    session: AsyncSession,
    company_id: UUID | str | None,
    external_id: str,
    name: str | None,
    phone: str | None,
    email: str | None,
    currency_code: str | None,
) -> tuple[Partner, bool]:
    """
    Get or create a Partner by external_id within a company.
    Returns (partner, created_bool).
    """
    if not company_id or not isinstance(company_id, UUID):
        company_id = _safe_uuid(company_id)

    # Ensure company exists, fallback to first active company if needed
    comp_exists = None
    if company_id:
        comp_res = await session.execute(
            select(Company).where(Company.id == company_id, Company.is_active.is_(True))
        )
        comp_exists = comp_res.scalar_one_or_none()

    if comp_exists is None:
        first_comp = await session.execute(
            select(Company).where(Company.is_active.is_(True)).order_by(Company.created_at.asc()).limit(1)
        )
        comp = first_comp.scalar_one_or_none()
        if comp is not None:
            company_id = comp.id
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No active company configured in ushanr.",
            )

    result = await session.execute(
        select(Partner).where(
            Partner.company_id == company_id,
            Partner.external_id == external_id,
            Partner.is_deleted.is_(False),
        )
    )
    partner = result.scalar_one_or_none()
    if partner is not None:
        return partner, False

    clean_name = (name or "").strip() or str(external_id)
    clean_phone = (phone or "").strip() or None
    clean_email = (email or "").strip() or None
    clean_currency = (currency_code or "").strip() or "KWD"

    partner = Partner(
        company_id=company_id,
        external_id=str(external_id),
        name=clean_name,
        display_name=clean_name,
        partner_type=PartnerType.CUSTOMER.value,
        is_customer=True,
        is_vendor=False,
        is_company=False,
        phone=clean_phone,
        email=clean_email,
        currency_code=clean_currency,
        is_active=True,
    )
    session.add(partner)
    await session.flush()
    return partner, True


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "/partners/ensure/",
    response_model=EnsurePartnerResponse,
    status_code=status.HTTP_200_OK,
    summary="Idempotently get or create a Partner by external_id",
)
async def ensure_partner(
    body: EnsurePartnerRequest,
    _: None = InternalAuth,
    session: AsyncSession = Depends(get_db),
) -> EnsurePartnerResponse:
    """
    Called by ushnotice before creating an invoice to obtain a partner_id.
    If the customer does not yet have a Partner record in ushanr, one is
    created automatically from the provided snapshot data.
    """
    partner, created = await _ensure_partner(
        session,
        company_id=body.company_id,
        external_id=body.external_id,
        name=body.name,
        phone=body.phone,
        email=body.email,
        currency_code=body.currency_code,
    )
    await session.commit()
    logger.info(
        "internal_partner_ensured",
        partner_id=str(partner.id),
        external_id=body.external_id,
        created=created,
    )
    return EnsurePartnerResponse(partner_id=partner.id, created=created)


@router.get(
    "/invoices/by-source/",
    response_model=InvoiceCreatedResponse,
    summary="Get existing invoice for a source document",
)
async def get_invoice_by_source(
    source_document_type: str = Query(...),
    source_document_id: str = Query(...),
    _: None = InternalAuth,
    session: AsyncSession = Depends(get_db),
) -> InvoiceCreatedResponse:
    """Fetch invoice for a source document by its source_document_type and source_document_id."""
    result = await session.execute(
        select(Invoice).where(
            Invoice.source_document_type == source_document_type,
            Invoice.source_document_id == source_document_id,
            Invoice.is_deleted.is_(False),
        )
    )
    existing_invoice = result.scalar_one_or_none()
    if existing_invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No invoice found for {source_document_type} {source_document_id}",
        )
    return InvoiceCreatedResponse(
        invoice_id=existing_invoice.id,
        invoice_name=existing_invoice.name,
        state=existing_invoice.state,
        amount_total=Decimal(str(existing_invoice.amount_total)),
    )


@router.post(
    "/invoices/from-source/",
    response_model=InvoiceCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create and post an invoice from a source document",
)
async def create_invoice_from_source(
    body: CreateInvoiceFromSourceRequest,
    _: None = InternalAuth,
    session: AsyncSession = Depends(get_db),
) -> InvoiceCreatedResponse:
    """
    Called by ushnotice to auto-generate an invoice for a confirmed booking,
    shop order, gift voucher purchase, etc.

    The invoice is created as DRAFT and immediately posted to the GL.
    Idempotency: if an invoice for the same source_document_id already exists,
    the existing invoice is returned without creating a duplicate.
    """
    p_status_clean = str(body.payment_status or "").strip().lower()
    is_paid_signal = (
        body.is_paid is True
        or p_status_clean in ("paid", "success", "completed", "successful", "rewarded")
    )
    is_explicitly_unpaid = (
        not is_paid_signal
        and (
            body.is_paid is False
            or p_status_clean in ("pending", "not_initiated", "unpaid")
        )
    )

    # Idempotency check — do not create duplicate invoices.
    # Match by source_document_id OR source_document_ref OR reference.
    id_conds = []
    if body.source_document_id:
        id_conds.append(Invoice.source_document_id == str(body.source_document_id))
    if body.source_document_ref:
        id_conds.append(Invoice.source_document_ref == str(body.source_document_ref))
        id_conds.append(Invoice.reference == str(body.source_document_ref))

    existing_invoice = None
    if id_conds:
        existing = await session.execute(
            select(Invoice).where(
                Invoice.source_document_type == body.source_document_type,
                or_(*id_conds),
                Invoice.is_deleted.is_(False),
            ).order_by(Invoice.created_at.desc())
        )
        existing_invoice = existing.scalars().first()

    if existing_invoice is not None:
        # If existing invoice exists and caller indicates payment has completed, update to PAID
        if is_paid_signal and existing_invoice.state != InvoiceState.PAID.value:
            paid_val = float(body.amount_paid if body.amount_paid is not None and body.amount_paid > Decimal("0") else existing_invoice.amount_total)
            existing_invoice.amount_paid = paid_val
            existing_invoice.amount_residual = max(0.0, float(Decimal(str(existing_invoice.amount_total)) - Decimal(str(paid_val))))
            if existing_invoice.amount_residual == 0.0:
                existing_invoice.state = InvoiceState.PAID.value
                if existing_invoice.journal_entry_id:
                    je = await session.get(JournalEntry, existing_invoice.journal_entry_id)
                    if je:
                        je.state = EntryState.PAID.value
                        session.add(je)
            elif existing_invoice.amount_paid > 0.0:
                existing_invoice.state = InvoiceState.PARTIAL.value
            session.add(existing_invoice)
            await session.commit()

        logger.info(
            "internal_invoice_already_exists",
            invoice_id=str(existing_invoice.id),
            source_type=body.source_document_type,
            source_id=body.source_document_id,
            source_ref=body.source_document_ref,
        )
        return InvoiceCreatedResponse(
            invoice_id=existing_invoice.id,
            invoice_name=existing_invoice.name,
            state=existing_invoice.state,
            amount_total=Decimal(str(existing_invoice.amount_total)),
        )

    # In our system invoices are only created after payment (payment_status=success/paid).
    # Disallow creating invoices for unpaid or pending source documents.
    if is_explicitly_unpaid or not is_paid_signal:
        logger.info(
            "internal_invoice_creation_skipped_unpaid",
            source_type=body.source_document_type,
            source_id=body.source_document_id,
            source_ref=body.source_document_ref,
            payment_status=body.payment_status,
            is_paid=body.is_paid,
            message="Invoice creation skipped because source document is unpaid/pending. Invoices are only created when payment_status=success.",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invoice creation is disallowed for unpaid/pending source documents. Invoices are only created when payment_status=success.",
        )

    # Ensure company exists, fallback to first active company if needed
    effective_company_id = _safe_uuid(body.company_id) if body.company_id else None
    comp_exists = None
    if effective_company_id:
        comp_res = await session.execute(
            select(Company).where(Company.id == effective_company_id, Company.is_active.is_(True))
        )
        comp_exists = comp_res.scalar_one_or_none()
    if comp_exists is None:
        first_comp = await session.execute(
            select(Company).where(Company.is_active.is_(True)).order_by(Company.created_at.asc()).limit(1)
        )
        comp = first_comp.scalar_one_or_none()
        if comp is not None:
            effective_company_id = comp.id
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No active company configured in ushanr.",
            )

    # Ensure journal exists, fallback to first active sale journal if needed
    effective_journal_id = _safe_uuid(body.journal_id) if body.journal_id else None
    jour_exists = None
    if effective_journal_id:
        jour_res = await session.execute(
            select(Journal).where(Journal.id == effective_journal_id, Journal.is_active.is_(True))
        )
        jour_exists = jour_res.scalar_one_or_none()
    if jour_exists is None:
        first_jour = await session.execute(
            select(Journal).where(
                Journal.company_id == effective_company_id,
                Journal.journal_type == JournalType.SALE.value,
                Journal.is_active.is_(True),
            ).order_by(Journal.created_at.asc()).limit(1)
        )
        jour = first_jour.scalar_one_or_none()
        if jour is not None:
            effective_journal_id = jour.id
        else:
            first_jour_any = await session.execute(
                select(Journal).where(
                    Journal.journal_type == JournalType.SALE.value,
                    Journal.is_active.is_(True),
                ).order_by(Journal.created_at.asc()).limit(1)
            )
            jour_any = first_jour_any.scalar_one_or_none()
            if jour_any is not None:
                effective_journal_id = jour_any.id

    effective_partner_id = _safe_uuid(body.partner_id) or body.partner_id

    # Resolve default revenue account for line items if needed
    async def _get_default_revenue_account_id() -> UUID | None:
        first_acct = await session.execute(
            select(Account).where(
                Account.company_id == effective_company_id,
                Account.account_type == AccountType.REVENUE.value,
                Account.is_active.is_(True),
                Account.is_deleted.is_(False),
            ).order_by(Account.created_at.asc()).limit(1)
        )
        def_acct_obj = first_acct.scalar_one_or_none()
        if def_acct_obj:
            return def_acct_obj.id
        first_acct_any = await session.execute(
            select(Account).where(
                Account.account_type == AccountType.REVENUE.value,
                Account.is_active.is_(True),
                Account.is_deleted.is_(False),
            ).order_by(Account.created_at.asc()).limit(1)
        )
        def_acct_any = first_acct_any.scalar_one_or_none()
        return def_acct_any.id if def_acct_any else None

    # Ensure line accounts exist, fallback to first active revenue account if needed
    lines = []
    default_account_id = None
    input_lines = body.lines or []
    if not input_lines:
        default_account_id = await _get_default_revenue_account_id()
        lines.append(
            InvoiceLineData(
                account_id=default_account_id,
                name=body.source_document_ref or body.notes or "Service",
                quantity=Decimal("1"),
                unit_price=Decimal("0"),
                discount=Decimal("0"),
            )
        )
    else:
        for ln in input_lines:
            target_acct_id = _safe_uuid(ln.account_id) or ln.account_id
            acct_exists = None
            if target_acct_id:
                acct_res = await session.execute(
                    select(Account).where(
                        Account.id == target_acct_id,
                        Account.is_active.is_(True),
                        Account.is_deleted.is_(False),
                    )
                )
                acct_exists = acct_res.scalar_one_or_none()
            if acct_exists is None:
                if default_account_id is None:
                    default_account_id = await _get_default_revenue_account_id()
                if default_account_id:
                    target_acct_id = default_account_id

            lines.append(
                InvoiceLineData(
                    account_id=target_acct_id,
                    name=ln.name,
                    quantity=ln.quantity,
                    unit_price=ln.unit_price,
                    discount=ln.discount,
                    tax_id=_safe_uuid(ln.tax_id) if ln.tax_id else None,
                    analytic_account_id=_safe_uuid(ln.analytic_account_id) if ln.analytic_account_id else None,
                    product_id=ln.product_id,
                )
            )

    svc = InvoiceService(session)
    parsed_pmt_id = _safe_uuid(body.payment_id)
    raw_pmt_str = (
        str(body.payment_id).strip()
        if body.payment_id and str(body.payment_id).strip().lower() not in ("none", "null", "undefined")
        else None
    )
    effective_payment_id = parsed_pmt_id or raw_pmt_str
    effective_payment_ref = body.payment_reference or (raw_pmt_str if not parsed_pmt_id else None)

    data = CreateInvoiceData(
        company_id=effective_company_id,
        partner_id=effective_partner_id,
        journal_id=effective_journal_id,
        invoice_type=InvoiceType.INVOICE.value,
        invoice_date=body.invoice_date,
        lines=lines,
        currency_code=body.currency_code,
        reference=body.source_document_ref or body.notes,
        notes=body.notes,
        source_document_type=body.source_document_type,
        source_document_id=body.source_document_id,
        source_document_ref=body.source_document_ref,
        is_paid=False if is_explicitly_unpaid else (body.is_paid if body.is_paid is not None else False),
        payment_status=body.payment_status,
        amount_paid=Decimal("0") if is_explicitly_unpaid else body.amount_paid,
        payment_id=effective_payment_id if not is_explicitly_unpaid else None,
        payment_reference=effective_payment_ref if not is_explicitly_unpaid else None,
    )

    try:
        invoice = await svc.create_invoice(data)
        invoice = await svc.post_invoice(invoice.id, posted_by="ushnotice")
        if is_paid_signal and invoice.journal_entry_id:
            je = await session.get(JournalEntry, invoice.journal_entry_id)
            if je and je.state != EntryState.PAID.value:
                je.state = EntryState.PAID.value
                session.add(je)
        await session.commit()
    except Exception as exc:
        logger.error(
            "internal_invoice_creation_failed",
            error=str(exc),
            source_type=body.source_document_type,
            source_id=body.source_document_id,
            partner_id=str(effective_partner_id),
            company_id=str(effective_company_id),
            journal_id=str(effective_journal_id),
            exc_info=True,
        )
        raise

    logger.info(
        "internal_invoice_created",
        invoice_id=str(invoice.id),
        invoice_name=invoice.name,
        source_type=body.source_document_type,
        source_id=body.source_document_id,
        amount_total=float(invoice.amount_total),
    )
    return InvoiceCreatedResponse(
        invoice_id=invoice.id,
        invoice_name=invoice.name,
        state=invoice.state,
        amount_total=Decimal(str(invoice.amount_total)),
    )


class MarkInvoicePaidRequest(BaseModel):
    invoice_id: UUID | str | None = None
    invoice_name: str | None = None
    source_document_type: str | None = None
    source_document_id: str | None = None
    source_document_ref: str | None = None
    amount_paid: Decimal | None = None
    payment_id: UUID | str | None = None


@router.post(
    "/invoices/mark-paid/",
    response_model=InvoiceCreatedResponse,
    summary="Mark invoice as paid or partially paid",
)
async def mark_invoice_paid_endpoint(
    body: MarkInvoicePaidRequest,
    _: None = InternalAuth,
    session: AsyncSession = Depends(get_db),
) -> InvoiceCreatedResponse:
    inv = None
    parsed_id = _safe_uuid(body.invoice_id) if body.invoice_id else None
    if parsed_id:
        res = await session.execute(select(Invoice).where(Invoice.id == parsed_id, Invoice.is_deleted.is_(False)))
        inv = res.scalar_one_or_none()

    if not inv and body.invoice_name:
        res = await session.execute(select(Invoice).where(Invoice.name == body.invoice_name, Invoice.is_deleted.is_(False)))
        inv = res.scalar_one_or_none()

    if not inv and body.source_document_type and (body.source_document_id or body.source_document_ref):
        id_conds = []
        if body.source_document_id:
            id_conds.append(Invoice.source_document_id == str(body.source_document_id))
        if body.source_document_ref:
            id_conds.append(Invoice.source_document_ref == str(body.source_document_ref))
            id_conds.append(Invoice.reference == str(body.source_document_ref))
        res = await session.execute(
            select(Invoice).where(
                Invoice.source_document_type == body.source_document_type,
                or_(*id_conds),
                Invoice.is_deleted.is_(False),
            ).order_by(Invoice.created_at.desc())
        )
        inv = res.scalars().first()

    if not inv and not body.invoice_id and not body.invoice_name and not (body.source_document_type and (body.source_document_id or body.source_document_ref)):
        raise HTTPException(status_code=400, detail="Missing invoice identifier.")

    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    svc = InvoiceService(session)
    inv = await svc.mark_invoice_paid(
        inv.id,
        amount_paid=body.amount_paid,
        payment_id=body.payment_id,
    )
    await session.commit()
    return InvoiceCreatedResponse(
        invoice_id=inv.id,
        invoice_name=inv.name,
        state=inv.state,
        amount_total=Decimal(str(inv.amount_total)),
    )


@router.post(
    "/invoices/credit-note/",
    response_model=InvoiceCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a credit note reversing a source document invoice",
)
async def create_credit_note(
    body: CreditNoteRequest,
    _: None = InternalAuth,
    session: AsyncSession = Depends(get_db),
) -> InvoiceCreatedResponse:
    """
    Called by ushnotice when a booking is cancelled or a payment is refunded.
    Finds the original posted invoice and creates a full credit note that
    reverses the GL entry.

    If no posted invoice exists for the source document (e.g. unpaid booking),
    returns 204 No Content (no accounting action needed).
    """
    # Find original invoice
    result = await session.execute(
        select(Invoice).where(
            Invoice.source_document_type == body.source_document_type,
            Invoice.source_document_id == body.source_document_id,
            Invoice.is_deleted.is_(False),
        )
    )
    original = result.scalar_one_or_none()

    if original is None or original.state not in (
        InvoiceState.POSTED.value,
        InvoiceState.PAID.value,
        InvoiceState.PARTIAL.value,
    ):
        logger.info(
            "internal_credit_note_skipped_no_posted_invoice",
            source_type=body.source_document_type,
            source_id=body.source_document_id,
            existing_state=original.state if original else "none",
        )
        # Return the original if draft/confirmed (not yet posted)
        if original:
            return InvoiceCreatedResponse(
                invoice_id=original.id,
                invoice_name=original.name,
                state=original.state,
                amount_total=Decimal(str(original.amount_total)),
            )
        raise HTTPException(status_code=404, detail="No invoice found for source document.")

    # Check for existing credit note for the same source document
    existing_cn = await session.execute(
        select(Invoice).where(
            Invoice.source_document_type == f"{body.source_document_type}_credit",
            Invoice.source_document_id == body.source_document_id,
            Invoice.is_deleted.is_(False),
        )
    )
    existing_cn_invoice = existing_cn.scalar_one_or_none()
    if existing_cn_invoice is not None:
        if body.record_refund_payment and existing_cn_invoice.state != InvoiceState.PAID.value:
            credit_note = existing_cn_invoice
            eff_refund_amount = Decimal(str(body.refund_amount or credit_note.amount_total))
            fee = Decimal(str(body.cancellation_fee or "0.000"))
            cn_date = body.cancellation_date or local_today()
            target_company_id = original.company_id or body.company_id

            from app.models.payment import PaymentType
            from app.services.payment_service import CreatePaymentData, PaymentService

            pmt_journal_id = None
            ref_method_str = str(body.refund_method or "").strip().lower()
            if ref_method_str == "cash":
                j_stmt = select(Journal.id).where(
                    Journal.company_id == target_company_id,
                    Journal.journal_type == JournalType.CASH.value,
                    Journal.is_active.is_(True),
                ).limit(1)
                pmt_journal_id = (await session.execute(j_stmt)).scalar_one_or_none()
            else:
                if "knet" in ref_method_str:
                    knet_stmt = select(Journal.id).where(
                        Journal.company_id == target_company_id,
                        Journal.journal_type == JournalType.BANK.value,
                        Journal.is_active.is_(True),
                        or_(
                            Journal.code.ilike("%knet%"),
                            Journal.name.ilike("%knet%"),
                        ),
                    ).limit(1)
                    pmt_journal_id = (await session.execute(knet_stmt)).scalar_one_or_none()

                if not pmt_journal_id:
                    bank_stmt = select(Journal.id).where(
                        Journal.company_id == target_company_id,
                        Journal.journal_type == JournalType.BANK.value,
                        Journal.is_active.is_(True),
                    ).limit(1)
                    pmt_journal_id = (await session.execute(bank_stmt)).scalar_one_or_none()

            if not pmt_journal_id:
                any_bank_cash = select(Journal.id).where(
                    Journal.company_id == target_company_id,
                    Journal.journal_type.in_([JournalType.BANK.value, JournalType.CASH.value]),
                    Journal.is_active.is_(True),
                ).limit(1)
                pmt_journal_id = (await session.execute(any_bank_cash)).scalar_one_or_none()

            if not pmt_journal_id:
                pmt_journal_id = credit_note.journal_id

            pmt_svc = PaymentService(session)
            pmt_data = CreatePaymentData(
                company_id=original.company_id,
                journal_id=pmt_journal_id,
                payment_type=PaymentType.OUTBOUND.value,
                amount=eff_refund_amount,
                payment_date=cn_date,
                currency_code=original.currency_code,
                partner_id=original.partner_id,
                partner_name=getattr(original, "partner_name", None),
                payment_method=body.refund_method or "cash",
                reference=body.refund_number or f"REF-{credit_note.name or original.name}",
                notes=body.notes or f"Customer refund for {body.source_document_type} {body.source_document_id}",
                created_by=body.processed_by or "ushnotice",
                is_refund=True,
                refund_number=body.refund_number,
                cancellation_fee=fee,
            )
            outbound_pmt = await pmt_svc.create_payment(pmt_data)
            outbound_pmt = await pmt_svc.post_payment(outbound_pmt.id, posted_by=body.processed_by or "ushnotice")
            await pmt_svc.allocate_payment(
                payment_id=outbound_pmt.id,
                invoice_id=credit_note.id,
                amount=eff_refund_amount,
                allocation_date=cn_date,
            )
            await session.commit()
            await session.refresh(credit_note)
            logger.info(
                "internal_credit_note_paid_on_refund_process",
                credit_note_id=str(credit_note.id),
                payment_id=str(outbound_pmt.id),
            )
            return InvoiceCreatedResponse(
                invoice_id=credit_note.id,
                invoice_name=credit_note.name,
                state=credit_note.state,
                amount_total=Decimal(str(credit_note.amount_total)),
            )

        logger.info(
            "internal_credit_note_already_exists",
            credit_note_id=str(existing_cn_invoice.id),
        )
        return InvoiceCreatedResponse(
            invoice_id=existing_cn_invoice.id,
            invoice_name=existing_cn_invoice.name,
            state=existing_cn_invoice.state,
            amount_total=Decimal(str(existing_cn_invoice.amount_total)),
        )


    orig_total = Decimal(str(original.amount_total))
    fee = Decimal(str(body.cancellation_fee or "0.000"))
    if body.refund_amount is not None:
        eff_refund_amount = Decimal(str(body.refund_amount))
    elif fee > Decimal("0.000"):
        eff_refund_amount = max(Decimal("0.000"), orig_total - fee)
    else:
        eff_refund_amount = orig_total

    # Build credit note lines mirroring the original invoice lines (adjusted for fee if present)
    cn_lines = []
    if eff_refund_amount < orig_total and len(original.lines) == 1:
        single_line = original.lines[0]
        cn_lines.append(
            InvoiceLineData(
                account_id=single_line.account_id,
                name=f"{single_line.name} (Refund after fee {fee} KWD)" if fee > 0 else single_line.name,
                quantity=Decimal("1"),
                unit_price=eff_refund_amount,
                discount=Decimal("0"),
                tax_id=single_line.tax_id,
                analytic_account_id=single_line.analytic_account_id,
                product_id=single_line.product_id,
            )
        )
    elif eff_refund_amount < orig_total and len(original.lines) > 1 and orig_total > Decimal("0"):
        ratio = eff_refund_amount / orig_total
        for line in original.lines:
            scaled_price = (Decimal(str(line.unit_price)) * ratio).quantize(Decimal("0.001"))
            cn_lines.append(
                InvoiceLineData(
                    account_id=line.account_id,
                    name=line.name,
                    quantity=Decimal(str(line.quantity)),
                    unit_price=scaled_price,
                    discount=Decimal(str(line.discount)),
                    tax_id=line.tax_id,
                    analytic_account_id=line.analytic_account_id,
                    product_id=line.product_id,
                )
            )
    else:
        for line in original.lines:
            cn_lines.append(
                InvoiceLineData(
                    account_id=line.account_id,
                    name=line.name,
                    quantity=Decimal(str(line.quantity)),
                    unit_price=Decimal(str(line.unit_price)),
                    discount=Decimal(str(line.discount)),
                    tax_id=line.tax_id,
                    analytic_account_id=line.analytic_account_id,
                    product_id=line.product_id,
                )
            )

    cn_date = body.cancellation_date or local_today()
    target_company_id = original.company_id or body.company_id

    # Resolve a valid Credit Note journal for this company:
    # 1. If body.journal_id is provided, verify it exists and is active for this company
    eff_journal_id = None
    if body.journal_id:
        j_check = await session.execute(
            select(Journal.id).where(
                Journal.id == body.journal_id,
                Journal.company_id == target_company_id,
                Journal.is_active.is_(True),
            )
        )
        eff_journal_id = j_check.scalar_one_or_none()

    # 2. Prefer dedicated credit note journal for this company (e.g. code='RINV' or name ILIKE '%credit note%')
    if not eff_journal_id:
        cn_j_stmt = select(Journal.id).where(
            Journal.company_id == target_company_id,
            Journal.is_active.is_(True),
            or_(
                Journal.code == "RINV",
                Journal.name.ilike("%credit note%"),
            ),
        ).limit(1)
        cn_j_res = await session.execute(cn_j_stmt)
        eff_journal_id = cn_j_res.scalar_one_or_none()

    # 3. Fall back to original invoice's journal if valid
    if not eff_journal_id and original.journal_id:
        orig_j_stmt = select(Journal.id).where(
            Journal.id == original.journal_id,
            Journal.company_id == target_company_id,
            Journal.is_active.is_(True),
        )
        orig_j_res = await session.execute(orig_j_stmt)
        eff_journal_id = orig_j_res.scalar_one_or_none()

    # 4. Fall back to any active sale journal for this company
    if not eff_journal_id:
        sale_j_stmt = select(Journal.id).where(
            Journal.company_id == target_company_id,
            Journal.journal_type == JournalType.SALE.value,
            Journal.is_active.is_(True),
        ).limit(1)
        sale_j_res = await session.execute(sale_j_stmt)
        eff_journal_id = sale_j_res.scalar_one_or_none()

    if not eff_journal_id:
        raise HTTPException(
            status_code=500,
            detail=f"No active journal found for company {target_company_id} to issue credit note.",
        )

    svc = InvoiceService(session)
    cn_data = CreateInvoiceData(
        company_id=target_company_id,
        partner_id=original.partner_id,
        journal_id=eff_journal_id,
        invoice_type=InvoiceType.CREDIT_NOTE.value,
        invoice_date=cn_date,
        lines=cn_lines,
        currency_code=original.currency_code,
        reference=f"CN-{body.refund_number or original.name or original.reference or original.source_document_ref or body.source_document_id}",
        notes=body.notes or f"Credit note for {body.source_document_type} {body.source_document_id}",
        source_document_type=f"{body.source_document_type}_credit",
        source_document_id=body.source_document_id,
        source_document_ref=original.source_document_ref or "",
    )
    credit_note = await svc.create_invoice(cn_data)
    credit_note = await svc.post_invoice(credit_note.id, posted_by=body.processed_by or "ushnotice")

    # If money was refunded to customer AND caller requested payment recording,
    # record and allocate an OUTBOUND payment.
    # On cancellation (record_refund_payment=false): only the credit note is created.
    # On refund completion (record_refund_payment=true): the outbound payment is posted.
    if body.record_refund_payment and eff_refund_amount > Decimal("0.000") and (original.state in (InvoiceState.PAID.value, InvoiceState.PARTIAL.value) or body.refund_amount is not None):
        from app.models.payment import PaymentType
        from app.services.payment_service import CreatePaymentData, PaymentService

        pmt_journal_id = None
        ref_method_str = str(body.refund_method or "").strip().lower()
        if ref_method_str == "cash":
            j_stmt = select(Journal.id).where(
                Journal.company_id == target_company_id,
                Journal.journal_type == JournalType.CASH.value,
                Journal.is_active.is_(True),
            ).limit(1)
            j_res = await session.execute(j_stmt)
            pmt_journal_id = j_res.scalar_one_or_none()
        else:
            if "knet" in ref_method_str:
                knet_stmt = select(Journal.id).where(
                    Journal.company_id == target_company_id,
                    Journal.journal_type == JournalType.BANK.value,
                    Journal.is_active.is_(True),
                    or_(
                        Journal.code.ilike("%knet%"),
                        Journal.name.ilike("%knet%"),
                    ),
                ).limit(1)
                knet_res = await session.execute(knet_stmt)
                pmt_journal_id = knet_res.scalar_one_or_none()

            if not pmt_journal_id:
                bank_stmt = select(Journal.id).where(
                    Journal.company_id == target_company_id,
                    Journal.journal_type == JournalType.BANK.value,
                    Journal.is_active.is_(True),
                ).limit(1)
                bank_res = await session.execute(bank_stmt)
                pmt_journal_id = bank_res.scalar_one_or_none()

        if not pmt_journal_id:
            any_bank_cash = select(Journal.id).where(
                Journal.company_id == target_company_id,
                Journal.journal_type.in_([JournalType.BANK.value, JournalType.CASH.value]),
                Journal.is_active.is_(True),
            ).limit(1)
            pmt_journal_id = (await session.execute(any_bank_cash)).scalar_one_or_none()

        if not pmt_journal_id:
            pmt_journal_id = eff_journal_id

        pmt_svc = PaymentService(session)
        pmt_data = CreatePaymentData(
            company_id=original.company_id,
            journal_id=pmt_journal_id,
            payment_type=PaymentType.OUTBOUND.value,
            amount=eff_refund_amount,
            payment_date=cn_date,
            currency_code=original.currency_code,
            partner_id=original.partner_id,
            partner_name=getattr(original, "partner_name", None),
            payment_method=body.refund_method or "cash",
            reference=body.refund_number or f"REF-{credit_note.name or original.name}",
            notes=body.notes or f"Customer refund for {body.source_document_type} {body.source_document_id}",
            created_by=body.processed_by or "ushnotice",
            is_refund=True,
            refund_number=body.refund_number,
            cancellation_fee=fee,
        )
        outbound_pmt = await pmt_svc.create_payment(pmt_data)
        outbound_pmt = await pmt_svc.post_payment(outbound_pmt.id, posted_by=body.processed_by or "ushnotice")
        await pmt_svc.allocate_payment(
            payment_id=outbound_pmt.id,
            invoice_id=credit_note.id,
            amount=eff_refund_amount,
            allocation_date=cn_date,
        )

    await session.commit()

    logger.info(
        "internal_credit_note_created",
        credit_note_id=str(credit_note.id),
        original_invoice_id=str(original.id),
        source_type=body.source_document_type,
        source_id=body.source_document_id,
    )
    return InvoiceCreatedResponse(
        invoice_id=credit_note.id,
        invoice_name=credit_note.name,
        state=credit_note.state,
        amount_total=Decimal(str(credit_note.amount_total)),
    )
