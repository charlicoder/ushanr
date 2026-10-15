"""
app/api/v1/deps.py
──────────────────
Common dependencies for API v1 endpoints.
"""
from __future__ import annotations

from uuid import UUID
from fastapi import Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.logging import get_logger
from app.models.company import Company

logger = get_logger(__name__)


async def get_optional_company_id(
    company_id: UUID | None = Query(None, description="Company ID (defaults to active company if not specified)"),
    db: AsyncSession = Depends(get_db),
) -> UUID | None:
    """
    Returns the specified company_id if it exists in DB,
    otherwise automatically defaults to the active company in the system.
    """
    if company_id is not None:
        exists = (
            await db.execute(select(Company.id).where(Company.id == company_id))
        ).scalar_one_or_none()
        if exists is not None:
            return exists
        logger.warning(
            "requested_company_not_found_falling_back_to_active",
            requested_company_id=str(company_id),
        )

    comp = (
        await db.execute(select(Company.id).where(Company.is_active == True).limit(1))
    ).scalar_one_or_none()
    if comp is None:
        comp = (await db.execute(select(Company.id).limit(1))).scalar_one_or_none()
    return comp
