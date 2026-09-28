"""
app/models/payment.py
──────────────────────
Payment and allocation models.

A Payment represents money received from a customer or paid to a vendor.
PaymentAllocation links a payment to an invoice (partial or full).

Payment types:
  - INBOUND:  Money received (customer payment)
  - OUTBOUND: Money sent (vendor payment)

State machine:
  draft → posted → reconciled | cancelled
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CompanyMixin, TimestampMixin, UUIDPrimaryKeyMixin


class PaymentType(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class PaymentMethod(str, Enum):
    CASH = "cash"
    BANK = "bank"
    KNET = "KNET"
    VISA = "VISA"
    MASTERCARD = "MASTERCARD"
    TAP = "TAP"
    MYFATOORAH = "MyFatoorah"
    OTHER = "other"


class PaymentState(str, Enum):
    DRAFT = "draft"
    POSTED = "posted"
    RECONCILED = "reconciled"
    CANCELLED = "cancelled"


class Payment(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """
    A payment transaction — either received from customer or paid to vendor.
    Preserves and extends the original `payments` table.
    """
    __tablename__ = "anr_payments"

    # Reference
    name: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True, index=True)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PaymentType.INBOUND.value, index=True
    )
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PaymentState.DRAFT.value, index=True
    )

    # Partner
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partners.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    partner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Journal
    journal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journals.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Linked journal entry
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("journal_entries.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Date
    payment_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # Amounts
    amount: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False)
    amount_residual: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False, default=0)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")

    # Payment method/provider info
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)
    payment_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # External gateway references
    payment_id_external: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_id_external: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    track_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Raw gateway response
    payment_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Created by
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    allocations: Mapped[list[PaymentAllocation]] = relationship(
        "PaymentAllocation", back_populates="payment", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Payment {self.name or self.id} {self.amount} [{self.state}]>"


class PaymentAllocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Links a payment to an invoice (reconciliation record).
    Supports partial allocations across multiple invoices.
    """
    __tablename__ = "payment_allocations"

    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("anr_payments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount: Mapped[float] = mapped_column(Numeric(20, 3), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")
    allocation_date: Mapped[date] = mapped_column(Date, nullable=False)

    payment: Mapped[Payment] = relationship("Payment", back_populates="allocations")

    def __repr__(self) -> str:
        return f"<PaymentAllocation pmt={self.payment_id} inv={self.invoice_id} {self.amount}>"
