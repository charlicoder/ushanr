"""
app/models/partner.py
──────────────────────
Partner model — represents customers, vendors, or both.
Preserves the original `partners` table structure while adding
relationship integrity and accounting-specific fields.
"""
from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CompanyMixin, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin


class PartnerType(str, Enum):
    CUSTOMER = "customer"
    VENDOR = "vendor"
    BOTH = "both"
    INTERNAL = "internal"


class Partner(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """
    A business partner (customer, vendor, or both).
    Preserves and extends the original `partners` table.
    """
    __tablename__ = "partners"

    # Identity
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    partner_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PartnerType.CUSTOMER.value, index=True
    )

    # Classification
    is_customer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_vendor: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_company: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Contact
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    mobile: Mapped[str | None] = mapped_column(String(50), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Address
    street: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # Tax / Legal
    tax_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vat_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Payment terms (number of days)
    payment_terms_days: Mapped[int] = mapped_column(default=0, nullable=False)
    credit_limit: Mapped[float | None] = mapped_column(Numeric(20, 3), nullable=True)

    # External reference (link to ushauth user or ushbooknpay customer)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # Receivable / Payable accounts
    receivable_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", use_alter=True), nullable=True
    )
    payable_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", use_alter=True), nullable=True
    )

    # Currency
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<Partner id={self.id} name={self.name!r} type={self.partner_type}>"
