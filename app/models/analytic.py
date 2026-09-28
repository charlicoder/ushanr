"""
app/models/analytic.py
───────────────────────
Analytic (cost center) accounting models.
Preserves and extends original analytic_plans, analytic_items tables.
"""
from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CompanyMixin, TimestampMixin, UUIDPrimaryKeyMixin


class AnalyticPlan(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """
    An analytic plan defines a dimension of analysis
    (e.g. 'Projects', 'Departments', 'Cost Centers').
    Preserves original `analytic_plans` table.
    """
    __tablename__ = "analytic_plans"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sequence: Mapped[int] = mapped_column(default=10, nullable=False)

    # Percentage of total cost to allocate to this plan (0-100)
    default_applicability: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=100
    )

    accounts: Mapped[list[AnalyticAccount]] = relationship(
        "AnalyticAccount", back_populates="plan"
    )

    def __repr__(self) -> str:
        return f"<AnalyticPlan {self.name!r}>"


class AnalyticAccount(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """
    An analytic account (cost center / project / department).
    Belongs to an AnalyticPlan.
    """
    __tablename__ = "analytic_accounts"

    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytic_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytic_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partners.id", ondelete="SET NULL"),
        nullable=True,
    )

    code: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    plan: Mapped[AnalyticPlan] = relationship("AnalyticPlan", back_populates="accounts")

    def __repr__(self) -> str:
        return f"<AnalyticAccount {self.code or ''} {self.name!r}>"


class AnalyticItem(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """
    An analytic distribution record linked to a journal item.
    Preserves original `analytic_items` table.
    """
    __tablename__ = "analytic_items"

    analytic_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytic_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    journal_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journal_items.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partners.id", ondelete="SET NULL"),
        nullable=True,
    )

    name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    date: Mapped[uuid.UUID | None] = mapped_column(Date, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=100)

    def __repr__(self) -> str:
        return f"<AnalyticItem acc={self.analytic_account_id} {self.amount}>"
