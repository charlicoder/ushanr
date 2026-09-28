"""
app/models/currency.py
───────────────────────
Currency and exchange rate models.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Currency(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "currencies"

    code: Mapped[str] = mapped_column(String(3), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(10), nullable=True)
    decimal_places: Mapped[int] = mapped_column(default=3, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_base: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:
        return f"<Currency {self.code}>"


class CurrencyRate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Daily exchange rate: 1 unit of `currency_code` = `rate` units of base currency."""
    __tablename__ = "currency_rates"
    __table_args__ = (
        UniqueConstraint("currency_code", "rate_date", name="uq_currency_rate_date"),
    )

    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    rate_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    rate: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
    inverse_rate: Mapped[float | None] = mapped_column(Numeric(20, 10), nullable=True)

    def __repr__(self) -> str:
        return f"<CurrencyRate {self.currency_code} {self.rate_date} = {self.rate}>"
