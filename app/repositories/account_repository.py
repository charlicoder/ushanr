"""
AccountRepository — data-access layer for the Chart of Accounts.

Extends :class:`BaseRepository` with account-specific queries:
  - Lookup by company + code
  - Filtered list by company
  - Aggregated debit / credit / balance computation
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account  # noqa: F401 — adjust import path if needed
from app.models.journal_item import JournalItem  # noqa: F401 — adjust import path if needed
from app.repositories.base import BaseRepository


class AccountRepository(BaseRepository[Account]):
    """
    Repository for :class:`~app.models.account.Account` entities.

    Parameters
    ----------
    session:
        An active :class:`AsyncSession` provided by the FastAPI dependency system.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Account)

    # ------------------------------------------------------------------
    # Account-specific queries
    # ------------------------------------------------------------------

    async def get_by_code(self, company_id: UUID, code: str) -> Account | None:
        """
        Fetch an account by its unique code within a company.

        Parameters
        ----------
        company_id:
            UUID of the owning company — scopes the lookup to prevent cross-tenant leakage.
        code:
            The chart-of-account code (e.g. ``"1010"``).

        Returns
        -------
        Account | None
            The matching :class:`Account`, or ``None`` when no account has that code in
            the given company.
        """
        stmt = (
            select(Account)
            .where(
                and_(
                    Account.company_id == company_id,
                    Account.code == code,
                )
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_company(
        self,
        company_id: UUID,
        filters: dict[str, Any] | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[Account], int]:
        """
        Return a paginated list of accounts belonging to *company_id*.

        Parameters
        ----------
        company_id:
            UUID of the owning company (mandatory scope).
        filters:
            Optional equality filters applied on top of the company scope.
            Supported keys: ``account_type``, ``is_active``.
        search:
            Optional free-text search applied as a case-insensitive partial
            match against ``Account.code`` and ``Account.name``.
        page:
            1-indexed page number.
        page_size:
            Number of records per page.

        Returns
        -------
        tuple[list[Account], int]
            *(items, total)* — paginated list and the total count before pagination.
        """
        filters = filters or {}

        # Start with the mandatory company scope
        where_clauses = [Account.company_id == company_id]

        # Equality filters
        _allowed_filter_keys = {"account_type", "is_active"}
        for attr, value in filters.items():
            if attr in _allowed_filter_keys and value is not None:
                where_clauses.append(getattr(Account, attr) == value)

        # Optional search across code and name
        if search:
            search_pattern = f"%{search}%"
            where_clauses.append(
                or_(
                    Account.code.ilike(search_pattern),
                    Account.name.ilike(search_pattern),
                )
            )

        # COUNT
        count_stmt = (
            select(func.count())
            .select_from(Account)
            .where(*where_clauses)
        )
        total_result = await self._session.execute(count_stmt)
        total: int = total_result.scalar_one()

        # DATA
        offset = (page - 1) * page_size
        data_stmt = (
            select(Account)
            .where(*where_clauses)
            .order_by(Account.code.asc())
            .offset(offset)
            .limit(page_size)
        )
        result = await self._session.execute(data_stmt)
        items: list[Account] = list(result.scalars().all())

        return items, total

    async def get_account_balance(
        self,
        account_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> dict[str, Decimal]:
        """
        Compute the aggregated debit total, credit total, and net balance for an account.

        Only **posted** journal entries are included in the aggregation.

        Parameters
        ----------
        account_id:
            UUID of the account to compute balances for.
        date_from:
            Optional start date (inclusive) for the aggregation period.
            When ``None``, aggregation includes all history up to *date_to*.
        date_to:
            Optional end date (inclusive) for the aggregation period.
            When ``None``, aggregation includes all history from *date_from* onwards.

        Returns
        -------
        dict[str, Decimal]
            A dictionary with three keys:

            * ``debit_total``  — sum of all debit amounts on posted journal items.
            * ``credit_total`` — sum of all credit amounts on posted journal items.
            * ``balance``      — ``debit_total - credit_total``
                                 (positive = net debit balance).

        Notes
        -----
        The query joins :class:`JournalItem` with its parent :class:`JournalEntry`
        to filter on ``entry_date`` and ``state = 'posted'``.
        """
        # Lazy import to avoid circular imports at module level
        from app.models.journal_entry import JournalEntry  # noqa: PLC0415

        where_clauses = [
            JournalItem.account_id == account_id,
            JournalEntry.state == "posted",
        ]

        if date_from is not None:
            where_clauses.append(JournalEntry.entry_date >= date_from)
        if date_to is not None:
            where_clauses.append(JournalEntry.entry_date <= date_to)

        stmt = (
            select(
                func.coalesce(func.sum(JournalItem.debit), Decimal("0")).label("debit_total"),
                func.coalesce(func.sum(JournalItem.credit), Decimal("0")).label("credit_total"),
            )
            .join(JournalEntry, JournalItem.journal_entry_id == JournalEntry.id)
            .where(*where_clauses)
        )

        result = await self._session.execute(stmt)
        row = result.one()

        debit_total: Decimal = row.debit_total
        credit_total: Decimal = row.credit_total
        balance: Decimal = debit_total - credit_total

        return {
            "debit_total": debit_total,
            "credit_total": credit_total,
            "balance": balance,
        }
