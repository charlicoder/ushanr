"""
app/models/journal_entry.py
────────────────────────────
Journal Entry and Journal Item (line) models.

This is the CORE of the double-entry bookkeeping system.

Design principles:
- Every JournalEntry is either DRAFT or POSTED (or CANCELLED).
- Posted entries are IMMUTABLE. Corrections use reversal entries.
- Every JournalEntry must have balanced journal items (sum(debit) == sum(credit)).
- JournalItem.debit_amount and JournalItem.credit_amount are both stored (never negative).
- The `amount_currency` field stores the original-currency amount for multi-currency support.
- Accounting role note: A JournalEntry is an immutable General Ledger transaction and
  never has a 'paid' state. In double-entry bookkeeping, an invoice document can be 'paid',
  while its linked journal entry remains 'posted' to the general ledger.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CompanyMixin, TimestampMixin, UUIDPrimaryKeyMixin


class EntryState(str, Enum):
    DRAFT = "draft"
    POSTED = "posted"
    CANCELLED = "cancelled"


class JournalEntry(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """
    A journal entry (voucher) — a set of balanced journal items.
    Preserves and extends the original `journal_entries` table.
    """
    __tablename__ = "journal_entries"

    # Reference fields
    name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)  # e.g. INV/2024/001
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    narration: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Journal
    journal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journals.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Partner (optional — for receivable/payable entries)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partners.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Dates
    entry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    accounting_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Fiscal period (populated on post)
    period_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounting_periods.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # State
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=EntryState.DRAFT.value, index=True
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Totals (denormalised for performance)
    amount_total: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")

    # Reversal linkage
    reversed_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journal_entries.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    is_reversal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Source document tracking
    source_document_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Invoice number (e.g. INV/2026/10/00001) of the invoice this entry belongs to
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # Relationships
    items: Mapped[list[JournalItem]] = relationship(
        "JournalItem", back_populates="entry", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<JournalEntry {self.name or self.id} [{self.state}]>"

    @property
    def is_posted(self) -> bool:
        return self.state == EntryState.POSTED.value

    @property
    def is_editable(self) -> bool:
        return self.state == EntryState.DRAFT.value


class JournalItem(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """
    A single line in a journal entry (debit or credit side).
    Preserves and extends the original `journal_items` table.

    Both debit_amount and credit_amount are stored (one will be 0 for each line).
    This avoids sign confusion and simplifies balance queries.
    """
    __tablename__ = "journal_items"

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journal_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partners.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    analytic_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytic_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Amounts — always non-negative; exactly one of debit/credit will be > 0
    debit_amount: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    credit_amount: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)

    # Multi-currency
    amount_currency: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")
    currency_rate: Mapped[float | None] = mapped_column(Numeric(20, 10), nullable=True)

    # Reconciliation
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    reconcile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Display
    name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sequence: Mapped[int] = mapped_column(default=10, nullable=False)
    invoice_number: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # Dates
    date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Relationships
    entry: Mapped[JournalEntry] = relationship("JournalEntry", back_populates="items")

    def __repr__(self) -> str:
        dr = f"Dr {self.debit_amount}" if self.debit_amount else ""
        cr = f"Cr {self.credit_amount}" if self.credit_amount else ""
        return f"<JournalItem entry={self.entry_id} account={self.account_id} {dr}{cr}>"
