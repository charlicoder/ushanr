"""
Generic async SQLAlchemy 2.x repository base class.

Provides standard CRUD operations backed by an :class:`AsyncSession`.
All concrete repositories should extend :class:`BaseRepository` and supply
the bound SQLAlchemy model class via the ``model`` constructor argument.
"""

from __future__ import annotations

import math
from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Generic type variable — represents any SQLAlchemy ORM model class
# ---------------------------------------------------------------------------
T = TypeVar("T")


class BaseRepository(Generic[T]):
    """
    Generic async repository providing standard data-access operations.

    Parameters
    ----------
    session:
        An active :class:`AsyncSession` injected by the FastAPI dependency system.
    model:
        The SQLAlchemy mapped class this repository manages.

    Example usage::

        class AccountRepository(BaseRepository[Account]):
            def __init__(self, session: AsyncSession) -> None:
                super().__init__(session, Account)
    """

    def __init__(self, session: AsyncSession, model: type[T]) -> None:
        self._session = session
        self._model = model

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get(self, id: UUID) -> T | None:
        """
        Fetch a single record by primary key.

        Parameters
        ----------
        id:
            The UUID primary key of the record.

        Returns
        -------
        T | None
            The mapped instance, or ``None`` when no record exists with the given *id*.
        """
        return await self._session.get(self._model, id)

    async def get_or_raise(self, id: UUID, exc_class: type[Exception]) -> T:
        """
        Fetch a single record by primary key, raising *exc_class* if not found.

        Parameters
        ----------
        id:
            The UUID primary key of the record.
        exc_class:
            Exception class to instantiate and raise when the record is missing.
            Will be called with a single descriptive string argument.

        Returns
        -------
        T
            The mapped instance.

        Raises
        ------
        exc_class
            When no record with *id* exists in the database.
        """
        instance = await self.get(id)
        if instance is None:
            raise exc_class(
                f"{self._model.__name__} with id={id} was not found."
            )
        return instance

    async def list(
        self,
        filters: dict[str, Any],
        page: int = 1,
        page_size: int = 25,
        order_by: Any = None,
    ) -> tuple[list[T], int]:
        """
        Return a paginated list of records matching *filters*.

        Parameters
        ----------
        filters:
            A mapping of column attribute names to their required values.
            Each entry is translated to an equality filter
            (``model.column == value``).  Pass an empty dict for no filtering.
        page:
            1-indexed page number.
        page_size:
            Number of records per page.
        order_by:
            Optional SQLAlchemy column expression to order results by.
            When ``None``, no explicit ORDER BY is applied.

        Returns
        -------
        tuple[list[T], int]
            A 2-tuple of *(items, total)* where *total* is the total count of
            records matching *filters* (before pagination).
        """
        # Build the base WHERE clause from the filters dict
        where_clauses = [
            getattr(self._model, attr) == value
            for attr, value in filters.items()
            if value is not None
        ]

        # COUNT query
        count_stmt = select(func.count()).select_from(self._model)
        if where_clauses:
            count_stmt = count_stmt.where(*where_clauses)
        total_result = await self._session.execute(count_stmt)
        total: int = total_result.scalar_one()

        # DATA query with pagination
        offset = (page - 1) * page_size
        data_stmt = select(self._model)
        if where_clauses:
            data_stmt = data_stmt.where(*where_clauses)
        if order_by is not None:
            data_stmt = data_stmt.order_by(order_by)
        data_stmt = data_stmt.offset(offset).limit(page_size)

        result = await self._session.execute(data_stmt)
        items: list[T] = list(result.scalars().all())

        return items, total

    async def count(self, filters: dict[str, Any]) -> int:
        """
        Return the number of records matching *filters*.

        Parameters
        ----------
        filters:
            Equality filter mapping as described in :meth:`list`.

        Returns
        -------
        int
            Count of matching records.
        """
        where_clauses = [
            getattr(self._model, attr) == value
            for attr, value in filters.items()
            if value is not None
        ]
        stmt = select(func.count()).select_from(self._model)
        if where_clauses:
            stmt = stmt.where(*where_clauses)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create(self, data: dict[str, Any]) -> T:
        """
        Create and persist a new record from *data*.

        Parameters
        ----------
        data:
            Mapping of column attribute names to their values.
            Typically the ``.model_dump()`` output of a Pydantic request schema.

        Returns
        -------
        T
            The newly created and refreshed ORM instance.
        """
        instance: T = self._model(**data)  # type: ignore[call-arg]
        self._session.add(instance)
        await self._session.flush()
        await self._session.refresh(instance)
        return instance

    async def update(self, instance: T, data: dict[str, Any]) -> T:
        """
        Apply *data* to an existing ORM *instance* and persist the changes.

        Parameters
        ----------
        instance:
            The ORM instance to update (fetched via :meth:`get` or :meth:`get_or_raise`).
        data:
            Mapping of column attribute names to new values.
            ``None`` values are skipped to support partial (PATCH) updates.

        Returns
        -------
        T
            The updated ORM instance after flush and refresh.
        """
        for attr, value in data.items():
            if value is not None:
                setattr(instance, attr, value)
        self._session.add(instance)
        await self._session.flush()
        await self._session.refresh(instance)
        return instance

    async def delete(self, instance: T) -> None:
        """
        Delete *instance* from the database.

        Parameters
        ----------
        instance:
            The ORM instance to delete.

        Notes
        -----
        Performs a hard delete.  For soft-delete behaviour, override this
        method in the concrete repository and set ``is_active = False``
        (or equivalent) instead.
        """
        await self._session.delete(instance)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def compute_pages(total: int, page_size: int) -> int:
        """
        Compute total page count from *total* records and *page_size*.

        Parameters
        ----------
        total:
            Total number of records.
        page_size:
            Records per page.

        Returns
        -------
        int
            Ceiling division of *total* / *page_size*, minimum 0.
        """
        if page_size <= 0:
            return 0
        return math.ceil(total / page_size) if total > 0 else 0
