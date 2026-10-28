"""Unit tests for FeedbackService (no database — in-memory fake repository)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import FeedbackAlreadySubmittedError, FeedbackNotFoundError
from app.models.feedback import FeedbackStatus
from app.repositories.feedback_repository import FeedbackFilters
from app.schemas.feedback import CreateFeedbackLinkRequest, SubmitFeedbackRequest
from app.services.feedback_service import FeedbackService, FeedbackUrlBuilder


class FakeRepo:
    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, Any] = {}
        self.race_once = False

    async def get_by_id(self, feedback_id):
        return self.rows.get(feedback_id)

    async def get_by_token(self, token, *, for_update=False):
        return next((r for r in self.rows.values() if r.public_token == token), None)

    async def get_by_booking_id(self, booking_id):
        return next((r for r in self.rows.values() if r.booking_id == booking_id), None)

    async def token_exists(self, token):
        return await self.get_by_token(token) is not None

    async def add(self, data):
        if self.race_once:
            self.race_once = False
            # a concurrent request created the row first
            winner = SimpleNamespace(id=uuid.uuid4(), created_at=datetime.now(timezone.utc),
                                     submitted_at=None, **{**data, "public_token": "winner-token"})
            self.rows[winner.id] = winner
            raise IntegrityError("insert", {}, Exception("duplicate booking_id"))
        row = SimpleNamespace(id=uuid.uuid4(), created_at=datetime.now(timezone.utc), submitted_at=None,
                              service_rating=None, therapist_rating=None, arrangement_rating=None,
                              comment=None, **data)
        self.rows[row.id] = row
        return row

    async def save(self, feedback):
        return feedback

    async def search(self, filters, page, page_size):
        rows = list(self.rows.values())
        return rows, len(rows)

    async def aggregate(self, filters):
        raise NotImplementedError


class SeqTokens:
    def __init__(self, *tokens: str) -> None:
        self._tokens = list(tokens)

    def generate(self) -> str:
        return self._tokens.pop(0)


def make_service(repo: FakeRepo | None = None, tokens=("tok-1", "tok-2", "tok-3")) -> tuple[FeedbackService, FakeRepo]:
    repo = repo or FakeRepo()
    return FeedbackService(repo, SeqTokens(*tokens), FeedbackUrlBuilder("https://ushspa.co/feedback/")), repo


def link_request(booking_id: uuid.UUID | None = None, **kw) -> CreateFeedbackLinkRequest:
    return CreateFeedbackLinkRequest(
        booking_id=booking_id or uuid.uuid4(),
        customer_name="Sara Ahmed",
        therapist_name="Maha",
        branch_name="Salmiya",
        service_name="Hot Stone",
        **kw,
    )


async def test_create_link_builds_public_url_and_is_idempotent():
    svc, repo = make_service()
    req = link_request()
    first = await svc.create_link(req, company_id=None, created_by="staff")
    assert first.created is True
    assert first.feedback_url == "https://ushspa.co/feedback/tok-1"
    assert first.status == "pending"

    again = await svc.create_link(req, company_id=None, created_by="staff")
    assert again.created is False
    assert again.public_token == first.public_token
    assert len(repo.rows) == 1


async def test_create_link_retries_on_token_collision():
    svc, repo = make_service(tokens=("dup", "fresh"))
    first_req = link_request()
    await svc.create_link(first_req, company_id=None, created_by=None)  # uses "dup"
    svc._tokens = SeqTokens("dup", "fresh")
    second = await svc.create_link(link_request(), company_id=None, created_by=None)
    assert second.public_token == "fresh"


async def test_create_link_race_same_booking_returns_winner():
    svc, repo = make_service()
    booking_id = uuid.uuid4()
    repo.race_once = True
    res = await svc.create_link(link_request(booking_id), company_id=None, created_by=None)
    assert res.created is False
    assert res.public_token == "winner-token"


async def test_public_view_hides_pii_and_uses_first_name():
    svc, _ = make_service()
    link = await svc.create_link(link_request(customer_phone="+96500000000"), company_id=None, created_by=None)
    view = await svc.get_public_view(link.public_token)
    assert view.customer_first_name == "Sara"
    assert not hasattr(view, "customer_phone")
    assert view.status == "pending"


async def test_public_view_unknown_token_not_found():
    svc, _ = make_service()
    with pytest.raises(FeedbackNotFoundError):
        await svc.get_public_view("nope")


async def test_submit_marks_submitted_and_blocks_duplicates():
    svc, repo = make_service()
    link = await svc.create_link(link_request(), company_id=None, created_by=None)
    body = SubmitFeedbackRequest(service_rating=5, therapist_rating=4, arrangement_rating=3, comment="  lovely ")

    view = await svc.submit(link.public_token, body)
    assert view.status == FeedbackStatus.SUBMITTED.value
    row = next(iter(repo.rows.values()))
    assert (row.service_rating, row.therapist_rating, row.arrangement_rating) == (5, 4, 3)
    assert row.comment == "lovely"
    assert row.submitted_at is not None

    with pytest.raises(FeedbackAlreadySubmittedError):
        await svc.submit(link.public_token, body)


async def test_submit_unknown_token():
    svc, _ = make_service()
    with pytest.raises(FeedbackNotFoundError):
        await svc.submit("nope", SubmitFeedbackRequest(service_rating=1, therapist_rating=1, arrangement_rating=1))


@pytest.mark.parametrize("bad", [0, 6, -1])
def test_rating_bounds_validated(bad):
    with pytest.raises(ValidationError):
        SubmitFeedbackRequest(service_rating=bad, therapist_rating=3, arrangement_rating=3)


def test_missing_rating_rejected():
    with pytest.raises(ValidationError):
        SubmitFeedbackRequest(service_rating=3, therapist_rating=3)  # type: ignore[call-arg]


def test_blank_ids_coerced_to_none_and_language_normalised():
    req = CreateFeedbackLinkRequest(
        booking_id=uuid.uuid4(), customer_id="", therapist_id="  ", language="EN-us"
    )
    assert req.customer_id is None and req.therapist_id is None
    assert req.language == "en"
    assert CreateFeedbackLinkRequest(booking_id=uuid.uuid4(), language="xx").language == "ar"


async def test_to_item_computes_average_and_url():
    svc, repo = make_service()
    link = await svc.create_link(link_request(), company_id=None, created_by=None)
    await svc.submit(link.public_token, SubmitFeedbackRequest(service_rating=5, therapist_rating=4, arrangement_rating=3))
    items, total = await svc.list_feedback(FeedbackFilters(), 1, 25)
    assert total == 1
    assert items[0].average_rating == 4.0
    assert items[0].feedback_url == "https://ushspa.co/feedback/tok-1"


async def test_summary_math():
    svc, repo = make_service()

    async def fake_aggregate(_):
        return {
            "total_links": 4,
            "submitted": 3,
            "averages": (4.5, 3.0, None),
            "distribution": {"service": {"1": 0, "2": 0, "3": 1, "4": 0, "5": 2}},
            "by_branch": [(uuid.uuid4(), "Salmiya", 3, 4.5, 3.0, 5)],
            "by_therapist": [],
            "by_service": [],
        }

    repo.aggregate = fake_aggregate  # type: ignore[method-assign]
    s = await svc.summary(FeedbackFilters())
    assert (s.total_links, s.submitted, s.pending) == (4, 3, 1)
    assert s.response_rate == 75.0
    assert s.averages.service == 4.5 and s.averages.arrangement is None
    assert s.averages.overall == 3.75
    assert s.by_branch[0].averages.overall == 4.17
