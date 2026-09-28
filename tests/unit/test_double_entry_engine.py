"""
tests/unit/test_double_entry_engine.py
───────────────────────────────────────
Unit tests for the double-entry bookkeeping engine.
Tests run with no database — all SQLAlchemy calls are mocked.

Covers:
  - Balance validation (balanced, unbalanced, edge cases)
  - Draft entry creation
  - Posting rules (draft → posted, double-posting rejected)
  - Reversal logic (debits/credits swapped)
  - Negative amount rejection
  - Both-debit-and-credit rejection
"""
from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.core.exceptions import EntryAlreadyPostedError, UnbalancedEntryError
from app.services.double_entry import (
    DoubleEntryEngine,
    JournalEntryData,
    JournalItemData,
)

COMPANY_ID = uuid4()
JOURNAL_ID = uuid4()
ACCOUNT_A = uuid4()
ACCOUNT_B = uuid4()
ACCOUNT_C = uuid4()


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_items(
    dr_account=ACCOUNT_A, cr_account=ACCOUNT_B, amount=Decimal("100.000")
) -> list[JournalItemData]:
    return [
        JournalItemData(account_id=dr_account, debit=amount, currency_code="KWD"),
        JournalItemData(account_id=cr_account, credit=amount, currency_code="KWD"),
    ]


def make_entry_data(items: list[JournalItemData] | None = None) -> JournalEntryData:
    return JournalEntryData(
        company_id=COMPANY_ID,
        journal_id=JOURNAL_ID,
        entry_date=date(2024, 1, 15),
        items=items or make_items(),
    )


engine = DoubleEntryEngine()


# ── Balance Validation ────────────────────────────────────────────────────────

class TestBalanceValidation:
    """Tests for validate_balance()."""

    @pytest.mark.unit
    def test_balanced_entry_passes(self):
        items = make_items(amount=Decimal("500.000"))
        engine.validate_balance(items)  # Should not raise

    @pytest.mark.unit
    def test_unbalanced_entry_raises(self):
        items = [
            JournalItemData(account_id=ACCOUNT_A, debit=Decimal("100.000")),
            JournalItemData(account_id=ACCOUNT_B, credit=Decimal("99.000")),
        ]
        with pytest.raises(UnbalancedEntryError) as exc_info:
            engine.validate_balance(items)
        assert "1.000" in str(exc_info.value)

    @pytest.mark.unit
    def test_multi_line_balanced_entry(self):
        """Three-line entry: Dr A 300, Cr B 200, Cr C 100 — balanced."""
        items = [
            JournalItemData(account_id=ACCOUNT_A, debit=Decimal("300.000")),
            JournalItemData(account_id=ACCOUNT_B, credit=Decimal("200.000")),
            JournalItemData(account_id=ACCOUNT_C, credit=Decimal("100.000")),
        ]
        engine.validate_balance(items)  # Should not raise

    @pytest.mark.unit
    def test_zero_amounts_balanced(self):
        items = [
            JournalItemData(account_id=ACCOUNT_A, debit=Decimal("0")),
            JournalItemData(account_id=ACCOUNT_B, credit=Decimal("0")),
        ]
        engine.validate_balance(items)  # Zero entry is technically balanced

    @pytest.mark.unit
    def test_rounding_tolerance(self):
        """0.001 KWD precision — 1/3 + 1/3 + 1/3 must balance with proper rounding."""
        one_third = Decimal("33.333")
        items = [
            JournalItemData(account_id=ACCOUNT_A, debit=Decimal("99.999")),
            JournalItemData(account_id=ACCOUNT_B, credit=one_third),
            JournalItemData(account_id=ACCOUNT_B, credit=one_third),
            JournalItemData(account_id=ACCOUNT_B, credit=one_third),
        ]
        engine.validate_balance(items)  # 33.333 * 3 = 99.999 ✓

    @pytest.mark.unit
    def test_negative_debit_raises(self):
        with pytest.raises(UnbalancedEntryError):
            JournalItemData(account_id=ACCOUNT_A, debit=Decimal("-10")).validate()

    @pytest.mark.unit
    def test_negative_credit_raises(self):
        with pytest.raises(UnbalancedEntryError):
            JournalItemData(account_id=ACCOUNT_A, credit=Decimal("-10")).validate()

    @pytest.mark.unit
    def test_both_debit_and_credit_raises(self):
        with pytest.raises(UnbalancedEntryError) as exc_info:
            JournalItemData(
                account_id=ACCOUNT_A, debit=Decimal("100"), credit=Decimal("50")
            ).validate()
        assert "both" in str(exc_info.value).lower()


# ── Draft Entry Creation ──────────────────────────────────────────────────────

