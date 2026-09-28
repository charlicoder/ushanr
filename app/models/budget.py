"""
app/models/budget.py
─────────────────────
Budget and budget line models.
"""
from __future__ import annotations

import uuid
from datetime import date
from enum import Enum

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CompanyMixin, TimestampMixin, UUIDPrimaryKeyMixin


class BudgetState(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class Budget(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    __tablename__ = "budgets"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    fiscal_year_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fiscal_years.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=BudgetState.DRAFT.value
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    lines: Mapped[list[BudgetLine]] = relationship(
        "BudgetLine", back_populates="budget", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Budget {self.name!r} [{self.state}]>"


class BudgetLine(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "budget_lines"

    budget_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("budgets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    analytic_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytic_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    planned_amount: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")

    budget: Mapped[Budget] = relationship("Budget", back_populates="lines")

    def __repr__(self) -> str:
        return f"<BudgetLine acct={self.account_id} {self.planned_amount}>"
