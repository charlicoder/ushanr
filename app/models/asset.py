"""
app/models/asset.py
───────────────────
Fixed Asset and Asset Depreciation models.
Mirrors the structure in data/account.asset.xlsx and integrates with journal entries.
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


class AssetStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    CLOSE = "close"
    CANCELLED = "cancelled"


class Asset(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """
    Fixed Asset entity (e.g. Curtains, Furniture, Vehicles, Equipment).
    """
    __tablename__ = "assets"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    purchase_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    asset_value: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False)
    book_value: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False)
    salvage_value: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False, default=0.0)

    # Linked GL Accounts
    fixed_asset_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    depreciation_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    expense_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    depreciation_model: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g. "36 Month Linear , 1% salvage"
    method_number: Mapped[int] = mapped_column(default=36, nullable=False)  # Number of periods
    method_period: Mapped[int] = mapped_column(default=1, nullable=False)  # In months
    asset_group: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=AssetStatus.RUNNING.value, index=True
    )
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partners.id", ondelete="SET NULL"),
        nullable=True,
    )

    depreciation_lines: Mapped[list[AssetDepreciationLine]] = relationship(
        "AssetDepreciationLine", back_populates="asset", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Asset {self.name!r} value={self.asset_value} book_val={self.book_value}>"


class AssetDepreciationLine(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """
    Individual depreciation line / schedule for an asset.
    """
    __tablename__ = "asset_depreciation_lines"

    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    depreciation_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False)
    remaining_value: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False)
    depreciated_value: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False)
    is_posted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journal_entries.id", ondelete="SET NULL"),
        nullable=True,
    )

    asset: Mapped[Asset] = relationship("Asset", back_populates="depreciation_lines")

    def __repr__(self) -> str:
        return f"<AssetDepreciationLine date={self.depreciation_date} amount={self.amount}>"
