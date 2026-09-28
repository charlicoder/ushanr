"""
app/core/exceptions.py
───────────────────────
Domain exception hierarchy for ushanr.
All business/accounting errors inherit from ANRBaseError.
"""
from __future__ import annotations

from typing import Any


class ANRBaseError(Exception):
    """Base error for all ushanr domain errors."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        detail: Any = None,
        code: str | None = None,
    ) -> None:
        self.message = message
        self.detail = detail
        if code:
            self.code = code
        super().__init__(message)


# ── HTTP 400 ──────────────────────────────────────────────────────────────────

class ValidationError(ANRBaseError):
    status_code = 400
    code = "VALIDATION_ERROR"


class InvalidAccountingPeriodError(ANRBaseError):
    status_code = 400
    code = "INVALID_ACCOUNTING_PERIOD"


class LockedPeriodError(ANRBaseError):
    """Raised when attempting to post to a locked accounting period."""
    status_code = 400
    code = "PERIOD_LOCKED"


class UnbalancedEntryError(ANRBaseError):
    """Raised when a journal entry does not balance (debits ≠ credits)."""
    status_code = 400
    code = "UNBALANCED_ENTRY"


class DuplicateDocumentError(ANRBaseError):
    status_code = 400
    code = "DUPLICATE_DOCUMENT"


class InvalidCurrencyError(ANRBaseError):
    status_code = 400
    code = "INVALID_CURRENCY"


class InsufficientBalanceError(ANRBaseError):
    status_code = 400
    code = "INSUFFICIENT_BALANCE"


class AllocationExceedsBalanceError(ANRBaseError):
    status_code = 400
    code = "ALLOCATION_EXCEEDS_BALANCE"


# ── HTTP 403 ──────────────────────────────────────────────────────────────────

class ForbiddenOperationError(ANRBaseError):
    status_code = 403
    code = "FORBIDDEN"


# ── HTTP 404 ──────────────────────────────────────────────────────────────────

class NotFoundError(ANRBaseError):
    status_code = 404
    code = "NOT_FOUND"


class AccountNotFoundError(NotFoundError):
    code = "ACCOUNT_NOT_FOUND"


class JournalNotFoundError(NotFoundError):
    code = "JOURNAL_NOT_FOUND"


class JournalEntryNotFoundError(NotFoundError):
    code = "JOURNAL_ENTRY_NOT_FOUND"


class PartnerNotFoundError(NotFoundError):
    code = "PARTNER_NOT_FOUND"


class InvoiceNotFoundError(NotFoundError):
    code = "INVOICE_NOT_FOUND"


class PaymentNotFoundError(NotFoundError):
    code = "PAYMENT_NOT_FOUND"


class FiscalYearNotFoundError(NotFoundError):
    code = "FISCAL_YEAR_NOT_FOUND"


# ── HTTP 409 ──────────────────────────────────────────────────────────────────

class ConflictError(ANRBaseError):
    status_code = 409
    code = "CONFLICT"


class EntryAlreadyPostedError(ConflictError):
    """Raised when trying to edit a posted journal entry directly."""
    code = "ENTRY_ALREADY_POSTED"


class InvoiceAlreadyPaidError(ConflictError):
    code = "INVOICE_ALREADY_PAID"


# ── HTTP 422 ──────────────────────────────────────────────────────────────────

class AccountTypeError(ANRBaseError):
    """Raised when an account is used with an incompatible type."""
    status_code = 422
    code = "ACCOUNT_TYPE_ERROR"


# ── HTTP 500 ──────────────────────────────────────────────────────────────────

class ImportError(ANRBaseError):
    status_code = 500
    code = "IMPORT_ERROR"


class ReportGenerationError(ANRBaseError):
    status_code = 500
    code = "REPORT_GENERATION_ERROR"
