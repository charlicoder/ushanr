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
from app.models.company import Company


async def get_optional_company_id(
    company_id: UUID | None = Query(None, description="Company ID (defaults to active company if not specified)"),
    db: AsyncSession = Depends(get_db),
) -> UUID | None:
    """
    Returns the specified company_id, or automatically defaults to the
    first active company in the system if omitted.
    """
    if company_id is not None:
        return company_id
    comp = (await db.execute(select(Company.id).where(Company.is_active == True).limit(1))).scalar_one_or_none()
    return comp
