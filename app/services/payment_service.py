"""
app/services/payment_service.py
─────────────────────────────────
Payment management service — receipt and disbursement of funds.

Payment lifecycle:
  DRAFT → POSTED → RECONCILED | CANCELLED

Accounting entries on POST:

  Inbound (customer payment received):
      Dr  Bank / Cash account          amount
          Cr  Accounts Receivable          amount

  Outbound (vendor payment sent):
      Dr  Accounts Payable             amount
          Cr  Bank / Cash account          amount

On allocate_payment():
  - PaymentAllocation record is created linking payment ↔ invoice.
  - invoice.amount_paid and invoice.amount_residual are updated.
  - payment.amount_residual is updated.
  - If invoice.amount_residual == 0 → invoice.state = PAID.
  - If 0 < invoice.amount_residual < invoice.amount_total → state = PARTIAL.
  - If payment.amount_residual == 0 → payment.state = RECONCILED.

Design notes:
  - No FastAPI dependency.
  - All monetary arithmetic uses Decimal.
  - DoubleEntryEngine handles JournalEntry creation.
  - Journal.payment_debit_account_id / payment_credit_account_id
    are used when set (bank/cash accounts on the journal).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AllocationExceedsBalanceError,
    ForbiddenOperationError,
    PaymentNotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.models.account import Account, AccountType
from app.models.invoice import Invoice, InvoiceState, InvoiceType
from app.models.journal import Journal
from app.models.journal_entry import JournalEntry
from app.models.payment import (
    Payment,
    PaymentAllocation,
    PaymentState,
    PaymentType,
)
from app.services.double_entry import (
    DoubleEntryEngine,
    JournalEntryData,
    JournalItemData,
    double_entry_engine,
)

logger = get_logger(__name__)

ZERO = Decimal("0")
PRECISION = Decimal("0.001")


# ── Input dataclasses ─────────────────────────────────────────────────────────


@dataclass
class CreatePaymentData:
    """Input for creating a new payment."""
    company_id: UUID
    journal_id: UUID
    payment_type: str              # PaymentType value: "inbound" or "outbound"
    amount: Decimal
    payment_date: date
    currency_code: str = "KWD"
    partner_id: UUID | None = None
    partner_name: str | None = None
    payment_method: str | None = None
    payment_provider: str | None = None
    reference: str | None = None
    notes: str | None = None
    # External gateway fields
    payment_id_external: str | None = None
    transaction_id: str | None = None
    invoice_id_external: str | None = None
    reference_id: str | None = None
    track_id: str | None = None
    payment_data: dict | None = None
    created_by: str | None = None
    created_by_name: str | None = None
    is_refund: bool = False
    refund_number: str | None = None
    cancellation_fee: Decimal = ZERO

    def __post_init__(self) -> None:
        self.amount = Decimal(str(self.amount))
        self.cancellation_fee = Decimal(str(self.cancellation_fee or "0"))
        valid_types = {t.value for t in PaymentType}
        if self.payment_type not in valid_types:
            raise ValidationError(
                f"Invalid payment_type '{self.payment_type}'. "
                f"Valid values: {sorted(valid_types)}"
            )
        if self.amount <= ZERO:
            raise ValidationError("Payment amount must be greater than zero.")


@dataclass
class PaymentFilters:
    """Filters for listing payments."""
    company_id: UUID | None = None
    partner_id: UUID | None = None
    payment_type: str | None = None
    state: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    journal_id: UUID | None = None
    limit: int = 100
    offset: int = 0


# ── Service ───────────────────────────────────────────────────────────────────


class PaymentService:
    """
    Async service for payment creation, posting, allocation, and cancellation.

    Usage::

        async with async_session() as session:
            svc = PaymentService(session)
            payment = await svc.create_payment(data)
            payment = await svc.post_payment(payment.id, posted_by="user@example.com")
            allocation = await svc.allocate_payment(payment.id, invoice.id, amount)
    """

    def __init__(
        self,
        session: AsyncSession,
        engine: DoubleEntryEngine | None = None,
    ) -> None:
        self._session = session
        self._engine = engine or double_entry_engine

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _fetch_payment(
        self, payment_id: UUID, company_id: UUID | None = None
    ) -> Payment:
        """Fetch payment with optional company scope guard."""
        stmt = select(Payment).where(Payment.id == payment_id)
        if company_id is not None:
            stmt = stmt.where(Payment.company_id == company_id)

        result = await self._session.execute(stmt)
        payment = result.scalar_one_or_none()
        if payment is None:
            raise PaymentNotFoundError(
                f"Payment {payment_id} not found.",
                detail={"payment_id": str(payment_id)},
            )
        return payment

    async def _fetch_journal(self, journal_id: UUID) -> Journal:
        """Fetch a journal by ID."""
        result = await self._session.execute(
            select(Journal).where(Journal.id == journal_id)
        )
        journal = result.scalar_one_or_none()
        if journal is None:
            raise ValidationError(
                f"Journal {journal_id} not found.",
                detail={"journal_id": str(journal_id)},
            )
        return journal

    async def _fetch_bank_account(
        self, company_id: UUID, journal: Journal, payment_type: str
    ) -> Account:
        """
        Resolve the bank/cash account for a payment journal.

        Priority:
          1. journal.payment_debit_account_id  (inbound) /
             journal.payment_credit_account_id (outbound)
          2. journal.default_account_id
          3. First active ASSET+is_bank_account account for company

        For inbound payments the debit side is bank; for outbound the credit side is bank.
        """
        # Prefer the explicit payment account from the journal
        if payment_type == PaymentType.INBOUND.value:
            account_id = journal.payment_debit_account_id or journal.default_account_id
        else:
            account_id = journal.payment_credit_account_id or journal.default_account_id

        if account_id:
            result = await self._session.execute(
                select(Account).where(Account.id == account_id)
            )
            account = result.scalar_one_or_none()
            if account:
                return account

        # Fallback: first active bank account for the company
        result = await self._session.execute(
            select(Account).where(
                Account.company_id == company_id,
                Account.is_bank_account.is_(True),
                Account.is_active.is_(True),
                Account.is_deleted.is_(False),
            ).limit(1)
        )
        account = result.scalar_one_or_none()
        if account is None:
            raise ValidationError(
                f"No bank/cash account found for company {company_id}. "
                "Please configure a bank account on the payment journal."
            )
        return account

    async def _fetch_ar_ap_account(
        self, company_id: UUID, payment_type: str, is_refund: bool = False
    ) -> Account:
        """
        Resolve AR (inbound or customer refund) or AP (outbound vendor) account for the company.

        Uses first reconcilable ASSET (AR) or LIABILITY (AP) account found.
        """
        if payment_type == PaymentType.INBOUND.value or is_refund:
            acct_type = AccountType.ASSET.value
        else:
            acct_type = AccountType.LIABILITY.value

        result = await self._session.execute(
            select(Account).where(
                Account.company_id == company_id,
                Account.account_type == acct_type,
                Account.is_reconcilable.is_(True),
                Account.is_active.is_(True),
                Account.is_deleted.is_(False),
            ).limit(1)
        )
        account = result.scalar_one_or_none()
        if account is None:
            raise ValidationError(
                f"No {'AR' if (payment_type == PaymentType.INBOUND.value or is_refund) else 'AP'} account "
                f"found for company {company_id}."
            )
        return account

    # ── CRUD ──────────────────────────────────────────────────────────────────

    async def create_payment(self, data: CreatePaymentData) -> Payment:
        """
        Create a DRAFT payment record.

        No journal entry is created at this stage.
        Call post_payment() to record the accounting entry.

        Raises:
            ValidationError: Invalid payment_type or amount ≤ 0.
        """
        amount = data.amount.quantize(PRECISION, rounding=ROUND_HALF_UP)

        payment = Payment(
            company_id=data.company_id,
            journal_id=data.journal_id,
            payment_type=data.payment_type,
            state=PaymentState.DRAFT.value,
            amount=float(amount),
            amount_residual=float(amount),
            currency_code=data.currency_code,
            payment_date=data.payment_date,
            partner_id=data.partner_id,
            partner_name=data.partner_name,
            payment_method=data.payment_method,
            payment_provider=data.payment_provider,
            reference=data.reference,
            notes=data.notes,
            payment_id_external=data.payment_id_external,
            transaction_id=data.transaction_id,
            invoice_id_external=data.invoice_id_external,
            reference_id=data.reference_id,
            track_id=data.track_id,
            payment_data=data.payment_data,
            created_by=data.created_by,
            created_by_name=data.created_by_name,
            is_refund=data.is_refund,
            refund_number=data.refund_number,
            cancellation_fee=float(data.cancellation_fee),
        )
        self._session.add(payment)
        await self._session.flush()

        logger.info(
            "payment_created",
            payment_id=str(payment.id),
            payment_type=data.payment_type,
            amount=float(amount),
            company_id=str(data.company_id),
        )
        return payment

    async def post_payment(
        self,
        payment_id: UUID,
        posted_by: str,
        company_id: UUID | None = None,
    ) -> Payment:
        """
        Post a DRAFT payment — creates the double-entry JournalEntry.

        Inbound (customer payment):
            Dr  Bank / Cash              amount
                Cr  Accounts Receivable      amount

        Outbound (vendor payment):
            Dr  Accounts Payable         amount
                Cr  Bank / Cash              amount

        Raises:
            PaymentNotFoundError:    Payment not found.
            ForbiddenOperationError: Payment not in DRAFT state.
            ValidationError:         Missing bank or AR/AP account.
        """
        payment = await self._fetch_payment(payment_id, company_id)

        if payment.state != PaymentState.DRAFT.value:
            raise ForbiddenOperationError(
                f"Payment {payment.name or payment_id} is in state '{payment.state}'. "
                "Only DRAFT payments can be posted.",
                detail={"state": payment.state},
            )

        amount = Decimal(str(payment.amount)).quantize(PRECISION, rounding=ROUND_HALF_UP)

        # Resolve accounts
        is_refund = bool(getattr(payment, "is_refund", False))
        journal = await self._fetch_journal(payment.journal_id)
        bank_account = await self._fetch_bank_account(
            payment.company_id, journal, payment.payment_type
        )
        ar_ap_account = await self._fetch_ar_ap_account(
            payment.company_id, payment.payment_type, is_refund=is_refund
        )

        if payment.payment_type == PaymentType.INBOUND.value:
            # Dr Bank, Cr AR
            journal_items = [
                JournalItemData(
                    account_id=bank_account.id,
                    debit=amount,
                    credit=ZERO,
                    name=f"Payment received: {payment.name or payment_id}",
                    partner_id=payment.partner_id,
                    currency_code=payment.currency_code,
                ),
                JournalItemData(
                    account_id=ar_ap_account.id,
                    debit=ZERO,
                    credit=amount,
                    name=f"AR cleared: {payment.name or payment_id}",
                    partner_id=payment.partner_id,
                    currency_code=payment.currency_code,
                ),
            ]
        elif is_refund:
            # Outbound customer refund: Dr AR, Cr Bank
            ref_label = payment.refund_number or payment.name or payment_id
            journal_items = [
                JournalItemData(
                    account_id=ar_ap_account.id,
                    debit=amount,
                    credit=ZERO,
                    name=f"Customer refund: {ref_label}",
                    partner_id=payment.partner_id,
                    currency_code=payment.currency_code,
                ),
                JournalItemData(
                    account_id=bank_account.id,
                    debit=ZERO,
                    credit=amount,
                    name=f"Bank disbursed (refund): {ref_label}",
                    partner_id=payment.partner_id,
                    currency_code=payment.currency_code,
                ),
            ]
        else:
            # Outbound: Dr AP, Cr Bank
            journal_items = [
                JournalItemData(
                    account_id=ar_ap_account.id,
                    debit=amount,
                    credit=ZERO,
                    name=f"AP cleared: {payment.name or payment_id}",
                    partner_id=payment.partner_id,
                    currency_code=payment.currency_code,
                ),
                JournalItemData(
                    account_id=bank_account.id,
                    debit=ZERO,
                    credit=amount,
                    name=f"Payment sent: {payment.name or payment_id}",
                    partner_id=payment.partner_id,
                    currency_code=payment.currency_code,
                ),
            ]

        entry_data = JournalEntryData(
            company_id=payment.company_id,
            journal_id=payment.journal_id,
            entry_date=payment.payment_date,
            accounting_date=payment.payment_date,
            items=journal_items,
            name=payment.name,
            reference=payment.reference,
            narration=(
                f"{'Customer' if payment.payment_type == PaymentType.INBOUND.value else 'Vendor'} "
                f"payment: {payment.name or payment_id}"
            ),
            partner_id=payment.partner_id,
            currency_code=payment.currency_code,
            source_document_type="payment",
            source_document_id=payment.id,
            posted_by=posted_by,
        )

        journal_entry = await self._engine.create_and_post_entry(
            self._session, entry_data
        )

        payment.journal_entry_id = journal_entry.id
        payment.state = PaymentState.POSTED.value
        self._session.add(payment)
        await self._session.flush()

        logger.info(
            "payment_posted",
            payment_id=str(payment_id),
            journal_entry_id=str(journal_entry.id),
            posted_by=posted_by,
        )
        return payment

    async def allocate_payment(
        self,
        payment_id: UUID,
        invoice_id: UUID,
        amount: Decimal,
        company_id: UUID | None = None,
        allocation_date: date | None = None,
    ) -> PaymentAllocation:
        """
        Allocate (reconcile) a portion or all of a payment against an invoice.

        Updates:
          - invoice.amount_paid += amount
          - invoice.amount_residual -= amount
          - invoice.state → PAID or PARTIAL
          - payment.amount_residual -= amount
          - payment.state → RECONCILED if fully used

        Args:
            payment_id:      Payment to allocate from.
            invoice_id:      Invoice to allocate against.
            amount:          Decimal amount to allocate (must be > 0).
            company_id:      Optional company scope guard.
            allocation_date: Date of allocation (defaults to today).

        Returns:
            PaymentAllocation record.

        Raises:
            PaymentNotFoundError:       Payment not found.
            ValidationError:            Amount ≤ 0, Invoice not found.
            AllocationExceedsBalanceError: Amount exceeds payment or invoice residual.
            ForbiddenOperationError:    Payment not POSTED, or invoice not POSTED/PARTIAL.
        """
        amount = Decimal(str(amount)).quantize(PRECISION, rounding=ROUND_HALF_UP)
        if amount <= ZERO:
            raise ValidationError("Allocation amount must be greater than zero.")

        payment = await self._fetch_payment(payment_id, company_id)

        if payment.state not in {PaymentState.POSTED.value, PaymentState.RECONCILED.value}:
            raise ForbiddenOperationError(
                f"Payment {payment.name or payment_id} must be POSTED to allocate.",
                detail={"state": payment.state},
            )

        # Fetch invoice
        inv_stmt = select(Invoice).where(Invoice.id == invoice_id)
        if company_id is not None:
            inv_stmt = inv_stmt.where(Invoice.company_id == company_id)
        inv_result = await self._session.execute(inv_stmt)
        invoice = inv_result.scalar_one_or_none()
        if invoice is None:
            raise ValidationError(
                f"Invoice {invoice_id} not found.",
                detail={"invoice_id": str(invoice_id)},
            )

        if invoice.state not in {InvoiceState.POSTED.value, InvoiceState.PARTIAL.value}:
            raise ForbiddenOperationError(
                f"Invoice {invoice.name or invoice_id} must be in POSTED or PARTIAL state. "
                f"Current state: {invoice.state}",
                detail={"state": invoice.state},
            )

        # Validate residuals
        payment_residual = Decimal(str(payment.amount_residual)).quantize(
            PRECISION, rounding=ROUND_HALF_UP
        )
        invoice_residual = Decimal(str(invoice.amount_residual)).quantize(
            PRECISION, rounding=ROUND_HALF_UP
        )

        if amount > payment_residual:
            raise AllocationExceedsBalanceError(
                f"Allocation amount {amount} exceeds payment residual {payment_residual}.",
                detail={
                    "payment_residual": str(payment_residual),
                    "requested": str(amount),
                },
            )

        if amount > invoice_residual:
            raise AllocationExceedsBalanceError(
                f"Allocation amount {amount} exceeds invoice residual {invoice_residual}.",
                detail={
                    "invoice_residual": str(invoice_residual),
                    "requested": str(amount),
                },
            )

        # Create allocation record
        alloc = PaymentAllocation(
            payment_id=payment.id,
            invoice_id=invoice.id,
            amount=float(amount),
            currency_code=payment.currency_code,
            allocation_date=allocation_date or date.today(),
        )
        self._session.add(alloc)

        # Update payment residual
        new_payment_residual = (payment_residual - amount).quantize(
            PRECISION, rounding=ROUND_HALF_UP
        )
        payment.amount_residual = float(new_payment_residual)
        if new_payment_residual == ZERO:
            payment.state = PaymentState.RECONCILED.value
        self._session.add(payment)

        # Update invoice paid/residual
        new_invoice_paid = (
            Decimal(str(invoice.amount_paid)) + amount
        ).quantize(PRECISION, rounding=ROUND_HALF_UP)
        new_invoice_residual = (invoice_residual - amount).quantize(
            PRECISION, rounding=ROUND_HALF_UP
        )
        invoice.amount_paid = float(new_invoice_paid)
        invoice.amount_residual = float(new_invoice_residual)

        if new_invoice_residual == ZERO:
            invoice.state = InvoiceState.PAID.value
        elif new_invoice_residual < Decimal(str(invoice.amount_total)):
            invoice.state = InvoiceState.PARTIAL.value

        self._session.add(invoice)
        await self._session.flush()

        logger.info(
            "payment_allocated",
            payment_id=str(payment_id),
            invoice_id=str(invoice_id),
            amount=float(amount),
            payment_residual=float(new_payment_residual),
            invoice_residual=float(new_invoice_residual),
        )
        return alloc

    async def cancel_payment(
        self,
        payment_id: UUID,
        company_id: UUID | None = None,
        cancelled_by: str | None = None,
        cancellation_date: date | None = None,
    ) -> Payment:
        """
        Cancel a payment.

        - DRAFT: Simply mark cancelled (no journal entry exists).
        - POSTED: Create a full reversal journal entry.

        If the payment had allocations, the invoice residuals must first be
        reversed by the caller before cancelling (this service checks for
        existing allocations and raises if any are found on POSTED payments).

        Raises:
            PaymentNotFoundError:    Payment not found.
            ForbiddenOperationError: Payment already cancelled/reconciled,
                                     or has existing allocations.
        """
        payment = await self._fetch_payment(payment_id, company_id)

        if payment.state == PaymentState.CANCELLED.value:
            raise ForbiddenOperationError(
                f"Payment {payment.name or payment_id} is already cancelled."
            )

        if payment.state == PaymentState.RECONCILED.value:
            raise ForbiddenOperationError(
                f"Payment {payment.name or payment_id} is fully reconciled. "
                "Reverse all allocations before cancelling.",
                detail={"state": payment.state},
            )

        # Check for existing allocations on posted payments
        if payment.state == PaymentState.POSTED.value:
            alloc_result = await self._session.execute(
                select(PaymentAllocation.id).where(
                    PaymentAllocation.payment_id == payment.id
                ).limit(1)
            )
            if alloc_result.scalar_one_or_none() is not None:
                raise ForbiddenOperationError(
                    f"Payment {payment.name or payment_id} has allocations. "
                    "Reverse all allocations before cancelling.",
                    detail={"payment_id": str(payment_id)},
                )

            # Reverse the journal entry
            if payment.journal_entry_id:
                entry_result = await self._session.execute(
                    select(JournalEntry).where(
                        JournalEntry.id == payment.journal_entry_id
                    )
                )
                entry = entry_result.scalar_one_or_none()
                if entry is not None:
                    reversal_date = cancellation_date or date.today()
                    await self._engine.reverse_entry(
                        self._session,
                        entry=entry,
                        reversal_date=reversal_date,
                        reversal_narration=(
                            f"Cancellation of payment {payment.name or payment_id}"
                        ),
                        posted_by=cancelled_by,
                    )

        payment.state = PaymentState.CANCELLED.value
        self._session.add(payment)
        await self._session.flush()

        logger.info(
            "payment_cancelled",
            payment_id=str(payment_id),
            cancelled_by=cancelled_by,
        )
        return payment

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_payment(
        self, payment_id: UUID, company_id: UUID | None = None
    ) -> Payment:
        """
        Fetch a single payment by ID.

        Raises:
            PaymentNotFoundError: Payment not found.
        """
        return await self._fetch_payment(payment_id, company_id)

    async def list_payments(self, filters: PaymentFilters) -> list[Payment]:
        """
        Return a filtered, paginated list of payments.

        Ordered by payment_date DESC, created_at DESC.
        """
        stmt = select(Payment)

        if filters.company_id is not None:
            stmt = stmt.where(Payment.company_id == filters.company_id)
        if filters.partner_id is not None:
            stmt = stmt.where(Payment.partner_id == filters.partner_id)
        if filters.payment_type is not None:
            stmt = stmt.where(Payment.payment_type == filters.payment_type)
        if filters.state is not None:
            stmt = stmt.where(Payment.state == filters.state)
        if filters.journal_id is not None:
            stmt = stmt.where(Payment.journal_id == filters.journal_id)
        if filters.date_from is not None:
            stmt = stmt.where(Payment.payment_date >= filters.date_from)
        if filters.date_to is not None:
            stmt = stmt.where(Payment.payment_date <= filters.date_to)

        stmt = (
            stmt.order_by(Payment.payment_date.desc(), Payment.created_at.desc())
            .limit(filters.limit)
            .offset(filters.offset)
        )

        result = await self._session.execute(stmt)
        return list(result.scalars().all())
