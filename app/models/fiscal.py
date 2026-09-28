"""
app/models/fiscal.py
─────────────────────
Fiscal year and accounting period models.
Fiscal periods control which dates entries can be posted to.
"""
from __future__ import annotations

import uuid
from datetime import date
from enum import Enum

from sqlalchemy import Boolean, Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CompanyMixin, TimestampMixin, UUIDPrimaryKeyMixin


class FiscalYearState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    LOCKED = "locked"


class PeriodState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    LOCKED = "locked"


class FiscalYear(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """
    A fiscal (accounting) year.
    Entries can only be posted to open fiscal years.
    """
    __tablename__ = "fiscal_years"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_fiscal_year_company_name"),
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FiscalYearState.OPEN.value, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Closing journal entry
    closing_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<FiscalYear {self.name} {self.date_from}–{self.date_to} [{self.state}]>"


class AccountingPeriod(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """
    A monthly (or custom) accounting period within a fiscal year.
    """
    __tablename__ = "accounting_periods"
    __table_args__ = (
        UniqueConstraint("company_id", "date_from", "date_to", name="uq_period_dates"),
    )

    fiscal_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fiscal_years.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    date_to: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PeriodState.OPEN.value, index=True
    )

    def __repr__(self) -> str:
        return f"<AccountingPeriod {self.name} [{self.state}]>"
