"""
Common Pydantic v2 schemas used across all endpoints of the ushanr accounting microservice.

Includes:
  - Generic paginated response wrapper
  - Standardised success / error envelope
  - Reusable query-parameter classes (pagination, date-range filter)
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Generic type variable for paginated payloads
# ---------------------------------------------------------------------------
T = TypeVar("T")


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class PaginatedResponse(BaseModel, Generic[T]):
    """Generic wrapper for paginated list responses."""

    model_config = ConfigDict(from_attributes=True)

    items: list[T]
    total: int = Field(..., ge=0, description="Total number of records matching the filter")
    page: int = Field(..., ge=1, description="Current page number (1-indexed)")
    page_size: int = Field(..., ge=1, description="Number of items per page")
    pages: int = Field(..., ge=0, description="Total number of pages")


class PaginationParams:
    """
    Dependency-injectable pagination parameters.

    Usage::

        @router.get("/items")
        async def list_items(pagination: Annotated[PaginationParams, Depends()]):
            ...
    """

    def __init__(
        self,
        page: Annotated[int, Query(ge=1, description="Page number, 1-indexed")] = 1,
        page_size: Annotated[
            int, Query(ge=1, le=200, description="Number of results per page")
        ] = 25,
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        """SQL OFFSET derived from page / page_size."""
        return (self.page - 1) * self.page_size


# ---------------------------------------------------------------------------
# Date-range filter
# ---------------------------------------------------------------------------

class DateRangeFilter(BaseModel):
    """Optional inclusive date-range used by list / report endpoints."""

    model_config = ConfigDict(from_attributes=True)

    date_from: date | None = Field(
        default=None,
        description="Start of the date range (inclusive). ISO-8601 date string.",
    )
    date_to: date | None = Field(
        default=None,
        description="End of the date range (inclusive). ISO-8601 date string.",
    )


# ---------------------------------------------------------------------------
# Success envelope
# ---------------------------------------------------------------------------

class SuccessResponse(BaseModel):
    """Standard success envelope returned by mutating endpoints."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = True
    data: Any = None
    message: str | None = None


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    """Structured detail block embedded in :class:`ErrorResponse`."""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(..., description="Application-level error code, e.g. 'NOT_FOUND'")
    message: str = Field(..., description="Human-readable description of the error")
    detail: Any = Field(
        default=None,
        description="Optional extra information (validation errors, offending field, etc.)",
    )


class ErrorResponse(BaseModel):
    """Standard error envelope returned on all 4xx / 5xx responses."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = False
    error: ErrorDetail
