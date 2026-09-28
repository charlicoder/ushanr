"""
app/models/tax.py
──────────────────
Tax configuration models.
Taxes are configurable (not hard-coded), supporting VAT, withholding, custom rates.
"""
from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CompanyMixin, TimestampMixin, UUIDPrimaryKeyMixin


class TaxType(str, Enum):
    SALE = "sale"      # Tax applied on sales
    PURCHASE = "purchase"  # Tax applied on purchases


class TaxComputation(str, Enum):
    PERCENTAGE = "percentage"  # % of base amount
    FIXED = "fixed"            # Fixed amount
    GROUP = "group"            # Group of taxes


class TaxGroup(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    __tablename__ = "tax_groups"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence: Mapped[int] = mapped_column(default=10, nullable=False)

    def __repr__(self) -> str:
        return f"<TaxGroup {self.name!r}>"


class Tax(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    __tablename__ = "taxes"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tax_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    computation: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaxComputation.PERCENTAGE.value
    )
    amount: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)

    # Accounts
    tax_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    tax_refund_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax_groups.id", ondelete="SET NULL"),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    include_in_price: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sequence: Mapped[int] = mapped_column(default=10, nullable=False)

    def __repr__(self) -> str:
        return f"<Tax {self.name!r} {self.amount}%>"
