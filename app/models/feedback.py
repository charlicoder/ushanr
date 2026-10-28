"""
app/models/feedback.py
──────────────────────
Customer feedback for a completed booking.

One row per booking (unique ``booking_id``). The row is created as ``pending``
when staff generate the feedback link and becomes ``submitted`` once the
customer rates the visit.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import CheckConstraint, DateTime, Index, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class FeedbackStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"


class Feedback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "feedbacks"
    __table_args__ = (
        CheckConstraint("service_rating IS NULL OR service_rating BETWEEN 1 AND 5", name="ck_feedbacks_service_rating"),
        CheckConstraint("therapist_rating IS NULL OR therapist_rating BETWEEN 1 AND 5", name="ck_feedbacks_therapist_rating"),
        CheckConstraint(
            "arrangement_rating IS NULL OR arrangement_rating BETWEEN 1 AND 5",
            name="ck_feedbacks_arrangement_rating",
        ),
        CheckConstraint(
            "status <> 'submitted' OR (service_rating IS NOT NULL AND therapist_rating IS NOT NULL "
            "AND arrangement_rating IS NOT NULL)",
            name="ck_feedbacks_submitted_complete",
        ),
        Index("ix_feedbacks_status_submitted_at", "status", "submitted_at"),
    )

    public_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    booking_number: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    # Customer snapshot
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Service snapshot (primary + full list for multi-service bookings)
    service_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    service_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    services: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Therapist snapshot (secondary used for couple / double rooms)
    therapist_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    therapist_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secondary_therapist_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    secondary_therapist_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Branch snapshot
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    branch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    appointment_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    language: Mapped[str] = mapped_column(String(5), nullable=False, default="ar")

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FeedbackStatus.PENDING.value, index=True
    )
    service_rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    therapist_rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    arrangement_rating: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
