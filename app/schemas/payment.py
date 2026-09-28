"""
Pydantic v2 schemas for payment endpoints.

Covers:
  - PaymentTypeEnum, PaymentStateEnum
  - CreatePaymentRequest / PaymentResponse
  - AllocatePaymentRequest
  - PaymentListFilters (query-parameter dependency)
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumerations (must mirror the model-layer enums exactly)
# ---------------------------------------------------------------------------

class PaymentTypeEnum(str, enum.Enum):
    """Direction of the payment flow."""

    inbound = "inbound"    # Customer payment received
    outbound = "outbound"  # Vendor payment made


class PaymentStateEnum(str, enum.Enum):
    """Lifecycle state of a payment record."""

    draft = "draft"
    confirmed = "confirmed"
    posted = "posted"
    reconciled = "reconciled"
    cancelled = "cancelled"
    failed = "failed"


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class CreatePaymentRequest(BaseModel):
    """Payload for POST /payments — register an inbound or outbound payment."""

    model_config = ConfigDict(from_attributes=True)

    company_id: UUID = Field(..., description="Owning company UUID")
    journal_id: UUID = Field(
        ...,
        description="Bank or cash journal UUID the payment is recorded against",
    )
    partner_id: UUID = Field(
        ...,
        description="Customer (inbound) or vendor (outbound) partner UUID",
    )
    payment_type: PaymentTypeEnum = Field(
        ...,
        description="Payment direction: inbound (receipt) or outbound (disbursement)",
    )
    payment_date: date = Field(..., description="Date the payment was received / made")
    amount: Decimal = Field(
        ...,
        gt=Decimal("0"),
        decimal_places=3,
        description="Payment amount (must be strictly positive)",
    )
    currency_code: str = Field(
        default="KWD",
        max_length=3,
        description="ISO 4217 currency code of the payment",
    )
    payment_method: str = Field(
        ...,
        max_length=64,
        description="Method of payment (e.g. 'bank_transfer', 'cheque', 'cash', 'knet')",
    )
    payment_provider: str | None = Field(
        default=None,
        max_length=128,
        description="Payment gateway or bank that processed the payment",
    )
    payment_id_external: str | None = Field(
        default=None,
        max_length=256,
        description="External gateway payment ID for reconciliation",
    )
    transaction_id: str | None = Field(
        default=None,
        max_length=256,
        description="Bank transaction / clearing reference",
    )
    reference: str | None = Field(
        default=None,
        max_length=256,
        description="Internal reference or memo",
    )
    payment_data: dict[str, Any] | None = Field(
        default=None,
        description="Arbitrary gateway-specific payload stored as JSONB",
    )
    created_by: str = Field(
        ...,
        max_length=128,
        description="Username or system identifier that initiated the payment",
    )


class AllocatePaymentRequest(BaseModel):
    """Payload for POST /payments/{id}/allocate — link a payment to an invoice."""

    model_config = ConfigDict(from_attributes=True)

    invoice_id: UUID = Field(
        ...,
        description="UUID of the invoice / bill to allocate this payment against",
    )
    amount: Decimal = Field(
        ...,
        gt=Decimal("0"),
        decimal_places=3,
        description="Amount to allocate (must be > 0 and ≤ payment residual)",
    )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class PaymentResponse(BaseModel):
    """Full payment representation returned from the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    journal_id: UUID
    partner_id: UUID
    payment_type: PaymentTypeEnum
    state: PaymentStateEnum
    payment_date: date
    amount: Decimal
    currency_code: str
    payment_method: str
    payment_provider: str | None = None
    payment_id_external: str | None = None
    transaction_id: str | None = None
    reference: str | None = None
    payment_data: dict[str, Any] | None = None
    amount_residual: Decimal = Field(
        ...,
        description="Unallocated (unapplied) portion of the payment",
    )
    journal_entry_id: UUID | None = Field(
        default=None,
        description="UUID of the generated accounting journal entry",
    )
    created_by: str
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# List-filter dependency
# ---------------------------------------------------------------------------

class PaymentListFilters:
    """
    Dependency-injectable query parameters for GET /payments.

    Usage::

        @router.get("/payments")
        async def list_payments(filters: Annotated[PaymentListFilters, Depends()]):
            ...
    """

    def __init__(
        self,
        company_id: Annotated[UUID, Query(description="Filter by owning company (required)")],
        payment_type: Annotated[
            PaymentTypeEnum | None,
            Query(description="Filter by payment direction"),
        ] = None,
        state: Annotated[
            PaymentStateEnum | None,
            Query(description="Filter by lifecycle state"),
        ] = None,
        partner_id: Annotated[
            UUID | None,
            Query(description="Filter by partner UUID"),
        ] = None,
        journal_id: Annotated[
            UUID | None,
            Query(description="Filter by bank / cash journal UUID"),
        ] = None,
        date_from: Annotated[
            date | None,
            Query(description="Start of payment-date range (inclusive)"),
        ] = None,
        date_to: Annotated[
            date | None,
            Query(description="End of payment-date range (inclusive)"),
        ] = None,
        payment_method: Annotated[
            str | None,
            Query(max_length=64, description="Filter by payment method"),
        ] = None,
        search: Annotated[
            str | None,
            Query(
                max_length=128,
                description="Partial match against reference, transaction_id, or payment_id_external",
            ),
        ] = None,
    ) -> None:
        self.company_id = company_id
        self.payment_type = payment_type
        self.state = state
        self.partner_id = partner_id
        self.journal_id = journal_id
        self.date_from = date_from
        self.date_to = date_to
        self.payment_method = payment_method
        self.search = search
