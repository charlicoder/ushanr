"""
app/models/invoice.py
──────────────────────
Invoice and Bill models (Accounts Receivable / Accounts Payable).

Invoice types:
  - INVOICE:       Customer invoice (AR)
  - CREDIT_NOTE:   Customer credit note (reverse of invoice)
  - BILL:          Vendor bill (AP)
  - VENDOR_CREDIT: Vendor credit note (reverse of bill)

State machine:
  draft → confirmed → posted → (paid | partial | cancelled)
  posted entries create journal entries automatically
"""
from __future__ import annotations

import uuid
from datetime import date
from enum import Enum

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CompanyMixin, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin


class InvoiceType(str, Enum):
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    BILL = "bill"
    VENDOR_CREDIT = "vendor_credit"


class InvoiceState(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    POSTED = "posted"
    PAID = "paid"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


class PaymentTerms(str, Enum):
    IMMEDIATE = "immediate"
    NET_30 = "net_30"
    NET_60 = "net_60"
    NET_90 = "net_90"
    CUSTOM = "custom"


class Invoice(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """
    Customer invoice, vendor bill, or credit note.
    """
    __tablename__ = "invoices"

    # Document identification
    name: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True, index=True)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=InvoiceState.DRAFT.value, index=True
    )

    # Partner
    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partners.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Journal
    journal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journals.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Linked journal entry (created when posted)
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journal_entries.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Dates
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    accounting_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Payment terms
    payment_terms: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PaymentTerms.IMMEDIATE.value
    )
    payment_terms_days: Mapped[int] = mapped_column(default=0, nullable=False)

    # Currency
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")

    # Amounts (computed from lines)
    amount_untaxed: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    amount_tax: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    amount_total: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    amount_paid: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    amount_residual: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)

    # Reversal linkage
    reversed_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", use_alter=True, ondelete="SET NULL"),
        nullable=True,
    )
    is_reversal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Source tracking (e.g. ushbooknpay order)
    source_document_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_document_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_document_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    lines: Mapped[list[InvoiceLine]] = relationship(
        "InvoiceLine", back_populates="invoice", cascade="all, delete-orphan", lazy="selectin"
    )
    taxes: Mapped[list[InvoiceTax]] = relationship(
        "InvoiceTax", back_populates="invoice", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Invoice {self.name or self.id} [{self.invoice_type}] [{self.state}]>"


class InvoiceLine(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """A single line item on an invoice or bill."""
    __tablename__ = "invoice_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    analytic_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytic_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    tax_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("taxes.id", ondelete="SET NULL"),
        nullable=True,
    )

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    quantity: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False, default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    discount: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    tax_rate: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)

    subtotal: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    total: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)

    # External product reference
    product_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    sequence: Mapped[int] = mapped_column(default=10, nullable=False)

    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="lines")

    def __repr__(self) -> str:
        return f"<InvoiceLine {self.name!r} qty={self.quantity} price={self.unit_price}>"


class InvoiceTax(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Aggregated tax breakdown per invoice (per tax)."""
    __tablename__ = "invoice_taxes"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tax_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("taxes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tax_name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_amount: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)

    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="taxes")

    def __repr__(self) -> str:
        return f"<InvoiceTax {self.tax_name} {self.tax_amount}>"
