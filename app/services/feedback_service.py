"""
app/services/feedback_service.py
────────────────────────────────
Business rules for booking feedback.

SOLID notes
-----------
* S — token generation, URL building and persistence are separate collaborators;
  the service only orchestrates the feedback rules.
* O — new token strategies / URL schemes plug in without touching the service.
* D — the service depends on :class:`FeedbackRepositoryProtocol`,
  :class:`TokenGenerator` and :class:`FeedbackUrlBuilder`, injected by the API layer.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import FeedbackAlreadySubmittedError, FeedbackNotFoundError
from app.core.logging import get_logger
from app.models.feedback import Feedback, FeedbackStatus
from app.repositories.feedback_repository import FeedbackFilters, FeedbackRepositoryProtocol
from app.schemas.feedback import (
    CreateFeedbackLinkRequest,
    FeedbackGroupSummary,
    FeedbackItem,
    FeedbackLinkResponse,
    FeedbackSummary,
    PublicFeedbackView,
    RatingAverages,
    SubmitFeedbackRequest,
)

logger = get_logger(__name__)

_MAX_TOKEN_ATTEMPTS = 5


class TokenGenerator(Protocol):
    def generate(self) -> str: ...


class SecureTokenGenerator:
    """Unguessable URL-safe token (16 random bytes → 22 chars)."""

    def generate(self) -> str:
        return secrets.token_urlsafe(16)


class FeedbackUrlBuilder:
    """Builds the public link: ``{base_url}/{public_token}``."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def build(self, token: str) -> str:
        return f"{self._base_url}/{token}"


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def _mean(values: list[float | None]) -> float | None:
    present = [float(v) for v in values if v is not None]
    return _round(sum(present) / len(present)) if present else None


def _averages(service: Any, therapist: Any, arrangement: Any) -> RatingAverages:
    return RatingAverages(
        service=_round(service),
        therapist=_round(therapist),
        arrangement=_round(arrangement),
        overall=_mean([service, therapist, arrangement]),
    )


class FeedbackService:
    def __init__(
        self,
        repository: FeedbackRepositoryProtocol,
        token_generator: TokenGenerator,
        url_builder: FeedbackUrlBuilder,
    ) -> None:
        self._repo = repository
        self._tokens = token_generator
        self._urls = url_builder

    # ── Link creation (staff) ────────────────────────────────────────────
    async def create_link(
        self,
        request: CreateFeedbackLinkRequest,
        *,
        company_id: UUID | None,
        created_by: str | None,
    ) -> FeedbackLinkResponse:
        """Idempotent: a booking has at most one feedback link."""
        existing = await self._repo.get_by_booking_id(request.booking_id)
        if existing is not None:
            return self._link_response(existing, created=False)

        data = request.model_dump(exclude={"company_id"})
        data["company_id"] = company_id or request.company_id
        data["created_by"] = created_by
        data["status"] = FeedbackStatus.PENDING.value

        try:
            for _ in range(_MAX_TOKEN_ATTEMPTS):
                token = self._tokens.generate()
                if not await self._repo.token_exists(token):
                    data["public_token"] = token
                    break
            else:  # pragma: no cover - astronomically unlikely
                raise RuntimeError("Could not generate a unique feedback token")
            feedback = await self._repo.add(data)
        except IntegrityError:
            # Lost a race with a concurrent request for the same booking.
            existing = await self._repo.get_by_booking_id(request.booking_id)
            if existing is None:
                raise
            return self._link_response(existing, created=False)

        logger.info("feedback_link_created", booking_id=str(request.booking_id), feedback_id=str(feedback.id))
        return self._link_response(feedback, created=True)

    # ── Public page ──────────────────────────────────────────────────────
    async def get_public_view(self, token: str) -> PublicFeedbackView:
        feedback = await self._require_by_token(token)
        first_name = (feedback.customer_name or "").strip().split(" ")[0] or None
        return PublicFeedbackView(
            status=feedback.status,
            language=feedback.language,
            customer_first_name=first_name,
            branch_name=feedback.branch_name,
            service_name=feedback.service_name,
            therapist_name=feedback.therapist_name,
            secondary_therapist_name=feedback.secondary_therapist_name,
            appointment_start=feedback.appointment_start,
        )

    async def submit(self, token: str, request: SubmitFeedbackRequest) -> PublicFeedbackView:
        feedback = await self._repo.get_by_token(token, for_update=True)
        if feedback is None:
            raise FeedbackNotFoundError("Feedback link not found.")
        if feedback.status == FeedbackStatus.SUBMITTED.value:
            raise FeedbackAlreadySubmittedError("Feedback has already been submitted for this booking.")

        feedback.service_rating = request.service_rating
        feedback.therapist_rating = request.therapist_rating
        feedback.arrangement_rating = request.arrangement_rating
        feedback.comment = request.comment
        feedback.status = FeedbackStatus.SUBMITTED.value
        feedback.submitted_at = datetime.now(timezone.utc)
        await self._repo.save(feedback)
        logger.info("feedback_submitted", feedback_id=str(feedback.id), booking_id=str(feedback.booking_id))
        return await self.get_public_view(token)

    # ── Admin ────────────────────────────────────────────────────────────
    async def list_feedback(
        self, filters: FeedbackFilters, page: int, page_size: int
    ) -> tuple[list[FeedbackItem], int]:
        rows, total = await self._repo.search(filters, page, page_size)
        return [self.to_item(r) for r in rows], total

    async def get_feedback(self, feedback_id: UUID) -> FeedbackItem:
        feedback = await self._repo.get_by_id(feedback_id)
        if feedback is None:
            raise FeedbackNotFoundError("Feedback not found.")
        return self.to_item(feedback)

    async def summary(self, filters: FeedbackFilters) -> FeedbackSummary:
        agg = await self._repo.aggregate(filters)
        total, submitted = agg["total_links"], agg["submitted"]

        def groups(rows: list[tuple]) -> list[FeedbackGroupSummary]:
            return [
                FeedbackGroupSummary(
                    id=str(r[0]) if r[0] is not None else None,
                    name=r[1],
                    count=int(r[2]),
                    averages=_averages(r[3], r[4], r[5]),
                )
                for r in rows
            ]

        return FeedbackSummary(
            total_links=total,
            submitted=submitted,
            pending=max(total - submitted, 0),
            response_rate=round(submitted / total * 100, 1) if total else 0.0,
            averages=_averages(*agg["averages"]),
            distribution=agg["distribution"],
            by_branch=groups(agg["by_branch"]),
            by_therapist=groups(agg["by_therapist"]),
            by_service=groups(agg["by_service"]),
        )

    # ── Mapping helpers ──────────────────────────────────────────────────
    def to_item(self, feedback: Feedback) -> FeedbackItem:
        item = FeedbackItem.model_validate(feedback)
        item.average_rating = _mean(
            [feedback.service_rating, feedback.therapist_rating, feedback.arrangement_rating]
        )
        item.feedback_url = self._urls.build(feedback.public_token)
        return item

    def _link_response(self, feedback: Feedback, *, created: bool) -> FeedbackLinkResponse:
        return FeedbackLinkResponse(
            id=feedback.id,
            public_token=feedback.public_token,
            feedback_url=self._urls.build(feedback.public_token),
            status=feedback.status,
            created=created,
            booking_id=feedback.booking_id,
            submitted_at=feedback.submitted_at,
        )

    async def _require_by_token(self, token: str) -> Feedback:
        feedback = await self._repo.get_by_token(token)
        if feedback is None:
            raise FeedbackNotFoundError("Feedback link not found.")
        return feedback