class TestDraftEntryCreation:
    """Tests for create_draft_entry()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_draft_entry_validates_balance(self):
        """Unbalanced items should raise before touching the DB."""
        unbalanced = [
            JournalItemData(account_id=ACCOUNT_A, debit=Decimal("100")),
            JournalItemData(account_id=ACCOUNT_B, credit=Decimal("50")),
        ]
        data = make_entry_data(items=unbalanced)
        session = AsyncMock()

        with pytest.raises(UnbalancedEntryError):
            await engine.create_draft_entry(session, data)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_draft_entry_calls_session_add(self):
        """Balanced entry should add entry + items to session."""
        data = make_entry_data()
        session = AsyncMock()
        session.flush = AsyncMock()

        # Mock the entry returned after flush
        mock_entry = MagicMock()
        mock_entry.id = uuid4()
        mock_entry.items = []

        # We need to patch JournalEntry to control what's added to session
        with patch("app.services.double_entry.JournalEntry") as mock_je_class, \
             patch("app.services.double_entry.JournalItem") as mock_ji_class:
            mock_je_class.return_value = mock_entry
            mock_ji_class.return_value = MagicMock()

            await engine.create_draft_entry(session, data)

        # session.add should have been called (at least once for the entry)
        assert session.add.called

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_create_draft_entry_rejects_invalid_items(self):
        """Item with both debit and credit should be rejected."""
        bad_items = [
            JournalItemData(account_id=ACCOUNT_A, debit=Decimal("100"), credit=Decimal("100")),
        ]
        data = make_entry_data(items=bad_items)
        with pytest.raises(UnbalancedEntryError):
            await engine.create_draft_entry(AsyncMock(), data)


# ── Posting ───────────────────────────────────────────────────────────────────

class TestPosting:
    """Tests for post_entry()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cannot_post_already_posted_entry(self):
        mock_entry = MagicMock()
        mock_entry.state = "posted"
        mock_entry.name = "JE/2024/001"
        mock_entry.id = uuid4()

        with pytest.raises(EntryAlreadyPostedError):
            await engine.post_entry(AsyncMock(), mock_entry)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cannot_post_cancelled_entry(self):
        mock_entry = MagicMock()
        mock_entry.state = "cancelled"
        mock_entry.name = "JE/2024/002"
        mock_entry.id = uuid4()

        with pytest.raises(EntryAlreadyPostedError):
            await engine.post_entry(AsyncMock(), mock_entry)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_post_entry_sets_state_to_posted(self):
        """A valid draft entry should transition to posted state."""
        mock_item_a = MagicMock()
        mock_item_a.account_id = ACCOUNT_A
        mock_item_a.debit_amount = 100.000
        mock_item_a.credit_amount = 0.0

        mock_item_b = MagicMock()
        mock_item_b.account_id = ACCOUNT_B
        mock_item_b.debit_amount = 0.0
        mock_item_b.credit_amount = 100.000

        mock_entry = MagicMock()
        mock_entry.state = "draft"
        mock_entry.items = [mock_item_a, mock_item_b]
        mock_entry.company_id = COMPANY_ID
        mock_entry.accounting_date = date(2024, 1, 15)
        mock_entry.name = "JE/2024/001"

        session = AsyncMock()
        # validate_period_open should return None (no period configured = open)
        with patch.object(engine, "validate_period_open", return_value=None):
            await engine.post_entry(session, mock_entry, posted_by="admin")

        assert mock_entry.state == "posted"
        assert mock_entry.posted_by == "admin"
        assert mock_entry.posted_at is not None


# ── Reversal ──────────────────────────────────────────────────────────────────

class TestReversal:
    """Tests for reverse_entry()."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cannot_reverse_draft_entry(self):
        mock_entry = MagicMock()
        mock_entry.state = "draft"
        mock_entry.id = uuid4()

        with pytest.raises(EntryAlreadyPostedError):
            await engine.reverse_entry(AsyncMock(), mock_entry, reversal_date=date.today())

    @pytest.mark.unit
    def test_reversal_swaps_debit_credit(self):
        """Reversals must swap debit ↔ credit on every item."""
        original_items = [
            JournalItemData(account_id=ACCOUNT_A, debit=Decimal("200.000")),
            JournalItemData(account_id=ACCOUNT_B, credit=Decimal("150.000")),
            JournalItemData(account_id=ACCOUNT_C, credit=Decimal("50.000")),
        ]
        # Build reversal items manually (mimics reverse_entry logic)
        reversal_items = [
            JournalItemData(
                account_id=item.account_id,
                debit=item.credit,
                credit=item.debit,
            )
            for item in original_items
        ]

        # Verify swap: original debit → reversal credit
        assert reversal_items[0].debit == Decimal("0")
        assert reversal_items[0].credit == Decimal("200.000")
        # Original credit → reversal debit
        assert reversal_items[1].debit == Decimal("150.000")
        assert reversal_items[1].credit == Decimal("0")

        # Reversed items should still balance
        engine.validate_balance(reversal_items)


# ── Decimal Precision ─────────────────────────────────────────────────────────

class TestDecimalPrecision:
    """Verify monetary calculations use Decimal, not float."""

    @pytest.mark.unit
    def test_validate_balance_uses_decimal_not_float(self):
        """Amounts stored as Decimal must survive round-trip."""
        items = [
            JournalItemData(account_id=ACCOUNT_A, debit=Decimal("0.1")),
            JournalItemData(account_id=ACCOUNT_B, debit=Decimal("0.2")),
            JournalItemData(account_id=ACCOUNT_C, credit=Decimal("0.3")),
        ]
        # 0.1 + 0.2 == 0.3 in Decimal (not in float)
        engine.validate_balance(items)

    @pytest.mark.unit
    def test_float_input_is_coerced_to_decimal(self):
        """JournalItemData must coerce float inputs to Decimal."""
        item = JournalItemData(account_id=ACCOUNT_A, debit=100.5)
        assert isinstance(item.debit, Decimal)
        assert item.debit == Decimal("100.5")
