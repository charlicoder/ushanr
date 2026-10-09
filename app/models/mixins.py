"""
app/models/mixins.py
─────────────────────
Reusable SQLAlchemy column mixins.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

_KUWAIT_TZ = ZoneInfo("Asia/Kuwait")


def _now_kuwait() -> datetime:
    """Asia/Kuwait wall-clock time labelled UTC (platform-wide convention, same as appointment_start)."""
    return datetime.now(timezone.utc)


class UUIDPrimaryKeyMixin:
    """UUID primary key, auto-generated."""
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )


class TimestampMixin:
    """Created/updated timestamps, auto-managed in Asia/Kuwait timezone."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_now_kuwait,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_now_kuwait,
        onupdate=_now_kuwait,
        nullable=False,
    )


class SoftDeleteMixin:
    """Soft-delete support."""
    is_deleted: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CompanyMixin:
    """All accounting records belong to a company."""
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
