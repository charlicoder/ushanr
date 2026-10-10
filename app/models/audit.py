"""
app/models/audit.py
────────────────────
Audit trail model — immutable log of all significant state changes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

_KUWAIT_TZ = ZoneInfo("Asia/Kuwait")


def _now_kuwait() -> datetime:
    """Current instant as a timezone-aware UTC datetime (stored as timestamptz; APIs render it in Asia/Kuwait)."""
    return datetime.now(timezone.utc)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=_now_kuwait, nullable=False, index=True
    )

    # Who
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # What
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # Details
    old_values: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_values: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Company context
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} {self.resource_type}/{self.resource_id}>"
