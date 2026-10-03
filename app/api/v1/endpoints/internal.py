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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.invoice import Invoice, InvoiceState, InvoiceType
from app.models.partner import Partner, PartnerType
from app.services.invoice_service import (
    CreateInvoiceData,
    InvoiceLineData,
    InvoiceService,
)

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
    company_id: UUID
    external_id: str = Field(..., description="Customer UUID from ushauth/ushbooknpay")
    name: str = Field(default="", description="Customer display name")
    phone: str = Field(default="")
    email: str = Field(default="")
    currency_code: str = Field(default="KWD")


class EnsurePartnerResponse(BaseModel):
    partner_id: UUID
    created: bool


class InvoiceLineIn(BaseModel):
    account_id: UUID
    name: str
    quantity: Decimal = Decimal("1")
    unit_price: Decimal
    discount: Decimal = Decimal("0")
    tax_id: UUID | None = None
    analytic_account_id: UUID | None = None
    product_id: str | None = None


class CreateInvoiceFromSourceRequest(BaseModel):
    """Create + auto-post an invoice from a source document."""
    company_id: UUID
    partner_id: UUID
    journal_id: UUID
    invoice_date: date
    source_document_type: str  # booking | shop_order | gift_voucher | gift_voucher_purchase
    source_document_id: str    # UUID string of the originating entity
    source_document_ref: str = Field(default="", description="Human-readable ref e.g. B260921001")
    currency_code: str = Field(default="KWD")
    notes: str | None = None
    lines: list[InvoiceLineIn]


class InvoiceCreatedResponse(BaseModel):
    invoice_id: UUID
    invoice_name: str | None
    state: str
    amount_total: Decimal


class CreditNoteRequest(BaseModel):
    """Create a credit note reversing an invoice for the given source document."""
    company_id: UUID
    journal_id: UUID
    source_document_type: str
    source_document_id: str
    notes: str | None = None
    cancellation_date: date | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _ensure_partner(
    session: AsyncSession,
    company_id: UUID,
    external_id: str,
    name: str,
    phone: str,
    email: str,
    currency_code: str,
) -> tuple[Partner, bool]:
    """
    Get or create a Partner by external_id within a company.
    Returns (partner, created_bool).
    """
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

    partner = Partner(
        company_id=company_id,
        external_id=external_id,
        name=name or external_id,
        display_name=name or external_id,
        partner_type=PartnerType.CUSTOMER.value,
        is_customer=True,
        is_vendor=False,
        is_company=False,
        phone=phone or None,
        email=email or None,
        currency_code=currency_code,
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
    # Idempotency check — do not create duplicate invoices
    existing = await session.execute(
        select(Invoice).where(
            Invoice.source_document_type == body.source_document_type,
            Invoice.source_document_id == body.source_document_id,
            Invoice.is_deleted.is_(False),
        )
    )
    existing_invoice = existing.scalar_one_or_none()
    if existing_invoice is not None:
        logger.info(
            "internal_invoice_already_exists",
            invoice_id=str(existing_invoice.id),
            source_type=body.source_document_type,
            source_id=body.source_document_id,
        )
        return InvoiceCreatedResponse(
            invoice_id=existing_invoice.id,
            invoice_name=existing_invoice.name,
            state=existing_invoice.state,
            amount_total=Decimal(str(existing_invoice.amount_total)),
        )

    lines = [
        InvoiceLineData(
            account_id=ln.account_id,
            name=ln.name,
            quantity=ln.quantity,
            unit_price=ln.unit_price,
            discount=ln.discount,
            tax_id=ln.tax_id,
            analytic_account_id=ln.analytic_account_id,
            product_id=ln.product_id,
        )
        for ln in body.lines
    ]

    svc = InvoiceService(session)
    data = CreateInvoiceData(
        company_id=body.company_id,
        partner_id=body.partner_id,
        journal_id=body.journal_id,
        invoice_type=InvoiceType.INVOICE.value,
        invoice_date=body.invoice_date,
        lines=lines,
        currency_code=body.currency_code,
        reference=body.source_document_ref or body.notes,
        notes=body.notes,
        source_document_type=body.source_document_type,
        source_document_id=body.source_document_id,
        source_document_ref=body.source_document_ref,
    )
    invoice = await svc.create_invoice(data)
    invoice = await svc.post_invoice(invoice.id, posted_by="ushnotice")
    await session.commit()

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

    # Build credit note lines mirroring the original invoice lines
    cn_lines = [
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
        for line in original.lines
    ]

    cn_date = body.cancellation_date or date.today()
    svc = InvoiceService(session)
    cn_data = CreateInvoiceData(
        company_id=original.company_id,
        partner_id=original.partner_id,
        journal_id=body.journal_id,
        invoice_type=InvoiceType.CREDIT_NOTE.value,
        invoice_date=cn_date,
        lines=cn_lines,
        currency_code=original.currency_code,
        reference=f"CN-{original.name or original.reference or original.source_document_ref or body.source_document_id}",
        notes=body.notes or f"Credit note for {body.source_document_type} {body.source_document_id}",
        source_document_type=f"{body.source_document_type}_credit",
        source_document_id=body.source_document_id,
        source_document_ref=original.source_document_ref or "",
    )
    credit_note = await svc.create_invoice(cn_data)
    credit_note = await svc.post_invoice(credit_note.id, posted_by="ushnotice")
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
