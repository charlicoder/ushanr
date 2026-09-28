"""
app/models/journal.py
──────────────────────
Journal model — defines a journal (book) for recording entries.
Preserves and extends the original `journals` table.

Each journal has a type that controls which kinds of entries it accepts:
  - SALE: Customer invoices
  - PURCHASE: Vendor bills
  - CASH: Cash payments
  - BANK: Bank transactions
  - GENERAL: Manual journal entries, opening balances, etc.
  - MISC: Miscellaneous
"""
from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CompanyMixin, TimestampMixin, UUIDPrimaryKeyMixin


class JournalType(str, Enum):
    SALE = "sale"
    PURCHASE = "purchase"
    CASH = "cash"
    BANK = "bank"
    GENERAL = "general"
    MISC = "misc"


class Journal(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """
    A journal (book of original entry) for double-entry bookkeeping.
    Preserves the original `journals` table with improvements.
    """
    __tablename__ = "journals"

    # Core fields (preserved from original)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    journal_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Default accounts for this journal
    default_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    suspense_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    payment_debit_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    payment_credit_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Document sequence
    sequence_prefix: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sequence_padding: Mapped[int] = mapped_column(default=4, nullable=False)

    # Currency
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # Permissions / settings
    restrict_mode_hash_table: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    show_on_dashboard: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence: Mapped[int] = mapped_column(default=10, nullable=False)

    def __repr__(self) -> str:
        return f"<Journal {self.code} ({self.journal_type})>"
