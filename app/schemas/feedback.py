"""
app/schemas/feedback.py
───────────────────────
Request / response models for the feedback API.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


# ── Requests ──────────────────────────────────────────────────────────────────


class CreateFeedbackLinkRequest(BaseModel):
    """Booking snapshot sent by ushdesk when staff generate a feedback link."""

    booking_id: UUID
    booking_number: str | None = None
    company_id: UUID | None = None

    customer_id: UUID | None = None
    customer_name: str | None = None
    customer_phone: str | None = None

    service_id: UUID | None = None
    service_name: str | None = None
    services: list[dict[str, Any]] | None = None

    therapist_id: UUID | None = None
    therapist_name: str | None = None
    secondary_therapist_id: UUID | None = None
    secondary_therapist_name: str | None = None

    branch_id: UUID | None = None
    branch_name: str | None = None

    appointment_start: datetime | None = None
    language: str = "ar"

    model_config = ConfigDict(extra="ignore")

    @field_validator(
        "company_id", "customer_id", "service_id", "therapist_id",
        "secondary_therapist_id", "branch_id", "appointment_start",
        "booking_number", "customer_name", "customer_phone", "service_name",
        "therapist_name", "secondary_therapist_name", "branch_name",
        mode="before",
    )
    @classmethod
    def _blank(cls, v: Any) -> Any:
        return _blank_to_none(v)

    @field_validator("language", mode="before")
    @classmethod
    def _language(cls, v: Any) -> str:
        return "en" if str(v or "").strip().lower().startswith("en") else "ar"


class SubmitFeedbackRequest(BaseModel):
    service_rating: int = Field(ge=1, le=5)
    therapist_rating: int = Field(ge=1, le=5)
    arrangement_rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)

    @field_validator("comment", mode="before")
    @classmethod
    def _comment(cls, v: Any) -> Any:
        v = _blank_to_none(v)
        return v.strip() if isinstance(v, str) else v


# ── Responses ─────────────────────────────────────────────────────────────────


class FeedbackLinkResponse(BaseModel):
    id: UUID
    public_token: str
    feedback_url: str
    status: str
    created: bool
    booking_id: UUID
    submitted_at: datetime | None = None


class PublicFeedbackView(BaseModel):
    """What the customer-facing page may see. No phone / ids."""

    status: str
    language: str
    customer_first_name: str | None = None
    branch_name: str | None = None
    service_name: str | None = None
    therapist_name: str | None = None
    secondary_therapist_name: str | None = None
    appointment_start: datetime | None = None


class FeedbackItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    public_token: str
    status: str
    booking_id: UUID
    booking_number: str | None = None
    customer_id: UUID | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    service_id: UUID | None = None
    service_name: str | None = None
    services: list[dict[str, Any]] | None = None
    therapist_id: UUID | None = None
    therapist_name: str | None = None
    secondary_therapist_id: UUID | None = None
    secondary_therapist_name: str | None = None
    branch_id: UUID | None = None
    branch_name: str | None = None
    appointment_start: datetime | None = None
    language: str
    service_rating: int | None = None
    therapist_rating: int | None = None
    arrangement_rating: int | None = None
    average_rating: float | None = None
    comment: str | None = None
    created_by: str | None = None
    created_at: datetime
    submitted_at: datetime | None = None
    feedback_url: str | None = None


class FeedbackListResponse(BaseModel):
    items: list[FeedbackItem]
    total: int
    page: int
    page_size: int
    pages: int


class RatingAverages(BaseModel):
    service: float | None = None
    therapist: float | None = None
    arrangement: float | None = None
    overall: float | None = None


class FeedbackGroupSummary(BaseModel):
    id: str | None = None
    name: str | None = None
    count: int
    averages: RatingAverages


class FeedbackSummary(BaseModel):
    total_links: int
    submitted: int
    pending: int
    response_rate: float
    averages: RatingAverages
    # question -> {"1": n, ..., "5": n}
    distribution: dict[str, dict[str, int]]
    by_branch: list[FeedbackGroupSummary]
    by_therapist: list[FeedbackGroupSummary]
    by_service: list[FeedbackGroupSummary]

