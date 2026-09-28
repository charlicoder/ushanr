"""
tests/unit/test_models.py
──────────────────────────
Unit tests for ORM model properties and helper methods.
No database required — tests pure Python logic.
"""
from __future__ import annotations

import pytest
from app.models.account import Account, AccountNature, AccountType, ACCOUNT_NATURE_MAP
from app.models.journal_entry import EntryState, JournalEntry, JournalItem
from app.models.sequence import DocumentSequence
from app.models.invoice import InvoiceState, InvoiceType


class TestAccountModel:
    @pytest.mark.unit
    def test_asset_is_debit_normal(self):
        account = Account()
        account.account_nature = AccountNature.DEBIT.value
        assert account.is_debit_normal is True

    @pytest.mark.unit
    def test_liability_is_not_debit_normal(self):
        account = Account()
        account.account_nature = AccountNature.CREDIT.value
        assert account.is_debit_normal is False

    @pytest.mark.unit
    def test_account_nature_map_covers_all_types(self):
        for acct_type in AccountType:
            assert acct_type.value in ACCOUNT_NATURE_MAP, (
                f"AccountType.{acct_type.name} missing from ACCOUNT_NATURE_MAP"
            )

    @pytest.mark.unit
    def test_asset_debit_nature(self):
        assert ACCOUNT_NATURE_MAP[AccountType.ASSET.value] == AccountNature.DEBIT.value

    @pytest.mark.unit
    def test_liability_credit_nature(self):
        assert ACCOUNT_NATURE_MAP[AccountType.LIABILITY.value] == AccountNature.CREDIT.value

    @pytest.mark.unit
    def test_revenue_credit_nature(self):
        assert ACCOUNT_NATURE_MAP[AccountType.REVENUE.value] == AccountNature.CREDIT.value

    @pytest.mark.unit
    def test_expense_debit_nature(self):
        assert ACCOUNT_NATURE_MAP[AccountType.EXPENSE.value] == AccountNature.DEBIT.value


class TestJournalEntryModel:
    @pytest.mark.unit
    def test_posted_entry_is_not_editable(self):
        entry = JournalEntry()
        entry.state = EntryState.POSTED.value
        assert entry.is_posted is True
        assert entry.is_editable is False

    @pytest.mark.unit
    def test_draft_entry_is_editable(self):
        entry = JournalEntry()
        entry.state = EntryState.DRAFT.value
        assert entry.is_posted is False
        assert entry.is_editable is True

    @pytest.mark.unit
    def test_cancelled_entry_is_not_editable(self):
        entry = JournalEntry()
        entry.state = EntryState.CANCELLED.value
        assert entry.is_editable is False


class TestDocumentSequence:
    @pytest.mark.unit
    def test_get_next_value_with_prefix_and_date(self):
        seq = DocumentSequence()
        seq.prefix = "INV"
        seq.next_number = 42
        seq.padding = 4
        seq.use_date_range = True
        seq.suffix = None

        from datetime import datetime
        year = datetime.now().year
        result = seq.get_next_value()
        assert result == f"INV/{year}/0042"

    @pytest.mark.unit
    def test_get_next_value_without_date(self):
        seq = DocumentSequence()
        seq.prefix = "PMT"
        seq.next_number = 1
        seq.padding = 6
        seq.use_date_range = False
        seq.suffix = None

        result = seq.get_next_value()
        assert result == "PMT/000001"

    @pytest.mark.unit
    def test_get_next_value_zero_padded(self):
        seq = DocumentSequence()
        seq.prefix = "JE"
        seq.next_number = 5
        seq.padding = 4
        seq.use_date_range = False
        seq.suffix = None

        result = seq.get_next_value()
        assert result == "JE/0005"


class TestInvoiceEnums:
    @pytest.mark.unit
    def test_invoice_type_values(self):
        assert InvoiceType.INVOICE.value == "invoice"
        assert InvoiceType.CREDIT_NOTE.value == "credit_note"
        assert InvoiceType.BILL.value == "bill"
        assert InvoiceType.VENDOR_CREDIT.value == "vendor_credit"

    @pytest.mark.unit
    def test_invoice_state_values(self):
        assert InvoiceState.DRAFT.value == "draft"
        assert InvoiceState.POSTED.value == "posted"
        assert InvoiceState.PAID.value == "paid"
        assert InvoiceState.CANCELLED.value == "cancelled"
