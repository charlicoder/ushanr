"""
app/models/sequence.py
───────────────────────
Document sequence / auto-numbering for invoices, journal entries, etc.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import CompanyMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.utils.timezone import local_now


class DocumentSequence(UUIDPrimaryKeyMixin, CompanyMixin, TimestampMixin, Base):
    """
    Auto-incrementing document number sequence per company/journal/prefix.
    Example: prefix="INV", padding=4, next_number=42 → "INV/2024/0042"
    """
    __tablename__ = "document_sequences"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    prefix: Mapped[str | None] = mapped_column(String(20), nullable=True)
    suffix: Mapped[str | None] = mapped_column(String(20), nullable=True)
    next_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    step: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    padding: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    use_date_range: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<DocumentSequence {self.code} next={self.next_number}>"

    def get_next_value(self) -> str:
        """
        Returns the next formatted sequence value.
        IMPORTANT: Caller must increment next_number and persist within a transaction.
        """
        from datetime import datetime
        number_str = str(self.next_number).zfill(self.padding)
        parts = []
        if self.prefix:
            parts.append(self.prefix)
        if self.use_date_range:
            parts.append(str(local_now().year))
        parts.append(number_str)
        if self.suffix:
            parts.append(self.suffix)
        return "/".join(parts)
