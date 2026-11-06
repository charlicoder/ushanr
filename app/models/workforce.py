"""
app/models/workforce.py
────────────────────────
Workforce (therapist) history & analytics tables.

All tables are append-only / replace-per-day facts consumed by reporting:

* ``wf_schedule_change_log``        – audit trail of every working-hours / leave /
                                      extra-hours / therapist edit made in ushauth.
* ``wf_therapist_daily_roster``     – the resolved schedule of a therapist for one
                                      Asia/Kuwait local date (shift, break, leave, extra).
* ``wf_completed_session``          – COMPLETED booking sessions only (actual work).
* ``wf_therapist_activity_segment`` – per-day timeline: service / idle / break / leave / off.
* ``wf_therapist_daily_summary``    – per-day minutes rollup (scheduled, worked, idle, ...).
* ``wf_processed_event``            – consumer idempotency ledger.

IDs of therapists / employees / bookings are UUIDs owned by other services, so there
are intentionally no foreign keys to them.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class SegmentType:
    SERVICE = "service"
    IDLE = "idle"
    BREAK = "break"
    LEAVE = "leave"
    OFF = "off"


class ScheduleChangeLog(UUIDPrimaryKeyMixin, Base):
    """One row per schedule-affecting edit made in ushauth (append-only)."""

    __tablename__ = "wf_schedule_change_log"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_wf_change_log_event_id"),
        Index("ix_wf_change_log_therapist_time", "therapist_id", "occurred_at"),
        Index("ix_wf_change_log_employee_time", "employee_id", "occurred_at"),
        Index("ix_wf_change_log_entity", "entity_type", "entity_id"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    therapist_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # created|updated|deleted
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    changed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TherapistDailyRoster(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Resolved schedule of one therapist for one Asia/Kuwait local date."""

    __tablename__ = "wf_therapist_daily_roster"
    __table_args__ = (
        UniqueConstraint("therapist_id", "local_date", name="uq_wf_roster_therapist_date"),
        Index("ix_wf_roster_date", "local_date"),
        Index("ix_wf_roster_branch_date", "branch_id", "local_date"),
    )

    therapist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    therapist_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    therapist_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)

    is_day_off: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shift_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    shift_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    break_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    break_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    break_duration_config: Mapped[int | None] = mapped_column(Integer, nullable=True)
    working_hours_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    leaves: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    extra_segments: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    scheduled_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    leave_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extra_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # recorded = snapshot taken by the daily job; session_only = therapist had completed
    # work but was missing from the ushauth roster; reconstructed = backfilled.
    data_quality: Mapped[str] = mapped_column(String(16), nullable=False, default="recorded")
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CompletedSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A COMPLETED booking session served by a therapist (primary or secondary)."""

    __tablename__ = "wf_completed_session"
    __table_args__ = (
        UniqueConstraint("session_id", "therapist_id", name="uq_wf_completed_session_therapist"),
        Index("ix_wf_completed_session_therapist_start", "therapist_id", "start_at"),
        Index("ix_wf_completed_session_date", "local_date"),
        Index("ix_wf_completed_session_booking", "booking_id"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    booking_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    therapist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(12), nullable=False, default="primary")
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    booking_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    services: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    extra_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    addons_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TherapistActivitySegment(UUIDPrimaryKeyMixin, Base):
    """Ordered timeline entry of a therapist's day."""

    __tablename__ = "wf_therapist_activity_segment"
    __table_args__ = (
        Index("ix_wf_segment_therapist_date", "therapist_id", "local_date", "start_at"),
        Index("ix_wf_segment_date_type", "local_date", "segment_type"),
    )

    therapist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    segment_type: Mapped[str] = mapped_column(String(12), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    is_overtime: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    booking_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    service_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    service_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TherapistDailySummary(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per therapist per day minutes rollup (basis of weekly / monthly reports)."""

    __tablename__ = "wf_therapist_daily_summary"
    __table_args__ = (
        UniqueConstraint("therapist_id", "local_date", name="uq_wf_summary_therapist_date"),
        Index("ix_wf_summary_date", "local_date"),
        Index("ix_wf_summary_branch_date", "branch_id", "local_date"),
    )

    therapist_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    therapist_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)

    scheduled_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    leave_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extra_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worked_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idle_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    overtime_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sessions_completed: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    utilization_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    is_day_off: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    on_leave: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProcessedEvent(Base):
    """Consumer idempotency ledger (purged after 30 days)."""

    __tablename__ = "wf_processed_event"
    __table_args__ = (Index("ix_wf_processed_event_at", "processed_at"),)

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
