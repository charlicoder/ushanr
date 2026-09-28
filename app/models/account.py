"""
app/models/account.py
──────────────────────
Chart of Accounts — hierarchical account structure.
Preserves and extends the original `accounts` table.

Account types follow standard double-entry bookkeeping:
  - ASSET      (debit-normal)
  - LIABILITY  (credit-normal)
  - EQUITY     (credit-normal)
  - REVENUE    (credit-normal)
  - EXPENSE    (debit-normal)
  - COGS       (debit-normal)
  - OTHER      (configurable)
"""
from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CompanyMixin, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin


class AccountType(str, Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"
    COGS = "cogs"
    OTHER = "other"


class AccountNature(str, Enum):
    """Debit-normal vs credit-normal for balance calculations."""
    DEBIT = "debit"
    CREDIT = "credit"


# Nature lookup by account type
ACCOUNT_NATURE_MAP: dict[str, str] = {
    AccountType.ASSET.value: AccountNature.DEBIT.value,
    AccountType.COGS.value: AccountNature.DEBIT.value,
    AccountType.EXPENSE.value: AccountNature.DEBIT.value,
    AccountType.LIABILITY.value: AccountNature.CREDIT.value,
    AccountType.EQUITY.value: AccountNature.CREDIT.value,
    AccountType.REVENUE.value: AccountNature.CREDIT.value,
    AccountType.OTHER.value: AccountNature.DEBIT.value,
}


class AccountGroup(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """
    Optional grouping layer above accounts (e.g. 'Current Assets').
    Used for report section grouping.
    """
    __tablename__ = "account_groups"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_account_group_company_code"),
    )

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("account_groups.id", ondelete="SET NULL"),
        nullable=True,
    )
    sequence: Mapped[int] = mapped_column(default=10, nullable=False)

    def __repr__(self) -> str:
        return f"<AccountGroup {self.code} {self.name!r}>"


class Account(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """
    General Ledger Account.

    Design decisions:
    - `code` is unique per company (allows multi-company setup).
    - `account_type` + `account_nature` together determine debit/credit normal balance.
    - `is_reconcilable` marks accounts (receivable/payable) whose items must be reconciled.
    - `deprecated` replaces hard-delete; posted entries on deprecated accounts still exist.
    - Hierarchy: parent_id for sub-account grouping.
    """
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_account_company_code"),
    )

    # Core fields (preserved from original schema)
    code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    account_nature: Mapped[str] = mapped_column(
        String(10), nullable=False, default=AccountNature.DEBIT.value
    )

    # Hierarchy
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("account_groups.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Classification flags
    is_reconcilable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_bank_account: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_reconciliation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Currency
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Display
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence: Mapped[int] = mapped_column(default=10, nullable=False)

    # Opening balance (for migration from existing systems)
    opening_debit: Mapped[float] = mapped_column(Numeric(20, 3), default=0, nullable=False)
    opening_credit: Mapped[float] = mapped_column(Numeric(20, 3), default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<Account {self.code} {self.name!r} ({self.account_type})>"

    @property
    def is_debit_normal(self) -> bool:
        return self.account_nature == AccountNature.DEBIT.value
