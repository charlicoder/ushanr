"""
app/api/v1/endpoints/feedback.py
────────────────────────────────
Booking feedback endpoints (thin HTTP layer — rules live in ``FeedbackService``).

Staff (employee JWT):
  POST /create-feedback-link/            Create (or return the existing) link for a booking.

Customer (public — protected only by the unguessable token):
  GET  /feedback/public/{token}/         Context for the feedback page + status.
  POST /feedback/public/{token}/submit/  Submit the ratings (one per booking).

Admin dashboard (permission ``deskmenu.ushspa.feedback``):
  GET  /feedback/                        List / filter feedback.
  GET  /feedback/summary/                Averages, distribution, per branch/therapist/service.
  GET  /feedback/{id}/                   Detail.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_optional_company_id
from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import require_employee_user, require_permission
from app.repositories.feedback_repository import FeedbackFilters, FeedbackRepository
from app.schemas.feedback import CreateFeedbackLinkRequest, SubmitFeedbackRequest
from app.services.feedback_service import FeedbackService, FeedbackUrlBuilder, SecureTokenGenerator

router = APIRouter()

FEEDBACK_PERMISSION = "deskmenu.ushspa.feedback"


def get_feedback_service(db: AsyncSession = Depends(get_db)) -> FeedbackService:
    return FeedbackService(
        repository=FeedbackRepository(db),
        token_generator=SecureTokenGenerator(),
        url_builder=FeedbackUrlBuilder(get_settings().FEEDBACK_PUBLIC_BASE_URL),
    )


def _actor(claims: dict[str, Any]) -> str | None:
    value = claims.get("name") or claims.get("user_id") or claims.get("sub")
    return str(value) if value else None


# ── Staff ─────────────────────────────────────────────────────────────────────


@router.post("/create-feedback-link/", summary="Create (or return) the feedback link for a booking")
async def create_feedback_link(
    body: CreateFeedbackLinkRequest,
    company_id: UUID | None = Depends(get_optional_company_id),
    claims: dict[str, Any] = Depends(require_employee_user),
    service: FeedbackService = Depends(get_feedback_service),
) -> dict:
    result = await service.create_link(body, company_id=company_id, created_by=_actor(claims))
    return {"success": True, "data": result.model_dump(mode="json")}


# ── Public (customer) ─────────────────────────────────────────────────────────


@router.get("/feedback/public/{token}/", summary="Feedback page context")
async def get_public_feedback(
    token: str,
    service: FeedbackService = Depends(get_feedback_service),
) -> dict:
    view = await service.get_public_view(token)
    return {"success": True, "data": view.model_dump(mode="json")}


@router.post("/feedback/public/{token}/submit/", summary="Submit feedback ratings")
async def submit_public_feedback(
    token: str,
    body: SubmitFeedbackRequest,
    service: FeedbackService = Depends(get_feedback_service),
) -> dict:
    view = await service.submit(token, body)
    return {"success": True, "data": view.model_dump(mode="json")}


# ── Admin ─────────────────────────────────────────────────────────────────────


def _filters(
    company_id: UUID | None,
    branch_id: UUID | None,
    therapist_id: UUID | None,
    service_id: UUID | None,
    status: str | None,
    min_rating: float | None,
    max_rating: float | None,
    date_from: datetime | None,
    date_to: datetime | None,
    search: str | None,
) -> FeedbackFilters:
    return FeedbackFilters(
        company_id=company_id,
        branch_id=branch_id,
        therapist_id=therapist_id,
        service_id=service_id,
        status=status,
        min_rating=min_rating,
        max_rating=max_rating,
        date_from=date_from,
        date_to=date_to,
        search=search,
    )


@router.get("/feedback/summary/", summary="Average ratings & distribution")
async def feedback_summary(
    company_id: UUID | None = Depends(get_optional_company_id),
    branch_id: UUID | None = Query(None),
    therapist_id: UUID | None = Query(None),
    service_id: UUID | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    service: FeedbackService = Depends(get_feedback_service),
    _: dict = Depends(require_permission(FEEDBACK_PERMISSION)),
) -> dict:
    filters = _filters(company_id, branch_id, therapist_id, service_id, None, None, None, date_from, date_to, None)
    summary = await service.summary(filters)
    return {"success": True, "data": summary.model_dump(mode="json")}


@router.get("/feedback/", summary="List feedback")
async def list_feedback(
    company_id: UUID | None = Depends(get_optional_company_id),
    branch_id: UUID | None = Query(None),
    therapist_id: UUID | None = Query(None),
    service_id: UUID | None = Query(None),
    status: str | None = Query(None, pattern="^(pending|submitted)$"),
    min_rating: float | None = Query(None, ge=1, le=5),
    max_rating: float | None = Query(None, ge=1, le=5),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    service: FeedbackService = Depends(get_feedback_service),
    _: dict = Depends(require_permission(FEEDBACK_PERMISSION)),
) -> dict:
    filters = _filters(
        company_id, branch_id, therapist_id, service_id, status, min_rating, max_rating, date_from, date_to, search
    )
    items, total = await service.list_feedback(filters, page, page_size)
    return {
        "success": True,
        "data": {
            "items": [i.model_dump(mode="json") for i in items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        },
    }


@router.get("/feedback/{feedback_id}/", summary="Feedback detail")
async def get_feedback(
    feedback_id: UUID,
    service: FeedbackService = Depends(get_feedback_service),
    _: dict = Depends(require_permission(FEEDBACK_PERMISSION)),
) -> dict:
    item = await service.get_feedback(feedback_id)
    return {"success": True, "data": item.model_dump(mode="json")}

