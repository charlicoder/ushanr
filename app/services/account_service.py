"""
app/services/account_service.py
────────────────────────────────
Chart of Accounts management service.

Responsibilities:
  - CRUD for Account and AccountGroup records (per company)
  - Account balance computation via aggregated JournalItem sums
  - Trial balance generation (all accounts with non-zero movements in period)

Design notes:
  - No FastAPI dependency in this layer — pure business logic.
  - All monetary results are returned as Decimal for precision.
  - Uses SQLAlchemy 2.x async patterns (select / scalars / execute).
  - Debit-normal accounts: balance = SUM(debit) - SUM(credit)
  - Credit-normal accounts: balance = SUM(credit) - SUM(debit)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AccountNotFoundError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.models.account import Account, AccountGroup, AccountNature, AccountType, ACCOUNT_NATURE_MAP
from app.models.journal_entry import EntryState, JournalEntry, JournalItem

logger = get_logger(__name__)

ZERO = Decimal("0")
PRECISION = Decimal("0.001")  # KWD uses 3 decimal places


# ── Input / Output dataclasses ────────────────────────────────────────────────

@dataclass
class CreateAccountData:
    """Input for creating a new Chart of Accounts entry."""
    company_id: UUID
    code: str
    name: str
    account_type: str                         # AccountType value
    account_nature: str | None = None         # auto-derived if None
    parent_id: UUID | None = None
    group_id: UUID | None = None
    currency_code: str | None = None
    is_reconcilable: bool = False
    is_bank_account: bool = False
    allow_reconciliation: bool = True
    description: str | None = None
    sequence: int = 10
    opening_debit: Decimal = field(default_factory=lambda: ZERO)
    opening_credit: Decimal = field(default_factory=lambda: ZERO)


@dataclass
class UpdateAccountData:
    """Input for updating an existing account. Only provided fields are changed."""
    name: str | None = None
    description: str | None = None
    is_reconcilable: bool | None = None
    is_bank_account: bool | None = None
    allow_reconciliation: bool | None = None
    currency_code: str | None = None
    sequence: int | None = None
    is_active: bool | None = None
    deprecated: bool | None = None
    group_id: UUID | None = None


@dataclass
class AccountBalanceResult:
    """Result of a single account balance computation."""
    account_id: UUID
    account_code: str
    account_name: str
    account_type: str
    account_nature: str
    debit_total: Decimal
    credit_total: Decimal
    balance: Decimal          # signed balance in debit-normal perspective
    opening_debit: Decimal
    opening_credit: Decimal


@dataclass
class TrialBalanceLine:
    """One line in the trial balance report."""
    account_id: UUID
    account_code: str
    account_name: str
    account_type: str
    account_nature: str
    debit_total: Decimal
    credit_total: Decimal
    balance_debit: Decimal    # balance shown in debit column (if debit-normal)
    balance_credit: Decimal   # balance shown in credit column (if credit-normal)


@dataclass
class AccountFilters:
    """Optional filters for listing accounts."""
    company_id: UUID | None = None
    account_type: str | None = None
    is_active: bool | None = True
    is_deprecated: bool | None = False
    is_bank_account: bool | None = None
    search: str | None = None            # matches code or name (ILIKE)
    group_id: UUID | None = None
    parent_id: UUID | None = None
    limit: int = 200
    offset: int = 0


# ── Service ───────────────────────────────────────────────────────────────────

class AccountService:
    """
    Async service for Chart of Accounts management.

    Usage::

        async with async_session() as session:
            svc = AccountService(session)
            account = await svc.create_account(data)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Create ────────────────────────────────────────────────────────────────

    async def create_account(self, data: CreateAccountData) -> Account:
        """
        Create a new ledger account.

        Auto-derives account_nature from account_type if not provided.
        Validates that the account code is unique within the company.

        Raises:
            ValidationError: Invalid account_type.
            ConflictError: Code already exists for this company.
        """
        # Validate account type
        valid_types = {t.value for t in AccountType}
        if data.account_type not in valid_types:
            raise ValidationError(
                f"Invalid account_type '{data.account_type}'. "
                f"Valid types: {sorted(valid_types)}"
            )

        # Auto-derive nature
        nature = data.account_nature or ACCOUNT_NATURE_MAP.get(
            data.account_type, AccountNature.DEBIT.value
        )

        # Check uniqueness
        existing = await self._session.execute(
            select(Account.id).where(
                Account.company_id == data.company_id,
                Account.code == data.code,
                Account.is_deleted.is_(False),
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ConflictError(
                f"Account with code '{data.code}' already exists for this company."
            )

        account = Account(
            company_id=data.company_id,
            code=data.code,
            name=data.name,
            account_type=data.account_type,
            account_nature=nature,
            parent_id=data.parent_id,
            group_id=data.group_id,
            currency_code=data.currency_code,
            is_reconcilable=data.is_reconcilable,
            is_bank_account=data.is_bank_account,
            allow_reconciliation=data.allow_reconciliation,
            description=data.description,
            sequence=data.sequence,
            opening_debit=float(data.opening_debit),
            opening_credit=float(data.opening_credit),
            is_active=True,
            deprecated=False,
        )
        self._session.add(account)
        await self._session.flush()

        logger.info(
            "account_created",
            account_id=str(account.id),
            code=account.code,
            company_id=str(data.company_id),
        )
        return account

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_account(self, account_id: UUID, company_id: UUID | None = None) -> Account:
        """
        Fetch a single account by ID.

        Args:
            account_id: Primary key of the account.
            company_id: If provided, enforces company scope (multi-tenant guard).

        Raises:
            AccountNotFoundError: Account not found or deleted.
        """
        stmt = select(Account).where(
            Account.id == account_id,
            Account.is_deleted.is_(False),
        )
        if company_id is not None:
            stmt = stmt.where(Account.company_id == company_id)

        result = await self._session.execute(stmt)
        account = result.scalar_one_or_none()
        if account is None:
            raise AccountNotFoundError(
                f"Account {account_id} not found.",
                detail={"account_id": str(account_id)},
            )
        return account

    async def get_account_by_code(self, company_id: UUID, code: str) -> Account:
        """
        Fetch a single account by company + code.

        Raises:
            AccountNotFoundError: Account not found or deprecated.
        """
        result = await self._session.execute(
            select(Account).where(
                Account.company_id == company_id,
                Account.code == code,
                Account.is_deleted.is_(False),
            )
        )
        account = result.scalar_one_or_none()
        if account is None:
            raise AccountNotFoundError(
                f"Account with code '{code}' not found for company {company_id}.",
                detail={"code": code, "company_id": str(company_id)},
            )
        return account

    async def list_accounts(self, filters: AccountFilters) -> list[Account]:
        """
        Return a filtered, paginated list of accounts.

        Ordered by sequence ASC, code ASC.
        """
        stmt = select(Account).where(Account.is_deleted.is_(False))

        if filters.company_id is not None:
            stmt = stmt.where(Account.company_id == filters.company_id)
        if filters.account_type is not None:
            stmt = stmt.where(Account.account_type == filters.account_type)
        if filters.is_active is not None:
            stmt = stmt.where(Account.is_active == filters.is_active)
        if filters.is_deprecated is not None:
            stmt = stmt.where(Account.deprecated == filters.is_deprecated)
        if filters.is_bank_account is not None:
            stmt = stmt.where(Account.is_bank_account == filters.is_bank_account)
        if filters.group_id is not None:
            stmt = stmt.where(Account.group_id == filters.group_id)
        if filters.parent_id is not None:
            stmt = stmt.where(Account.parent_id == filters.parent_id)
        if filters.search:
            pattern = f"%{filters.search}%"
            stmt = stmt.where(
                or_(
                    Account.code.ilike(pattern),
                    Account.name.ilike(pattern),
                )
            )

        stmt = (
            stmt.order_by(Account.sequence.asc(), Account.code.asc())
            .limit(filters.limit)
            .offset(filters.offset)
        )

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ── Update ────────────────────────────────────────────────────────────────

    async def update_account(
        self, account_id: UUID, data: UpdateAccountData, company_id: UUID | None = None
    ) -> Account:
        """
        Update mutable fields of an account.

        Posted entries linked to this account are NOT affected.
        Code changes are disallowed — create a new account instead.

        Raises:
            AccountNotFoundError: Account not found.
        """
        account = await self.get_account(account_id, company_id=company_id)

        changes: dict[str, Any] = {}
        if data.name is not None:
            changes["name"] = data.name
        if data.description is not None:
            changes["description"] = data.description
        if data.is_reconcilable is not None:
            changes["is_reconcilable"] = data.is_reconcilable
        if data.is_bank_account is not None:
            changes["is_bank_account"] = data.is_bank_account
        if data.allow_reconciliation is not None:
            changes["allow_reconciliation"] = data.allow_reconciliation
        if data.currency_code is not None:
            changes["currency_code"] = data.currency_code
        if data.sequence is not None:
            changes["sequence"] = data.sequence
        if data.is_active is not None:
            changes["is_active"] = data.is_active
        if data.deprecated is not None:
            changes["deprecated"] = data.deprecated
        if data.group_id is not None:
            changes["group_id"] = data.group_id

        for attr, value in changes.items():
            setattr(account, attr, value)

        self._session.add(account)
        await self._session.flush()

        logger.info(
            "account_updated",
            account_id=str(account_id),
            changes=list(changes.keys()),
        )
        return account

    # ── Balance ───────────────────────────────────────────────────────────────

    async def get_account_balance(
        self,
        account_id: UUID,
        company_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        include_opening: bool = True,
    ) -> AccountBalanceResult:
        """
        Compute the balance of a single account from posted journal items.

        Balance convention:
          - Debit-normal (ASSET, EXPENSE, COGS):
              balance = SUM(debit) - SUM(credit) + opening_debit - opening_credit
          - Credit-normal (LIABILITY, EQUITY, REVENUE):
              balance = SUM(credit) - SUM(debit) + opening_credit - opening_debit

        Args:
            account_id:      Account primary key.
            company_id:      Company scope.
            date_from:       Optional start date filter on journal_items.date.
            date_to:         Optional end date filter on journal_items.date.
            include_opening: Add the account's opening_debit/opening_credit to balance.

        Raises:
            AccountNotFoundError: Account not found.
        """
        account = await self.get_account(account_id, company_id=company_id)

        # Build aggregation query: JOIN journal_items → journal_entries (posted only)
        stmt = (
            select(
                func.coalesce(func.sum(JournalItem.debit_amount), 0).label("total_debit"),
                func.coalesce(func.sum(JournalItem.credit_amount), 0).label("total_credit"),
            )
            .join(JournalEntry, JournalItem.entry_id == JournalEntry.id)
            .where(
                JournalItem.account_id == account_id,
                JournalEntry.state == EntryState.POSTED.value,
                JournalEntry.company_id == company_id,
            )
        )

        if date_from is not None:
            stmt = stmt.where(JournalItem.date >= date_from)
        if date_to is not None:
            stmt = stmt.where(JournalItem.date <= date_to)

        row = (await self._session.execute(stmt)).one()
        total_debit = Decimal(str(row.total_debit))
        total_credit = Decimal(str(row.total_credit))

        # Opening amounts
        opening_debit = Decimal(str(account.opening_debit)) if include_opening else ZERO
        opening_credit = Decimal(str(account.opening_credit)) if include_opening else ZERO

        # Compute signed balance
        if account.is_debit_normal:
            balance = (total_debit + opening_debit) - (total_credit + opening_credit)
        else:
            balance = (total_credit + opening_credit) - (total_debit + opening_debit)

        balance = balance.quantize(PRECISION, rounding=ROUND_HALF_UP)
        total_debit = total_debit.quantize(PRECISION, rounding=ROUND_HALF_UP)
        total_credit = total_credit.quantize(PRECISION, rounding=ROUND_HALF_UP)

        return AccountBalanceResult(
            account_id=account.id,
            account_code=account.code,
            account_name=account.name,
            account_type=account.account_type,
            account_nature=account.account_nature,
            debit_total=total_debit,
            credit_total=total_credit,
            balance=balance,
            opening_debit=opening_debit,
            opening_credit=opening_credit,
        )

    # ── Trial Balance ─────────────────────────────────────────────────────────

    async def get_trial_balance(
        self,
        company_id: UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        include_zero_balances: bool = False,
    ) -> list[TrialBalanceLine]:
        """
        Generate a trial balance for all accounts in a company.

        Returns one line per account that has had any posted journal activity
        (or all accounts if include_zero_balances=True).

        The result is sorted by account_type then account_code.

        Total debits == Total credits is the fundamental double-entry invariant.
        Callers should assert this after calling this method.
        """
        # Single pass: aggregate JournalItems grouped by account, joined to Account
        stmt = (
            select(
                Account.id.label("account_id"),
                Account.code.label("account_code"),
                Account.name.label("account_name"),
                Account.account_type.label("account_type"),
                Account.account_nature.label("account_nature"),
                Account.opening_debit.label("opening_debit"),
                Account.opening_credit.label("opening_credit"),
                func.coalesce(func.sum(JournalItem.debit_amount), 0).label("total_debit"),
                func.coalesce(func.sum(JournalItem.credit_amount), 0).label("total_credit"),
            )
            .join(JournalItem, JournalItem.account_id == Account.id, isouter=True)
            .join(
                JournalEntry,
                and_(
                    JournalItem.entry_id == JournalEntry.id,
                    JournalEntry.state == EntryState.POSTED.value,
                    JournalEntry.company_id == company_id,
                ),
                isouter=True,
            )
            .where(
                Account.company_id == company_id,
                Account.is_deleted.is_(False),
                Account.is_active.is_(True),
            )
            .group_by(
                Account.id,
                Account.code,
                Account.name,
                Account.account_type,
                Account.account_nature,
                Account.opening_debit,
                Account.opening_credit,
            )
            .order_by(Account.account_type.asc(), Account.code.asc())
        )

        # Date filters apply to journal items (movement period)
        if date_from is not None:
            stmt = stmt.where(
                or_(JournalItem.date.is_(None), JournalItem.date >= date_from)
            )
        if date_to is not None:
            stmt = stmt.where(
                or_(JournalItem.date.is_(None), JournalItem.date <= date_to)
            )

        rows = (await self._session.execute(stmt)).all()

        lines: list[TrialBalanceLine] = []
        for row in rows:
            total_debit = Decimal(str(row.total_debit))
            total_credit = Decimal(str(row.total_credit))
            opening_debit = Decimal(str(row.opening_debit))
            opening_credit = Decimal(str(row.opening_credit))

            # Include opening balances in aggregation
            agg_debit = (total_debit + opening_debit).quantize(PRECISION, rounding=ROUND_HALF_UP)
            agg_credit = (total_credit + opening_credit).quantize(PRECISION, rounding=ROUND_HALF_UP)

            # Skip zero-balance accounts if not requested
            if not include_zero_balances and agg_debit == ZERO and agg_credit == ZERO:
                continue

            is_debit_normal = row.account_nature == AccountNature.DEBIT.value

            if is_debit_normal:
                net = agg_debit - agg_credit
                balance_debit = max(net, ZERO)
                balance_credit = max(-net, ZERO)
            else:
                net = agg_credit - agg_debit
                balance_credit = max(net, ZERO)
                balance_debit = max(-net, ZERO)

            lines.append(
                TrialBalanceLine(
                    account_id=row.account_id,
                    account_code=row.account_code,
                    account_name=row.account_name,
                    account_type=row.account_type,
                    account_nature=row.account_nature,
                    debit_total=agg_debit,
                    credit_total=agg_credit,
                    balance_debit=balance_debit,
                    balance_credit=balance_credit,
                )
            )

        logger.info(
            "trial_balance_generated",
            company_id=str(company_id),
            date_from=str(date_from),
            date_to=str(date_to),
            lines=len(lines),
        )
        return lines

    # ── Account Group helpers ─────────────────────────────────────────────────

    async def get_account_group(
        self, group_id: UUID, company_id: UUID | None = None
    ) -> AccountGroup:
        """Fetch an AccountGroup by ID."""
        stmt = select(AccountGroup).where(AccountGroup.id == group_id)
        if company_id is not None:
            stmt = stmt.where(AccountGroup.company_id == company_id)
        result = await self._session.execute(stmt)
        group = result.scalar_one_or_none()
        if group is None:
            raise NotFoundError(
                f"AccountGroup {group_id} not found.",
                detail={"group_id": str(group_id)},
            )
        return group
