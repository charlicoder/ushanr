"""
Pydantic v2 schemas for journal entry endpoints.

Covers:
  - EntryStateEnum
  - JournalItemRequest / JournalItemResponse
  - CreateJournalEntryRequest / JournalEntryResponse
  - PostEntryRequest, ReverseEntryRequest
  - JournalEntryListFilters (query-parameter dependency)
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Enumerations (must mirror the model-layer enums exactly)
# ---------------------------------------------------------------------------

class EntryStateEnum(str, enum.Enum):
    """Lifecycle state of a journal entry."""

    draft = "draft"
    posted = "posted"
    cancelled = "cancelled"
    reversed = "reversed"


# ---------------------------------------------------------------------------
# Journal item schemas
# ---------------------------------------------------------------------------

class JournalItemRequest(BaseModel):
    """
    A single debit or credit line within a journal entry.

    Either ``debit`` or ``credit`` must be non-zero; both non-zero is rejected.
    """

    model_config = ConfigDict(from_attributes=True)

    account_id: UUID = Field(..., description="Chart-of-account entry this line posts to")
    debit: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        decimal_places=3,
        description="Debit amount (≥ 0). Mutually exclusive with a non-zero credit.",
    )
    credit: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        decimal_places=3,
        description="Credit amount (≥ 0). Mutually exclusive with a non-zero debit.",
    )
    name: str | None = Field(
        default=None,
        max_length=512,
        description="Optional line description / label",
    )
    partner_id: UUID | None = Field(
        default=None,
        description="Optional partner (customer / vendor) UUID",
    )
    analytic_account_id: UUID | None = Field(
        default=None,
        description="Optional analytic account for cost-centre tracking",
    )
    currency_code: str = Field(
        default="KWD",
        max_length=3,
        description="ISO 4217 currency code for this line (defaults to KWD)",
    )

    @model_validator(mode="after")
    def _validate_debit_credit(self) -> "JournalItemRequest":
        """Ensure exactly one of debit / credit is non-zero per line."""
        if self.debit > 0 and self.credit > 0:
            raise ValueError("A journal line cannot have both debit and credit non-zero.")
        if self.debit == 0 and self.credit == 0:
            raise ValueError("A journal line must have a non-zero debit or credit amount.")
        return self


class JournalItemResponse(BaseModel):
    """Full journal item representation returned from the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    journal_entry_id: UUID
    account_id: UUID
    debit: Decimal
    credit: Decimal
    name: str | None = None
    partner_id: UUID | None = None
    analytic_account_id: UUID | None = None
    currency_code: str
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Journal entry schemas
# ---------------------------------------------------------------------------

class CreateJournalEntryRequest(BaseModel):
    """Payload for POST /journal-entries — create a new journal entry."""

    model_config = ConfigDict(from_attributes=True)

    company_id: UUID = Field(..., description="Owning company UUID")
    journal_id: UUID = Field(..., description="Journal (sub-ledger) UUID")
    entry_date: date = Field(..., description="Accounting date for this entry")
    items: list[JournalItemRequest] = Field(
        ...,
        min_length=2,
        description="Journal lines — minimum two lines required for double-entry",
    )
    reference: str | None = Field(
        default=None,
        max_length=256,
        description="External reference number (PO, cheque number, etc.)",
    )
    narration: str | None = Field(
        default=None,
        description="Free-text narrative / memo for this entry",
    )
    partner_id: UUID | None = Field(
        default=None,
        description="Primary partner (customer / vendor) associated with the entry",
    )
    currency_code: str = Field(
        default="KWD",
        max_length=3,
        description="Functional currency of the entry",
    )

    @model_validator(mode="after")
    def _validate_balanced(self) -> "CreateJournalEntryRequest":
        """Ensure total debits equal total credits (double-entry invariant)."""
        total_debit = sum(item.debit for item in self.items)
        total_credit = sum(item.credit for item in self.items)
        if total_debit != total_credit:
            raise ValueError(
                f"Journal entry is not balanced: debit={total_debit}, credit={total_credit}."
            )
        return self


class JournalEntryResponse(BaseModel):
    """Full journal entry representation including nested line items."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    journal_id: UUID
    entry_date: date
    state: EntryStateEnum
    reference: str | None = None
    narration: str | None = None
    partner_id: UUID | None = None
    currency_code: str
    reversal_of_id: UUID | None = Field(
        default=None,
        description="UUID of the original entry if this is a reversal",
    )
    posted_by: str | None = None
    posted_at: datetime | None = None
    items: list[JournalItemResponse] = Field(
        default_factory=list,
        description="All debit / credit lines belonging to this entry",
    )
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Action request schemas
# ---------------------------------------------------------------------------

class PostEntryRequest(BaseModel):
    """Payload for POST /journal-entries/{id}/post — confirm and post a draft entry."""

    model_config = ConfigDict(from_attributes=True)

    posted_by: str | None = Field(
        default=None,
        max_length=128,
        description="Username or identifier of the person posting the entry",
    )


class ReverseEntryRequest(BaseModel):
    """Payload for POST /journal-entries/{id}/reverse — create a counter entry."""

    model_config = ConfigDict(from_attributes=True)

    reversal_date: date = Field(
        ...,
        description="Accounting date for the generated reversal entry",
    )
    narration: str | None = Field(
        default=None,
        description="Optional custom narration for the reversal entry",
    )


# ---------------------------------------------------------------------------
# List-filter dependency
# ---------------------------------------------------------------------------

class JournalEntryListFilters:
    """
    Dependency-injectable query parameters for GET /journal-entries.

    Usage::

        @router.get("/journal-entries")
        async def list_entries(filters: Annotated[JournalEntryListFilters, Depends()]):
            ...
    """

    def __init__(
        self,
        company_id: Annotated[UUID, Query(description="Filter by owning company (required)")],
        journal_id: Annotated[
            UUID | None,
            Query(description="Filter by specific journal / sub-ledger"),
        ] = None,
        state: Annotated[
            EntryStateEnum | None,
            Query(description="Filter by entry lifecycle state"),
        ] = None,
        date_from: Annotated[
            date | None,
            Query(description="Start of accounting-date range (inclusive)"),
        ] = None,
        date_to: Annotated[
            date | None,
            Query(description="End of accounting-date range (inclusive)"),
        ] = None,
        partner_id: Annotated[
            UUID | None,
            Query(description="Filter by partner UUID"),
        ] = None,
        search: Annotated[
            str | None,
            Query(
                max_length=128,
                description="Partial match against reference or narration",
            ),
        ] = None,
    ) -> None:
        self.company_id = company_id
        self.journal_id = journal_id
        self.state = state
        self.date_from = date_from
        self.date_to = date_to
        self.partner_id = partner_id
        self.search = search
