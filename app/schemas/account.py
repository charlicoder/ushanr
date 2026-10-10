"""
Pydantic v2 schemas for Chart of Accounts endpoints.

Covers:
  - Enum mirrors for AccountTypeEnum and AccountNatureEnum
  - CreateAccountRequest / UpdateAccountRequest
  - AccountResponse (ORM-mapped)
  - AccountBalanceResponse
  - AccountListFilters (query-parameter dependency)
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field
from app.utils.timezone import LocalDateTime


# ---------------------------------------------------------------------------
# Enumerations (must mirror the model-layer enums exactly)
# ---------------------------------------------------------------------------

class AccountTypeEnum(str, enum.Enum):
    """Top-level classification of an account in the chart of accounts."""

    asset = "asset"
    liability = "liability"
    equity = "equity"
    revenue = "revenue"
    expense = "expense"


class AccountNatureEnum(str, enum.Enum):
    """
    Indicates the normal balance side of an account.

    - ``debit``  – assets, expenses (increases on the debit side)
    - ``credit`` – liabilities, equity, revenue (increases on the credit side)
    """

    debit = "debit"
    credit = "credit"


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class CreateAccountRequest(BaseModel):
    """Payload for POST /accounts — create a new chart-of-account entry."""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Unique account code within the company (e.g. '1010')",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable account name",
    )
    account_type: AccountTypeEnum = Field(
        ...,
        description="Top-level classification: asset, liability, equity, revenue, or expense",
    )
    company_id: UUID = Field(..., description="Owning company UUID")
    parent_id: UUID | None = Field(
        default=None,
        description="UUID of the parent account for hierarchical chart of accounts",
    )
    group_id: UUID | None = Field(
        default=None,
        description="Optional account-group UUID for grouping / reporting",
    )
    is_reconcilable: bool = Field(
        default=False,
        description="Whether journal items posted to this account can be reconciled",
    )
    currency_code: str | None = Field(
        default=None,
        max_length=3,
        description="Optional fixed currency (ISO 4217). Leave null to use company default.",
    )
    description: str | None = Field(
        default=None,
        description="Free-text description or internal notes",
    )


class UpdateAccountRequest(BaseModel):
    """Payload for PATCH /accounts/{id} — all fields are optional."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    account_type: AccountTypeEnum | None = Field(default=None)
    parent_id: UUID | None = Field(default=None)
    group_id: UUID | None = Field(default=None)
    is_reconcilable: bool | None = Field(default=None)
    is_active: bool | None = Field(default=None, description="Soft-activate / deactivate the account")
    currency_code: str | None = Field(default=None, max_length=3)
    description: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class AccountResponse(BaseModel):
    """Full account representation returned by GET and mutation endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    account_type: AccountTypeEnum
    account_nature: AccountNatureEnum
    company_id: UUID
    parent_id: UUID | None = None
    group_id: UUID | None = None
    is_reconcilable: bool
    is_active: bool
    currency_code: str | None = None
    description: str | None = None
    created_at: LocalDateTime
    updated_at: LocalDateTime


class AccountBalanceResponse(BaseModel):
    """Aggregated debit / credit / balance figures for a single account."""

    model_config = ConfigDict(from_attributes=True)

    account_id: UUID
    code: str
    name: str
    debit_total: Decimal = Field(..., description="Sum of all debit amounts on posted journal items")
    credit_total: Decimal = Field(..., description="Sum of all credit amounts on posted journal items")
    balance: Decimal = Field(
        ...,
        description="Net balance: debit_total − credit_total (positive = debit balance)",
    )


# ---------------------------------------------------------------------------
# List-filter dependency
# ---------------------------------------------------------------------------

class AccountListFilters:
    """
    Dependency-injectable query parameters for GET /accounts.

    Usage::

        @router.get("/accounts")
        async def list_accounts(filters: Annotated[AccountListFilters, Depends()]):
            ...
    """

    def __init__(
        self,
        company_id: Annotated[UUID, Query(description="Filter by owning company (required)")],
        account_type: Annotated[
            AccountTypeEnum | None,
            Query(description="Filter by account type"),
        ] = None,
        is_active: Annotated[
            bool | None,
            Query(description="Filter by active status"),
        ] = None,
        search: Annotated[
            str | None,
            Query(
                max_length=128,
                description="Partial match against account code or name",
            ),
        ] = None,
    ) -> None:
        self.company_id = company_id
        self.account_type = account_type
        self.is_active = is_active
        self.search = search
