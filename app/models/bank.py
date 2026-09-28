"""
app/models/bank.py
───────────────────
Bank account, bank statement, and bank statement line models.
"""
from __future__ import annotations

import uuid
from datetime import date
from enum import Enum

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CompanyMixin, TimestampMixin, UUIDPrimaryKeyMixin


class BankAccountType(str, Enum):
    BANK = "bank"
    CASH = "cash"


class BankStatementState(str, Enum):
    OPEN = "open"
    RECONCILED = "reconciled"
    CANCELLED = "cancelled"


class BankAccount(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """A bank or cash account linked to a GL account."""
    __tablename__ = "bank_accounts"

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    journal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journals.id", ondelete="SET NULL"),
        nullable=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bank_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")
    account_type: Mapped[str] = mapped_column(
        String(10), nullable=False, default=BankAccountType.BANK.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    statements: Mapped[list[BankStatement]] = relationship(
        "BankStatement", back_populates="bank_account"
    )

    def __repr__(self) -> str:
        return f"<BankAccount {self.name!r}>"


class BankStatement(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """An imported bank statement for a given period."""
    __tablename__ = "bank_statements"

    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bank_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    balance_start: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    balance_end: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=BankStatementState.OPEN.value
    )

    bank_account: Mapped[BankAccount] = relationship("BankAccount", back_populates="statements")
    lines: Mapped[list[BankStatementLine]] = relationship(
        "BankStatementLine", back_populates="statement", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<BankStatement {self.name!r} {self.date_from}–{self.date_to}>"


class BankStatementLine(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single line from a bank statement."""
    __tablename__ = "bank_statement_lines"

    statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bank_statements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journal_entries.id", ondelete="SET NULL"),
        nullable=True,
    )

    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")
    is_reconciled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Raw import data
    raw_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    statement: Mapped[BankStatement] = relationship("BankStatement", back_populates="lines")

    def __repr__(self) -> str:
        return f"<BankStatementLine {self.date} {self.amount} {self.name!r}>"
