"""
app/repositories/feedback_repository.py
───────────────────────────────────────
Data access for :class:`~app.models.feedback.Feedback`.

Only persistence concerns live here; business rules belong to ``FeedbackService``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feedback import Feedback, FeedbackStatus
from app.repositories.base import BaseRepository


@dataclass(slots=True)
class FeedbackFilters:
    company_id: UUID | None = None
    branch_id: UUID | None = None
    therapist_id: UUID | None = None
    service_id: UUID | None = None
    status: str | None = None
    min_rating: float | None = None
    max_rating: float | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    search: str | None = None


class FeedbackRepositoryProtocol(Protocol):
    """Abstraction the service depends on (DIP)."""

    async def get_by_id(self, feedback_id: UUID) -> Feedback | None: ...
    async def get_by_token(self, token: str, *, for_update: bool = False) -> Feedback | None: ...
    async def get_by_booking_id(self, booking_id: UUID) -> Feedback | None: ...
    async def token_exists(self, token: str) -> bool: ...
    async def add(self, data: dict[str, Any]) -> Feedback: ...
    async def save(self, feedback: Feedback) -> Feedback: ...
    async def search(
        self, filters: FeedbackFilters, page: int, page_size: int
    ) -> tuple[list[Feedback], int]: ...
    async def aggregate(self, filters: FeedbackFilters) -> dict[str, Any]: ...


# Average of the three question ratings for a row.
_ROW_AVG = (Feedback.service_rating + Feedback.therapist_rating + Feedback.arrangement_rating) / 3.0


class FeedbackRepository(BaseRepository[Feedback]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Feedback)

    # ── Lookups ──────────────────────────────────────────────────────────
    async def get_by_id(self, feedback_id: UUID) -> Feedback | None:
        return await self.get(feedback_id)

    async def get_by_token(self, token: str, *, for_update: bool = False) -> Feedback | None:
        stmt = select(Feedback).where(Feedback.public_token == token)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_booking_id(self, booking_id: UUID) -> Feedback | None:
        stmt = select(Feedback).where(Feedback.booking_id == booking_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def token_exists(self, token: str) -> bool:
        stmt = select(func.count()).select_from(Feedback).where(Feedback.public_token == token)
        return (await self._session.execute(stmt)).scalar_one() > 0

    # ── Writes ───────────────────────────────────────────────────────────
    async def add(self, data: dict[str, Any]) -> Feedback:
        # Savepoint: a unique-violation (concurrent create for the same booking)
        # must not poison the surrounding transaction.
        async with self._session.begin_nested():
            instance = Feedback(**data)
            self._session.add(instance)
            await self._session.flush()
        await self._session.refresh(instance)
        return instance

    async def save(self, feedback: Feedback) -> Feedback:
        self._session.add(feedback)
        await self._session.flush()
        await self._session.refresh(feedback)
        return feedback

    # ── Queries ──────────────────────────────────────────────────────────
    @staticmethod
    def _where(f: FeedbackFilters) -> list[Any]:
        clauses: list[Any] = []
        if f.company_id:
            clauses.append(Feedback.company_id == f.company_id)
        if f.branch_id:
            clauses.append(Feedback.branch_id == f.branch_id)
        if f.therapist_id:
            clauses.append(
                or_(Feedback.therapist_id == f.therapist_id, Feedback.secondary_therapist_id == f.therapist_id)
            )
        if f.service_id:
            clauses.append(Feedback.service_id == f.service_id)
        if f.status:
            clauses.append(Feedback.status == f.status)
        if f.min_rating is not None:
            clauses.append(_ROW_AVG >= f.min_rating)
        if f.max_rating is not None:
            clauses.append(_ROW_AVG <= f.max_rating)
        if f.date_from:
            clauses.append(func.coalesce(Feedback.submitted_at, Feedback.created_at) >= f.date_from)
        if f.date_to:
            clauses.append(func.coalesce(Feedback.submitted_at, Feedback.created_at) <= f.date_to)
        if f.search:
            like = f"%{f.search.strip()}%"
            clauses.append(
                or_(
                    Feedback.customer_name.ilike(like),
                    Feedback.customer_phone.ilike(like),
                    Feedback.booking_number.ilike(like),
                    Feedback.therapist_name.ilike(like),
                    Feedback.comment.ilike(like),
                )
            )
        return clauses

    async def search(
        self, filters: FeedbackFilters, page: int, page_size: int
    ) -> tuple[list[Feedback], int]:
        where = self._where(filters)
        count_stmt = select(func.count()).select_from(Feedback)
        data_stmt = select(Feedback)
        if where:
            count_stmt = count_stmt.where(*where)
            data_stmt = data_stmt.where(*where)
        total = (await self._session.execute(count_stmt)).scalar_one()
        data_stmt = (
            data_stmt.order_by(func.coalesce(Feedback.submitted_at, Feedback.created_at).desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list((await self._session.execute(data_stmt)).scalars().all())
        return rows, total

    async def aggregate(self, filters: FeedbackFilters) -> dict[str, Any]:
        """Totals, averages, per-question distribution and per-dimension groups (submitted only)."""
        where = self._where(filters)
        submitted = Feedback.status == FeedbackStatus.SUBMITTED.value

        # Totals (all rows matching filters, regardless of status)
        totals_stmt = select(
            func.count(),
            func.count().filter(submitted),
        ).select_from(Feedback)
        if where:
            totals_stmt = totals_stmt.where(*where)
        total_links, submitted_count = (await self._session.execute(totals_stmt)).one()

        base = and_(submitted, *where) if where else submitted

        avg_stmt = select(
            func.avg(Feedback.service_rating),
            func.avg(Feedback.therapist_rating),
            func.avg(Feedback.arrangement_rating),
        ).where(base)
        avg_service, avg_therapist, avg_arrangement = (await self._session.execute(avg_stmt)).one()

        distribution: dict[str, dict[str, int]] = {}
        for key, col in (
            ("service", Feedback.service_rating),
            ("therapist", Feedback.therapist_rating),
            ("arrangement", Feedback.arrangement_rating),
        ):
            rows = (
                await self._session.execute(
                    select(col, func.count()).where(base).group_by(col)
                )
            ).all()
            dist = {str(i): 0 for i in range(1, 6)}
            for rating, cnt in rows:
                if rating is not None:
                    dist[str(int(rating))] = int(cnt)
            distribution[key] = dist

        async def group(id_col, name_col) -> list[tuple]:
            stmt = (
                select(
                    id_col,
                    func.max(name_col),
                    func.count(),
                    func.avg(Feedback.service_rating),
                    func.avg(Feedback.therapist_rating),
                    func.avg(Feedback.arrangement_rating),
                )
                .where(base, id_col.is_not(None))
                .group_by(id_col)
                .order_by(func.count().desc())
            )
            return list((await self._session.execute(stmt)).all())

        return {
            "total_links": int(total_links or 0),
            "submitted": int(submitted_count or 0),
            "averages": (avg_service, avg_therapist, avg_arrangement),
            "distribution": distribution,
            "by_branch": await group(Feedback.branch_id, Feedback.branch_name),
            "by_therapist": await group(Feedback.therapist_id, Feedback.therapist_name),
            "by_service": await group(Feedback.service_id, Feedback.service_name),
        }
