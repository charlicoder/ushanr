"""
app/models/company.py
──────────────────────
Company model — top-level tenant for multi-company support.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trade_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="KWD")
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="KW")
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Fiscal settings
    fiscal_year_start_month: Mapped[int] = mapped_column(default=1, nullable=False)  # January
    decimal_places: Mapped[int] = mapped_column(default=3, nullable=False)

    def __repr__(self) -> str:
        return f"<Company id={self.id} name={self.name!r}>"
