"""
app/services/double_entry.py
─────────────────────────────
Double-Entry Bookkeeping Engine — the financial source of truth.

Rules:
  1. Every JournalEntry must balance: sum(debit) == sum(credit)
  2. All monetary calculations use Decimal — never float arithmetic
  3. Posted entries are IMMUTABLE — corrections use reversal entries
  4. Period must be open before posting
  5. Each JournalItem must have exactly one of debit or credit > 0

This service is pure business logic — no FastAPI dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    EntryAlreadyPostedError,
    LockedPeriodError,
    UnbalancedEntryError,
)
from app.core.logging import get_logger
from app.models.fiscal import AccountingPeriod, PeriodState
from app.models.journal_entry import EntryState, JournalEntry, JournalItem

logger = get_logger(__name__)

ZERO = Decimal("0")


@dataclass
class JournalItemData:
    """Input data for creating a journal item line."""
    account_id: UUID
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    name: str | None = None
    partner_id: UUID | None = None
    analytic_account_id: UUID | None = None
    currency_code: str = "KWD"
    amount_currency: Decimal | None = None
    currency_rate: Decimal | None = None
    due_date: date | None = None
    reference: str | None = None
    sequence: int = 10

    def __post_init__(self) -> None:
        # Ensure Decimal types
        self.debit = Decimal(str(self.debit))
        self.credit = Decimal(str(self.credit))
        if self.amount_currency is not None:
            self.amount_currency = Decimal(str(self.amount_currency))

    def validate(self) -> None:
        """Validate the line data."""
        if self.debit < ZERO:
            raise UnbalancedEntryError(
                f"Debit amount cannot be negative: {self.debit}"
            )
        if self.credit < ZERO:
            raise UnbalancedEntryError(
                f"Credit amount cannot be negative: {self.credit}"
            )
        if self.debit > ZERO and self.credit > ZERO:
            raise UnbalancedEntryError(
                "A journal item cannot have both debit and credit amounts."
            )


@dataclass
class JournalEntryData:
    """Input data for creating a journal entry."""
    company_id: UUID
    journal_id: UUID
    entry_date: date
    items: list[JournalItemData]
    name: str | None = None
    reference: str | None = None
    narration: str | None = None
    partner_id: UUID | None = None
    accounting_date: date | None = None
    currency_code: str = "KWD"
    source_document_type: str | None = None
    source_document_id: UUID | None = None
    posted_by: str | None = None


class DoubleEntryEngine:
    """
    Core double-entry bookkeeping engine.
    All public methods are async and operate on the provided session.
    """

    def validate_balance(self, items: list[JournalItemData]) -> None:
        """
        Enforce the fundamental double-entry rule:
          Total Debits == Total Credits

        Raises UnbalancedEntryError if not balanced.
        """
        total_debit = sum((item.debit for item in items), ZERO)
        total_credit = sum((item.credit for item in items), ZERO)

        # Round to 3 decimal places for comparison
        total_debit = total_debit.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        total_credit = total_credit.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

        if total_debit != total_credit:
            raise UnbalancedEntryError(
                f"Journal entry is unbalanced: "
                f"debits={total_debit}, credits={total_credit}, "
                f"difference={abs(total_debit - total_credit)}"
            )

    async def validate_period_open(
        self, session: AsyncSession, company_id: UUID, entry_date: date
    ) -> AccountingPeriod | None:
        """
        Check that the entry_date falls within an open accounting period.
        Returns the period if found, None if no period defined (allowed in flexible mode).
        Raises LockedPeriodError if period exists but is locked/closed.
        """
        result = await session.execute(
            select(AccountingPeriod).where(
                AccountingPeriod.company_id == company_id,
                AccountingPeriod.date_from <= entry_date,
                AccountingPeriod.date_to >= entry_date,
            )
        )
        period = result.scalar_one_or_none()

        if period is not None and period.state != PeriodState.OPEN.value:
            raise LockedPeriodError(
                f"Accounting period '{period.name}' is {period.state}. "
                "Cannot post entries to a closed or locked period."
            )
        return period

    async def create_draft_entry(
        self,
        session: AsyncSession,
        data: JournalEntryData,
    ) -> JournalEntry:
        """
        Create a DRAFT journal entry with items.
        Does NOT post — caller must call post_entry() to post.
        """
        for item in data.items:
            item.validate()

        self.validate_balance(data.items)

        accounting_date = data.accounting_date or data.entry_date
        total = sum((item.debit for item in data.items), ZERO)

        entry = JournalEntry(
            company_id=data.company_id,
            journal_id=data.journal_id,
            name=data.name,
            reference=data.reference,
            narration=data.narration,
            partner_id=data.partner_id,
            entry_date=data.entry_date,
            accounting_date=accounting_date,
            state=EntryState.DRAFT.value,
            amount_total=float(total),
            currency_code=data.currency_code,
            source_document_type=data.source_document_type,
            source_document_id=data.source_document_id,
        )
        session.add(entry)
        await session.flush()  # Get entry.id without committing

        for i, item_data in enumerate(data.items):
            item = JournalItem(
                company_id=data.company_id,
                entry_id=entry.id,
                account_id=item_data.account_id,
                partner_id=item_data.partner_id,
                analytic_account_id=item_data.analytic_account_id,
                debit_amount=float(item_data.debit),
                credit_amount=float(item_data.credit),
                name=item_data.name,
                currency_code=item_data.currency_code,
                amount_currency=float(item_data.amount_currency) if item_data.amount_currency else None,
                currency_rate=float(item_data.currency_rate) if item_data.currency_rate else None,
                due_date=item_data.due_date,
                reference=item_data.reference,
                date=data.entry_date,
                sequence=item_data.sequence or (i + 1) * 10,
            )
            session.add(item)

        await session.flush()
        logger.info(
            "journal_entry_created",
            entry_id=str(entry.id),
            journal_id=str(data.journal_id),
            total=float(total),
            state=entry.state,
        )
        return entry

    async def post_entry(
        self,
        session: AsyncSession,
        entry: JournalEntry,
        posted_by: str | None = None,
    ) -> JournalEntry:
        """
        Post a DRAFT journal entry — making it IMMUTABLE.

        Steps:
        1. Validate entry is in DRAFT state
        2. Re-validate balance
        3. Validate accounting period is open
        4. Assign period_id
        5. Set state = POSTED, posted_at, posted_by
        """
        from datetime import datetime, timezone

        if entry.state == EntryState.POSTED.value:
            raise EntryAlreadyPostedError(
                f"Journal entry {entry.name or entry.id} is already posted."
            )

        if entry.state == EntryState.CANCELLED.value:
            raise EntryAlreadyPostedError(
                f"Journal entry {entry.name or entry.id} is cancelled."
            )

        # Re-validate balance from stored items
        items_data = [
            JournalItemData(
                account_id=item.account_id,
                debit=Decimal(str(item.debit_amount)),
                credit=Decimal(str(item.credit_amount)),
            )
            for item in entry.items
        ]
        self.validate_balance(items_data)

        # Validate period
        period = await self.validate_period_open(
            session, entry.company_id, entry.accounting_date
        )

        entry.state = EntryState.POSTED.value
        entry.posted_at = datetime.now(timezone.utc)
        entry.posted_by = posted_by
        if period:
            entry.period_id = period.id

        session.add(entry)
        await session.flush()

        logger.info(
            "journal_entry_posted",
            entry_id=str(entry.id),
            entry_name=entry.name,
            posted_by=posted_by,
        )
        return entry

    async def create_and_post_entry(
        self,
        session: AsyncSession,
        data: JournalEntryData,
    ) -> JournalEntry:
        """Convenience: create and immediately post a balanced entry."""
        entry = await self.create_draft_entry(session, data)
        return await self.post_entry(session, entry, posted_by=data.posted_by)

    async def reverse_entry(
        self,
        session: AsyncSession,
        entry: JournalEntry,
        reversal_date: date,
        reversal_narration: str | None = None,
        posted_by: str | None = None,
    ) -> JournalEntry:
        """
        Create a reversal (counter-entry) for a posted journal entry.
        The reversal swaps all debit/credit amounts.

        Used for correcting posted entries — the original entry is
        NOT modified (immutability preserved).
        """
        if entry.state != EntryState.POSTED.value:
            raise EntryAlreadyPostedError(
                f"Can only reverse POSTED entries. Entry {entry.id} is {entry.state}."
            )

        reversal_items = [
            JournalItemData(
                account_id=item.account_id,
                debit=Decimal(str(item.credit_amount)),   # swap
                credit=Decimal(str(item.debit_amount)),  # swap
                name=item.name,
                partner_id=item.partner_id,
                analytic_account_id=item.analytic_account_id,
                currency_code=item.currency_code,
                amount_currency=(
                    -Decimal(str(item.amount_currency)) if item.amount_currency else None
                ),
                currency_rate=Decimal(str(item.currency_rate)) if item.currency_rate else None,
                sequence=item.sequence,
            )
            for item in entry.items
        ]

        reversal_data = JournalEntryData(
            company_id=entry.company_id,
            journal_id=entry.journal_id,
            entry_date=reversal_date,
            accounting_date=reversal_date,
            items=reversal_items,
            name=None,  # Will be assigned by sequence
            reference=f"Reversal of {entry.name or entry.id}",
            narration=reversal_narration or f"Reversal of {entry.name or entry.id}",
            partner_id=entry.partner_id,
            currency_code=entry.currency_code,
            source_document_type=entry.source_document_type,
            source_document_id=entry.source_document_id,
            posted_by=posted_by,
        )

        reversal_entry = await self.create_and_post_entry(session, reversal_data)
        reversal_entry.is_reversal = True
        reversal_entry.reversed_entry_id = entry.id
        entry.reversed_entry_id = reversal_entry.id

        session.add(reversal_entry)
        session.add(entry)
        await session.flush()

        logger.info(
            "journal_entry_reversed",
            original_entry_id=str(entry.id),
            reversal_entry_id=str(reversal_entry.id),
            reversal_date=str(reversal_date),
        )
        return reversal_entry


# Module-level singleton
double_entry_engine = DoubleEntryEngine()
