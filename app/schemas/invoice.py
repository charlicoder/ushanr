"""
Pydantic v2 schemas for invoice / bill endpoints.

Covers:
  - InvoiceTypeEnum, InvoiceStateEnum
  - InvoiceLineRequest / InvoiceLineResponse
  - CreateInvoiceRequest / InvoiceResponse
  - InvoiceListFilters (query-parameter dependency)
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field



# ---------------------------------------------------------------------------
# Enumerations (must mirror the model-layer enums exactly)
# ---------------------------------------------------------------------------

class InvoiceTypeEnum(str, enum.Enum):
    """Distinguishes between outgoing (customer) and incoming (vendor) documents."""

    customer_invoice = "customer_invoice"
    customer_credit_note = "customer_credit_note"
    vendor_bill = "vendor_bill"
    vendor_credit_note = "vendor_credit_note"


class InvoiceStateEnum(str, enum.Enum):
    """Lifecycle state of an invoice / bill."""

    draft = "draft"
    confirmed = "confirmed"
    posted = "posted"
    paid = "paid"
    cancelled = "cancelled"
    reversed = "reversed"


# ---------------------------------------------------------------------------
# Invoice line schemas
# ---------------------------------------------------------------------------

class InvoiceLineRequest(BaseModel):
    """A single product / service line on an invoice."""

    model_config = ConfigDict(from_attributes=True)

    account_id: UUID = Field(
        ...,
        description="Revenue or expense account UUID this line posts to",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="Line description (product name, service description, etc.)",
    )
    quantity: Decimal = Field(
        ...,
        gt=Decimal("0"),
        decimal_places=3,
        description="Quantity (must be strictly positive)",
    )
    unit_price: Decimal = Field(
        ...,
        ge=Decimal("0"),
        decimal_places=3,
        description="Unit price before discount and tax (≥ 0)",
    )
    discount: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        le=Decimal("100"),
        decimal_places=4,
        description="Discount percentage applied to the line (0 – 100)",
    )
    tax_id: UUID | None = Field(
        default=None,
        description="Optional tax / VAT rule UUID to apply to this line",
    )
    analytic_account_id: UUID | None = Field(
        default=None,
        description="Optional analytic account for cost-centre or project tracking",
    )
    product_id: str | None = Field(
        default=None,
        max_length=128,
        description="External product identifier (ERP, catalogue, etc.)",
    )


class InvoiceLineResponse(BaseModel):
    """Full invoice line representation returned from the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    invoice_id: UUID
    account_id: UUID
    name: str
    quantity: Decimal
    unit_price: Decimal
    discount: Decimal
    tax_id: UUID | None = None
    analytic_account_id: UUID | None = None
    product_id: str | None = None
    # Computed totals
    subtotal: Decimal = Field(..., description="quantity × unit_price × (1 − discount/100)")
    tax_amount: Decimal = Field(..., description="Computed tax amount for this line")
    total: Decimal = Field(..., description="subtotal + tax_amount")
    created_at: datetime
    updated_at: datetime




# ---------------------------------------------------------------------------
# Invoice schemas
# ---------------------------------------------------------------------------

class CreateInvoiceRequest(BaseModel):
    """Payload for POST /invoices — create a new invoice or vendor bill."""

    model_config = ConfigDict(from_attributes=True)

    company_id: UUID = Field(..., description="Owning company UUID")
    journal_id: UUID = Field(
        ...,
        description="Journal (accounts-receivable or accounts-payable) UUID",
    )
    partner_id: UUID = Field(
        ...,
        description="Customer or vendor partner UUID",
    )
    invoice_type: InvoiceTypeEnum = Field(
        ...,
        description="Document type: customer invoice, vendor bill, or credit notes",
    )
    invoice_date: date = Field(..., description="Invoice / document date")
    due_date: date | None = Field(
        default=None,
        description="Payment due date (overrides payment_terms if provided)",
    )
    payment_terms: str | None = Field(
        default=None,
        max_length=128,
        description="Payment terms label (e.g. 'Net 30'). Used to derive due_date when not set.",
    )
    currency_code: str = Field(
        default="KWD",
        max_length=3,
        description="ISO 4217 currency code for the invoice",
    )
    lines: list[InvoiceLineRequest] = Field(
        ...,
        min_length=1,
        description="Invoice line items — at least one line required",
    )
    reference: str | None = Field(
        default=None,
        max_length=256,
        description="Vendor reference / purchase order number",
    )
    notes: str | None = Field(
        default=None,
        description="Internal or customer-facing notes printed on the document",
    )
    source_document_type: str | None = Field(
        default=None,
        max_length=64,
        description="Type of the originating source document (e.g. 'purchase_order')",
    )
    source_document_id: str | None = Field(
        default=None,
        max_length=128,
        description="ID of the originating source document for traceability",
    )


class InvoiceResponse(BaseModel):
    """Full invoice representation including nested lines and computed totals."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    journal_id: UUID
    partner_id: UUID
    invoice_type: InvoiceTypeEnum
    state: InvoiceStateEnum
    invoice_date: date
    due_date: date | None = None
    payment_terms: str | None = None
    currency_code: str
    reference: str | None = None
    notes: str | None = None
    source_document_type: str | None = None
    source_document_id: str | None = None
    # Computed totals
    amount_untaxed: Decimal = Field(..., description="Sum of all line subtotals before tax")
    amount_tax: Decimal = Field(..., description="Total tax amount across all lines")
    amount_total: Decimal = Field(..., description="amount_untaxed + amount_tax")
    amount_residual: Decimal = Field(..., description="Outstanding (unpaid) amount")
    journal_entry_id: UUID | None = Field(
        default=None,
        description="UUID of the generated accounting journal entry",
    )
    lines: list[InvoiceLineResponse] = Field(
        default_factory=list,
        description="All line items belonging to this invoice",
    )
    created_at: datetime
    updated_at: datetime




# ---------------------------------------------------------------------------
# List-filter dependency
# ---------------------------------------------------------------------------

class InvoiceListFilters:
    """
    Dependency-injectable query parameters for GET /invoices.

    Usage::

        @router.get("/invoices")
        async def list_invoices(filters: Annotated[InvoiceListFilters, Depends()]):
            ...
    """

    def __init__(
        self,
        company_id: Annotated[UUID, Query(description="Filter by owning company (required)")],
        invoice_type: Annotated[
            InvoiceTypeEnum | None,
            Query(description="Filter by invoice / bill type"),
        ] = None,
        state: Annotated[
            InvoiceStateEnum | None,
            Query(description="Filter by lifecycle state"),
        ] = None,
        partner_id: Annotated[
            UUID | None,
            Query(description="Filter by customer or vendor partner UUID"),
        ] = None,
        date_from: Annotated[
            date | None,
            Query(description="Start of invoice-date range (inclusive)"),
        ] = None,
        date_to: Annotated[
            date | None,
            Query(description="End of invoice-date range (inclusive)"),
        ] = None,
        overdue_only: Annotated[
            bool | None,
            Query(description="When true, return only overdue (past due-date) invoices"),
        ] = None,
        search: Annotated[
            str | None,
            Query(
                max_length=128,
                description="Partial match against reference, notes, or partner name",
            ),
        ] = None,
    ) -> None:
        self.company_id = company_id
        self.invoice_type = invoice_type
        self.state = state
        self.partner_id = partner_id
        self.date_from = date_from
        self.date_to = date_to
        self.overdue_only = overdue_only
        self.search = search
