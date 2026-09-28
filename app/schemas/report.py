"""
Pydantic v2 schemas for accounting reporting endpoints.

Covers:
  - General Ledger (GL) line response
  - Trial Balance line response
  - Profit & Loss (PnL) response
  - Balance Sheet response
  - AR / AP aging responses
  - Shared ReportFilterRequest
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared report filter request
# ---------------------------------------------------------------------------

class ReportFilterRequest(BaseModel):
    """
    Common filter parameters accepted by all report endpoints.

    All report endpoints accept at least these fields; individual endpoints
    may extend this class or accept additional query parameters.
    """

    model_config = ConfigDict(from_attributes=True)

    company_id: UUID = Field(..., description="Company UUID to scope the report")
    date_from: date = Field(..., description="Report period start (inclusive)")
    date_to: date = Field(..., description="Report period end (inclusive)")
    currency_code: str = Field(
        default="KWD",
        max_length=3,
        description="ISO 4217 currency for report amounts",
    )


# ---------------------------------------------------------------------------
# General Ledger
# ---------------------------------------------------------------------------

class GLLineResponse(BaseModel):
    """A single line in the General Ledger report."""

    model_config = ConfigDict(from_attributes=True)

    date: date = Field(..., description="Accounting date of the journal entry")
    journal_entry_id: UUID = Field(..., description="UUID of the parent journal entry")
    account_code: str = Field(..., description="Chart-of-account code")
    account_name: str = Field(..., description="Chart-of-account name")
    partner_name: str | None = Field(
        default=None,
        description="Partner name when the line is linked to a partner",
    )
    description: str | None = Field(
        default=None,
        description="Line narration or reference",
    )
    debit: Decimal = Field(..., description="Debit amount for this line")
    credit: Decimal = Field(..., description="Credit amount for this line")
    balance: Decimal = Field(
        ...,
        description="Running (cumulative) balance up to and including this line",
    )
    currency_code: str = Field(..., description="Currency of the amounts")


# ---------------------------------------------------------------------------
# Trial Balance
# ---------------------------------------------------------------------------

class TrialBalanceLineResponse(BaseModel):
    """A single account row in the Trial Balance report."""

    model_config = ConfigDict(from_attributes=True)

    account_code: str
    account_name: str
    account_type: str = Field(..., description="Account type: asset, liability, equity, revenue, expense")
    # Opening balances (before date_from)
    opening_debit: Decimal = Field(..., description="Cumulative debit balance before the period")
    opening_credit: Decimal = Field(..., description="Cumulative credit balance before the period")
    # Period movements
    period_debit: Decimal = Field(..., description="Total debits posted within the period")
    period_credit: Decimal = Field(..., description="Total credits posted within the period")
    # Closing balances (opening + period movements)
    closing_debit: Decimal = Field(..., description="Net closing debit balance")
    closing_credit: Decimal = Field(..., description="Net closing credit balance")


class TrialBalanceResponse(BaseModel):
    """Complete Trial Balance report."""

    model_config = ConfigDict(from_attributes=True)

    company_id: UUID
    date_from: date
    date_to: date
    currency_code: str
    lines: list[TrialBalanceLineResponse] = Field(default_factory=list)
    # Totals
    total_opening_debit: Decimal
    total_opening_credit: Decimal
    total_period_debit: Decimal
    total_period_credit: Decimal
    total_closing_debit: Decimal
    total_closing_credit: Decimal
    generated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="UTC timestamp when the report was generated",
    )


# ---------------------------------------------------------------------------
# Profit & Loss
# ---------------------------------------------------------------------------

class PnLLineResponse(BaseModel):
    """A single account line within a P&L section."""

    model_config = ConfigDict(from_attributes=True)

    account_code: str
    account_name: str
    amount: Decimal = Field(..., description="Net amount for the period (positive = normal balance)")


class PnLSectionResponse(BaseModel):
    """A logical section of the P&L (e.g. 'Revenue', 'Cost of Goods Sold')."""

    model_config = ConfigDict(from_attributes=True)

    label: str = Field(..., description="Display label for the section")
    lines: list[PnLLineResponse] = Field(default_factory=list)
    total: Decimal = Field(..., description="Sum of all line amounts in this section")


class PnLResponse(BaseModel):
    """Complete Profit & Loss (Income Statement) report."""

    model_config = ConfigDict(from_attributes=True)

    company_id: UUID
    date_from: date
    date_to: date
    currency_code: str
    revenue_sections: list[PnLSectionResponse] = Field(
        default_factory=list,
        description="Revenue / income account sections",
    )
    expense_sections: list[PnLSectionResponse] = Field(
        default_factory=list,
        description="Expense / cost account sections",
    )
    total_revenue: Decimal = Field(..., description="Sum of all revenue section totals")
    total_cost_of_goods_sold: Decimal = Field(
        default=Decimal("0"),
        description="Cost of goods sold total (subset of expenses)",
    )
    gross_profit: Decimal = Field(
        ...,
        description="total_revenue − total_cost_of_goods_sold",
    )
    total_operating_expenses: Decimal = Field(
        default=Decimal("0"),
        description="Operating expense total (excluding COGS)",
    )
    net_income: Decimal = Field(
        ...,
        description="gross_profit − total_operating_expenses",
    )
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Balance Sheet
# ---------------------------------------------------------------------------

class BalanceSheetLineResponse(BaseModel):
    """A single account line within a Balance Sheet section."""

    model_config = ConfigDict(from_attributes=True)

    account_code: str
    account_name: str
    amount: Decimal


class BalanceSheetSectionResponse(BaseModel):
    """A logical section of the Balance Sheet (e.g. 'Current Assets')."""

    model_config = ConfigDict(from_attributes=True)

    label: str
    lines: list[BalanceSheetLineResponse] = Field(default_factory=list)
    total: Decimal


class BalanceSheetResponse(BaseModel):
    """Complete Balance Sheet (Statement of Financial Position) report."""

    model_config = ConfigDict(from_attributes=True)

    company_id: UUID
    as_of_date: date = Field(..., description="Balance sheet date (end of period)")
    currency_code: str
    # Assets
    asset_sections: list[BalanceSheetSectionResponse] = Field(
        default_factory=list,
        description="Asset sections (current assets, non-current assets, etc.)",
    )
    total_assets: Decimal
    # Liabilities
    liability_sections: list[BalanceSheetSectionResponse] = Field(
        default_factory=list,
        description="Liability sections (current liabilities, long-term liabilities, etc.)",
    )
    total_liabilities: Decimal
    # Equity
    equity_sections: list[BalanceSheetSectionResponse] = Field(
        default_factory=list,
        description="Equity sections (share capital, retained earnings, etc.)",
    )
    total_equity: Decimal
    total_liabilities_and_equity: Decimal = Field(
        ...,
        description="total_liabilities + total_equity — must equal total_assets",
    )
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# AR / AP Aging
# ---------------------------------------------------------------------------

class AgingBucket(BaseModel):
    """A single aging bucket (time band) in an aging report."""

    model_config = ConfigDict(from_attributes=True)

    bucket_label: str = Field(
        ...,
        description="Human-readable label for the bucket, e.g. '0-30 days', '31-60 days', 'Over 90 days'",
    )
    amount: Decimal = Field(
        ...,
        description="Outstanding balance that falls within this aging bucket",
    )


class AgingPartnerEntry(BaseModel):
    """Aging breakdown for a single partner."""

    model_config = ConfigDict(from_attributes=True)

    partner_id: UUID
    partner_name: str
    total_outstanding: Decimal = Field(
        ...,
        description="Total amount outstanding across all buckets",
    )
    buckets: list[AgingBucket] = Field(
        default_factory=list,
        description="Ordered list of aging buckets for this partner",
    )


class ARAgingResponse(BaseModel):
    """
    Accounts Receivable aging report.

    Lists customer balances segmented by overdue time bands.
    """

    model_config = ConfigDict(from_attributes=True)

    company_id: UUID
    as_of_date: date
    currency_code: str
    partners: list[AgingPartnerEntry] = Field(default_factory=list)
    # Report-level totals per bucket
    bucket_totals: list[AgingBucket] = Field(
        default_factory=list,
        description="Aggregated totals across all partners for each bucket",
    )
    grand_total: Decimal = Field(..., description="Total AR outstanding")
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class APAgingResponse(BaseModel):
    """
    Accounts Payable aging report.

    Lists vendor balances segmented by overdue time bands.
    """

    model_config = ConfigDict(from_attributes=True)

    company_id: UUID
    as_of_date: date
    currency_code: str
    partners: list[AgingPartnerEntry] = Field(default_factory=list)
    # Report-level totals per bucket
    bucket_totals: list[AgingBucket] = Field(
        default_factory=list,
        description="Aggregated totals across all partners for each bucket",
    )
    grand_total: Decimal = Field(..., description="Total AP outstanding")
    generated_at: datetime = Field(default_factory=datetime.utcnow)
