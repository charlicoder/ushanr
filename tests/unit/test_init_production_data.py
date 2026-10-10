"""
tests/unit/test_init_production_data.py
───────────────────────────────────────
Unit tests for production initialization data specifications.
"""
from __future__ import annotations

import pytest

from scripts.init_production_data import (
    COA_SPEC,
    CURRENCIES_SPEC,
    SEQUENCES_SPEC,
)
from app.models.account import AccountNature, AccountType


def test_currencies_spec_has_kwd_base():
    """Ensure KWD is defined as the base currency with 3 decimals."""
    kwd = next((c for c in CURRENCIES_SPEC if c["code"] == "KWD"), None)
    assert kwd is not None
    assert kwd["is_base"] is True
    assert kwd["decimal_places"] == 3


def test_coa_spec_critical_accounts_present():
    """Verify Accounts Receivable, Accounts Payable, and Revenue accounts are in COA."""
    codes = {item[0]: item for item in COA_SPEC}

    # AR
    assert "120000" in codes
    ar = codes["120000"]
    assert ar[2] == AccountType.ASSET.value
    assert ar[3] == AccountNature.DEBIT.value
    assert ar[4] is True  # is_reconcilable

    # AP
    assert "210000" in codes
    ap = codes["210000"]
    assert ap[2] == AccountType.LIABILITY.value
    assert ap[3] == AccountNature.CREDIT.value
    assert ap[4] is True  # is_reconcilable

    # Revenue
    assert "401000" in codes
    rev = codes["401000"]
    assert rev[2] == AccountType.REVENUE.value
    assert rev[3] == AccountNature.CREDIT.value


def test_sequences_spec_contains_all_document_types():
    """Verify sequences are configured for INV, BILL, RINV, RBILL, PAY, VPAY, MISC."""
    prefixes = {s["prefix"] for s in SEQUENCES_SPEC}
    expected = {"INV", "BILL", "RINV", "RBILL", "PAY", "VPAY", "MISC", "BNK"}
    assert expected.issubset(prefixes)
