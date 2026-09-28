"""
app/services/invoice_service.py
─────────────────────────────────
Full invoice lifecycle management service.

State machine:
  DRAFT → CONFIRMED → POSTED → PAID | PARTIAL | CANCELLED

Accounting entries created on POST (for customer invoices):
  Dr  Accounts Receivable (AR)   amount_total
      Cr  Revenue account(s)          amount_untaxed  (per line)
      Cr  Tax Payable                 amount_tax       (per tax group)

For vendor bills (BILL / VENDOR_CREDIT) the entries are mirrored:
  Dr  Expense / COGS account(s)  amount_untaxed  (per line)
  Dr  Tax Receivable              amount_tax
      Cr  Accounts Payable (AP)       amount_total

Credit notes reverse the original invoice entry automatically.

Design notes:
  - No FastAPI dependency.
  - All monetary arithmetic uses Decimal throughout.
  - Input validated via dataclasses.
  - DoubleEntryEngine handles the actual JournalEntry creation.
  - Due-date computation honours PaymentTerms enum values.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    ForbiddenOperationError,
    InvoiceNotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.models.account import Account, AccountType
from app.models.invoice import (
    Invoice,
    InvoiceLine,
    InvoiceState,
    InvoiceTax,
    InvoiceType,
    PaymentTerms,
)
from app.models.journal_entry import EntryState, JournalEntry
from app.models.journal import Journal
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
class InvoiceLineData:
    """Input data for a single invoice line item."""
    account_id: UUID
    name: str
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal = ZERO          # e.g. Decimal("0.05") for 5%
    discount: Decimal = ZERO          # e.g. Decimal("10") for 10%
    tax_id: UUID | None = None
    analytic_account_id: UUID | None = None
    description: str | None = None
    product_id: str | None = None
    product_code: str | None = None
    sequence: int = 10

    def __post_init__(self) -> None:
        self.quantity = Decimal(str(self.quantity))
        self.unit_price = Decimal(str(self.unit_price))
        self.tax_rate = Decimal(str(self.tax_rate))
        self.discount = Decimal(str(self.discount))

    def compute_subtotal(self) -> Decimal:
        """Subtotal before tax, after discount."""
        gross = self.quantity * self.unit_price
        discount_amount = gross * (self.discount / Decimal("100"))
        return (gross - discount_amount).quantize(PRECISION, rounding=ROUND_HALF_UP)

    def compute_tax_amount(self) -> Decimal:
        """Tax amount for this line."""
        return (self.compute_subtotal() * self.tax_rate).quantize(PRECISION, rounding=ROUND_HALF_UP)

    def compute_total(self) -> Decimal:
        """Subtotal + tax."""
        return (self.compute_subtotal() + self.compute_tax_amount()).quantize(
            PRECISION, rounding=ROUND_HALF_UP
        )


@dataclass
class CreateInvoiceData:
    """Input for creating a new invoice or bill."""
    company_id: UUID
    partner_id: UUID
    journal_id: UUID
    invoice_type: str                          # InvoiceType value
    invoice_date: date
    lines: list[InvoiceLineData]

    # Optional fields
    payment_terms: str = PaymentTerms.IMMEDIATE.value
    payment_terms_days: int = 0
    currency_code: str = "KWD"
    reference: str | None = None
    notes: str | None = None
    due_date: date | None = None               # overrides payment_terms if set
    source_document_type: str | None = None
    source_document_id: str | None = None
    source_document_ref: str | None = None
    accounting_date: date | None = None

    def __post_init__(self) -> None:
        valid_types = {t.value for t in InvoiceType}
        if self.invoice_type not in valid_types:
            raise ValidationError(
                f"Invalid invoice_type '{self.invoice_type}'. "
                f"Valid values: {sorted(valid_types)}"
            )
        if not self.lines:
            raise ValidationError("Invoice must have at least one line.")


@dataclass
class InvoiceFilters:
    """Filters for listing invoices."""
    company_id: UUID | None = None
    partner_id: UUID | None = None
    invoice_type: str | None = None
    state: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None
    journal_id: UUID | None = None
    limit: int = 100
    offset: int = 0


# ── Service ───────────────────────────────────────────────────────────────────


class InvoiceService:
    """
    Full invoice lifecycle management.

    Requires:
        session: AsyncSession — SQLAlchemy async session (transaction managed by caller)
        engine:  DoubleEntryEngine — bookkeeping engine (defaults to module singleton)

    Usage::

        async with async_session() as session:
            svc = InvoiceService(session)
            invoice = await svc.create_invoice(data)
            invoice = await svc.post_invoice(invoice.id, posted_by="user@example.com")
    """

    def __init__(
        self,
        session: AsyncSession,
        engine: DoubleEntryEngine | None = None,
    ) -> None:
        self._session = session
        self._engine = engine or double_entry_engine

    # ── Due Date ──────────────────────────────────────────────────────────────

    @staticmethod
    def compute_due_date(
        invoice_date: date,
        payment_terms: str,
        terms_days: int = 0,
    ) -> date:
        """
        Compute the due date based on payment terms.

        Args:
            invoice_date:  Date the invoice was issued.
            payment_terms: PaymentTerms enum value string.
            terms_days:    Custom days, used when payment_terms == CUSTOM.

        Returns:
            The calculated due date.
        """
        if payment_terms == PaymentTerms.IMMEDIATE.value:
            return invoice_date
        elif payment_terms == PaymentTerms.NET_30.value:
            return invoice_date + timedelta(days=30)
        elif payment_terms == PaymentTerms.NET_60.value:
            return invoice_date + timedelta(days=60)
        elif payment_terms == PaymentTerms.NET_90.value:
            return invoice_date + timedelta(days=90)
        elif payment_terms == PaymentTerms.CUSTOM.value:
            return invoice_date + timedelta(days=max(0, terms_days))
        else:
            return invoice_date

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _is_customer_doc(self, invoice_type: str) -> bool:
        """Returns True for customer-facing documents (INVOICE, CREDIT_NOTE)."""
        return invoice_type in {InvoiceType.INVOICE.value, InvoiceType.CREDIT_NOTE.value}

    def _is_credit_type(self, invoice_type: str) -> bool:
        """Returns True for credit / reversal documents."""
        return invoice_type in {InvoiceType.CREDIT_NOTE.value, InvoiceType.VENDOR_CREDIT.value}

    async def _fetch_invoice(
        self, invoice_id: UUID, company_id: UUID | None = None
    ) -> Invoice:
        """Fetch invoice with company scope guard."""
        stmt = select(Invoice).where(
            Invoice.id == invoice_id,
            Invoice.is_deleted.is_(False),
        )
        if company_id is not None:
            stmt = stmt.where(Invoice.company_id == company_id)

        result = await self._session.execute(stmt)
        invoice = result.scalar_one_or_none()
        if invoice is None:
            raise InvoiceNotFoundError(
                f"Invoice {invoice_id} not found.",
                detail={"invoice_id": str(invoice_id)},
            )
        return invoice

    def _compute_totals(
        self, lines: list[InvoiceLineData]
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Returns (amount_untaxed, amount_tax, amount_total)."""
        untaxed = sum((ln.compute_subtotal() for ln in lines), ZERO)
        tax = sum((ln.compute_tax_amount() for ln in lines), ZERO)
        total = untaxed + tax
        return (
            untaxed.quantize(PRECISION, rounding=ROUND_HALF_UP),
            tax.quantize(PRECISION, rounding=ROUND_HALF_UP),
            total.quantize(PRECISION, rounding=ROUND_HALF_UP),
        )

    def _build_tax_breakdown(
        self, lines: list[InvoiceLineData]
    ) -> dict[str, dict[str, Decimal]]:
        """
        Aggregate tax per tax_id (or rate for anonymous taxes).
        Returns {tax_key: {"base": Decimal, "tax": Decimal}}.
        """
        breakdown: dict[str, dict[str, Decimal]] = {}
        for ln in lines:
            if ln.tax_rate == ZERO:
                continue
            key = str(ln.tax_id) if ln.tax_id else f"rate_{ln.tax_rate}"
            if key not in breakdown:
                breakdown[key] = {"base": ZERO, "tax": ZERO, "tax_id": ln.tax_id}
            breakdown[key]["base"] += ln.compute_subtotal()
            breakdown[key]["tax"] += ln.compute_tax_amount()
        return breakdown

    async def _fetch_ar_ap_account(
        self, company_id: UUID, invoice_type: str
    ) -> Account:
        """
        Fetch the default AR or AP account for a company.

        Looks for account_type=ASSET+is_reconcilable (AR) or
        account_type=LIABILITY+is_reconcilable (AP).

        In production this would use journal.default_account_id;
        here we query the first suitable account for simplicity.
        """
        if self._is_customer_doc(invoice_type):
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
                f"No {'AR' if self._is_customer_doc(invoice_type) else 'AP'} account "
                f"found for company {company_id}. "
                "Please configure a reconcilable account of the correct type."
            )
        return account

    # ── CRUD ──────────────────────────────────────────────────────────────────

    async def create_invoice(self, data: CreateInvoiceData) -> Invoice:
        """
        Create a DRAFT invoice with computed line amounts.

        Computes per-line subtotal/tax/total and invoice-level totals.
        Sets due_date based on payment_terms (can be overridden by data.due_date).

        Raises:
            ValidationError: Bad input (invalid type, no lines, amounts ≤ 0).
        """
        amount_untaxed, amount_tax, amount_total = self._compute_totals(data.lines)

        if amount_total <= ZERO:
            raise ValidationError("Invoice total must be greater than zero.")

        # Compute due date
        computed_due = self.compute_due_date(
            data.invoice_date, data.payment_terms, data.payment_terms_days
        )
        effective_due = data.due_date or computed_due

        invoice = Invoice(
            company_id=data.company_id,
            partner_id=data.partner_id,
            journal_id=data.journal_id,
            invoice_type=data.invoice_type,
            invoice_date=data.invoice_date,
            due_date=effective_due,
            accounting_date=data.accounting_date or data.invoice_date,
            payment_terms=data.payment_terms,
            payment_terms_days=data.payment_terms_days,
            currency_code=data.currency_code,
            reference=data.reference,
            notes=data.notes,
            state=InvoiceState.DRAFT.value,
            amount_untaxed=float(amount_untaxed),
            amount_tax=float(amount_tax),
            amount_total=float(amount_total),
            amount_paid=0.0,
            amount_residual=float(amount_total),
            source_document_type=data.source_document_type,
            source_document_id=data.source_document_id,
            source_document_ref=data.source_document_ref,
            is_reversal=False,
        )
        self._session.add(invoice)
        await self._session.flush()  # populate invoice.id

        # Create invoice lines
        for i, line_data in enumerate(data.lines):
            subtotal = line_data.compute_subtotal()
            tax_amount = line_data.compute_tax_amount()
            total = line_data.compute_total()

            line = InvoiceLine(
                company_id=data.company_id,
                invoice_id=invoice.id,
                account_id=line_data.account_id,
                analytic_account_id=line_data.analytic_account_id,
                tax_id=line_data.tax_id,
                name=line_data.name,
                description=line_data.description,
                quantity=float(line_data.quantity),
                unit_price=float(line_data.unit_price),
                discount=float(line_data.discount),
                tax_rate=float(line_data.tax_rate),
                subtotal=float(subtotal),
                tax_amount=float(tax_amount),
                total=float(total),
                product_id=line_data.product_id,
                product_code=line_data.product_code,
                sequence=line_data.sequence or (i + 1) * 10,
            )
            self._session.add(line)

        # Create tax summary lines
        tax_breakdown = self._build_tax_breakdown(data.lines)
        for tax_key, tax_data in tax_breakdown.items():
            inv_tax = InvoiceTax(
                invoice_id=invoice.id,
                tax_id=tax_data["tax_id"] or uuid.uuid4(),  # placeholder if no tax_id
                tax_name=tax_key,
                base_amount=float(tax_data["base"].quantize(PRECISION, rounding=ROUND_HALF_UP)),
                tax_amount=float(tax_data["tax"].quantize(PRECISION, rounding=ROUND_HALF_UP)),
            )
            self._session.add(inv_tax)

        await self._session.flush()

        logger.info(
            "invoice_created",
            invoice_id=str(invoice.id),
            invoice_type=data.invoice_type,
            company_id=str(data.company_id),
            amount_total=float(amount_total),
        )
        return invoice

    async def confirm_invoice(
        self, invoice_id: UUID, company_id: UUID | None = None
    ) -> Invoice:
        """
        Transition invoice from DRAFT → CONFIRMED.

        Confirmed invoices have been reviewed but not yet posted to the GL.
        No journal entries are created at this stage.

        Raises:
            InvoiceNotFoundError: Invoice not found.
            ForbiddenOperationError: Invoice not in DRAFT state.
        """
        invoice = await self._fetch_invoice(invoice_id, company_id)

        if invoice.state != InvoiceState.DRAFT.value:
            raise ForbiddenOperationError(
                f"Invoice {invoice.name or invoice_id} is in state '{invoice.state}'. "
                "Only DRAFT invoices can be confirmed.",
                detail={"state": invoice.state},
            )

        invoice.state = InvoiceState.CONFIRMED.value
        self._session.add(invoice)
        await self._session.flush()

        logger.info("invoice_confirmed", invoice_id=str(invoice_id))
        return invoice

    async def post_invoice(
        self,
        invoice_id: UUID,
        posted_by: str,
        company_id: UUID | None = None,
    ) -> Invoice:
        """
        Post a CONFIRMED (or DRAFT) invoice to the General Ledger.

        Creates a double-entry JournalEntry:

        Customer Invoice (INVOICE):
            Dr  Accounts Receivable     amount_total
                Cr  Revenue (per line)      subtotal  (per line account)
                Cr  Tax Payable             tax amount (per tax group)

        Customer Credit Note (CREDIT_NOTE) — reversed:
            Dr  Revenue (per line)      subtotal
            Dr  Tax Payable             tax amount
                Cr  Accounts Receivable     amount_total

        Vendor Bill (BILL):
            Dr  Expense (per line)      subtotal
            Dr  Tax Receivable          tax amount
                Cr  Accounts Payable        amount_total

        Vendor Credit (VENDOR_CREDIT) — reversed:
            Dr  Accounts Payable        amount_total
                Cr  Expense (per line)      subtotal
                Cr  Tax Receivable          tax amount

        Raises:
            InvoiceNotFoundError: Invoice not found.
            ForbiddenOperationError: Invoice already posted / cancelled.
            ValidationError: No AR/AP account, or lines empty.
        """
        invoice = await self._fetch_invoice(invoice_id, company_id)

        allowed_states = {InvoiceState.DRAFT.value, InvoiceState.CONFIRMED.value}
        if invoice.state not in allowed_states:
            raise ForbiddenOperationError(
                f"Invoice {invoice.name or invoice_id} is in state '{invoice.state}'. "
                "Only DRAFT or CONFIRMED invoices can be posted.",
                detail={"state": invoice.state},
            )

        # Reload lines (eager-loaded via selectin on relationship)
        lines: list[InvoiceLine] = invoice.lines
        if not lines:
            raise ValidationError("Cannot post an invoice with no lines.")

        is_customer = self._is_customer_doc(invoice.invoice_type)
        is_credit = self._is_credit_type(invoice.invoice_type)

        # Fetch the controlling AR / AP account
        ar_ap_account = await self._fetch_ar_ap_account(
            invoice.company_id, invoice.invoice_type
        )

        amount_total = Decimal(str(invoice.amount_total))

        # Build journal items
        journal_items: list[JournalItemData] = []

        if is_customer and not is_credit:
            # Standard invoice: Dr AR, Cr Revenue + Tax
            journal_items.append(
                JournalItemData(
                    account_id=ar_ap_account.id,
                    debit=amount_total,
                    credit=ZERO,
                    name=f"AR: {invoice.name or invoice_id}",
                    partner_id=invoice.partner_id,
                    due_date=invoice.due_date,
                    currency_code=invoice.currency_code,
                )
            )
            for line in lines:
                subtotal = Decimal(str(line.subtotal))
                if subtotal != ZERO:
                    journal_items.append(
                        JournalItemData(
                            account_id=line.account_id,
                            debit=ZERO,
                            credit=subtotal,
                            name=line.name,
                            partner_id=invoice.partner_id,
                            analytic_account_id=line.analytic_account_id,
                            currency_code=invoice.currency_code,
                        )
                    )
            # Tax payable lines
            for inv_tax in invoice.taxes:
                tax_amount = Decimal(str(inv_tax.tax_amount))
                if tax_amount != ZERO:
                    # Use the first revenue account as a proxy for tax payable account
                    # (in a real system, taxes would have their own account mapping)
                    tax_account_id = lines[0].account_id
                    journal_items.append(
                        JournalItemData(
                            account_id=tax_account_id,
                            debit=ZERO,
                            credit=tax_amount,
                            name=f"Tax: {inv_tax.tax_name}",
                            partner_id=invoice.partner_id,
                            currency_code=invoice.currency_code,
                        )
                    )

        elif is_customer and is_credit:
            # Credit note: Dr Revenue + Tax, Cr AR
            journal_items.append(
                JournalItemData(
                    account_id=ar_ap_account.id,
                    debit=ZERO,
                    credit=amount_total,
                    name=f"AR Credit: {invoice.name or invoice_id}",
                    partner_id=invoice.partner_id,
                    currency_code=invoice.currency_code,
                )
            )
            for line in lines:
                subtotal = Decimal(str(line.subtotal))
                if subtotal != ZERO:
                    journal_items.append(
                        JournalItemData(
                            account_id=line.account_id,
                            debit=subtotal,
                            credit=ZERO,
                            name=line.name,
                            partner_id=invoice.partner_id,
                            analytic_account_id=line.analytic_account_id,
                            currency_code=invoice.currency_code,
                        )
                    )
            for inv_tax in invoice.taxes:
                tax_amount = Decimal(str(inv_tax.tax_amount))
                if tax_amount != ZERO:
                    tax_account_id = lines[0].account_id
                    journal_items.append(
                        JournalItemData(
                            account_id=tax_account_id,
                            debit=tax_amount,
                            credit=ZERO,
                            name=f"Tax: {inv_tax.tax_name}",
                            partner_id=invoice.partner_id,
                            currency_code=invoice.currency_code,
                        )
                    )

        elif not is_customer and not is_credit:
            # Vendor bill: Dr Expense + Tax, Cr AP
            journal_items.append(
                JournalItemData(
                    account_id=ar_ap_account.id,
                    debit=ZERO,
                    credit=amount_total,
                    name=f"AP: {invoice.name or invoice_id}",
                    partner_id=invoice.partner_id,
                    due_date=invoice.due_date,
                    currency_code=invoice.currency_code,
                )
            )
            for line in lines:
                subtotal = Decimal(str(line.subtotal))
                if subtotal != ZERO:
                    journal_items.append(
                        JournalItemData(
                            account_id=line.account_id,
                            debit=subtotal,
                            credit=ZERO,
                            name=line.name,
                            partner_id=invoice.partner_id,
                            analytic_account_id=line.analytic_account_id,
                            currency_code=invoice.currency_code,
                        )
                    )
            for inv_tax in invoice.taxes:
                tax_amount = Decimal(str(inv_tax.tax_amount))
                if tax_amount != ZERO:
                    tax_account_id = lines[0].account_id
                    journal_items.append(
                        JournalItemData(
                            account_id=tax_account_id,
                            debit=tax_amount,
                            credit=ZERO,
                            name=f"Tax: {inv_tax.tax_name}",
                            partner_id=invoice.partner_id,
                            currency_code=invoice.currency_code,
                        )
                    )

        else:
            # Vendor credit: Dr AP, Cr Expense + Tax
            journal_items.append(
                JournalItemData(
                    account_id=ar_ap_account.id,
                    debit=amount_total,
                    credit=ZERO,
                    name=f"AP Credit: {invoice.name or invoice_id}",
                    partner_id=invoice.partner_id,
                    currency_code=invoice.currency_code,
                )
            )
            for line in lines:
                subtotal = Decimal(str(line.subtotal))
                if subtotal != ZERO:
                    journal_items.append(
                        JournalItemData(
                            account_id=line.account_id,
                            debit=ZERO,
                            credit=subtotal,
                            name=line.name,
                            partner_id=invoice.partner_id,
                            analytic_account_id=line.analytic_account_id,
                            currency_code=invoice.currency_code,
                        )
                    )
            for inv_tax in invoice.taxes:
                tax_amount = Decimal(str(inv_tax.tax_amount))
                if tax_amount != ZERO:
                    tax_account_id = lines[0].account_id
                    journal_items.append(
                        JournalItemData(
                            account_id=tax_account_id,
                            debit=ZERO,
                            credit=tax_amount,
                            name=f"Tax: {inv_tax.tax_name}",
                            partner_id=invoice.partner_id,
                            currency_code=invoice.currency_code,
                        )
                    )

        entry_data = JournalEntryData(
            company_id=invoice.company_id,
            journal_id=invoice.journal_id,
            entry_date=invoice.invoice_date,
            accounting_date=invoice.accounting_date or invoice.invoice_date,
            items=journal_items,
            name=invoice.name,
            reference=invoice.reference,
            narration=f"{invoice.invoice_type.upper()}: {invoice.name or invoice_id}",
            partner_id=invoice.partner_id,
            currency_code=invoice.currency_code,
            source_document_type="invoice",
            source_document_id=invoice.id,
            posted_by=posted_by,
        )

        journal_entry = await self._engine.create_and_post_entry(
            self._session, entry_data
        )

        # Link the entry back to the invoice
        invoice.journal_entry_id = journal_entry.id
        invoice.state = InvoiceState.POSTED.value
        self._session.add(invoice)
        await self._session.flush()

        logger.info(
            "invoice_posted",
            invoice_id=str(invoice_id),
            journal_entry_id=str(journal_entry.id),
            posted_by=posted_by,
        )
        return invoice

    async def cancel_invoice(
        self,
        invoice_id: UUID,
        company_id: UUID | None = None,
        cancelled_by: str | None = None,
        cancellation_date: date | None = None,
    ) -> Invoice:
        """
        Cancel an invoice.

        - DRAFT / CONFIRMED: Simply mark cancelled.
        - POSTED: Create a full reversal journal entry (Dr/Cr swapped).

        Raises:
            InvoiceNotFoundError: Invoice not found.
            ForbiddenOperationError: Invoice already paid or already cancelled.
        """
        invoice = await self._fetch_invoice(invoice_id, company_id)

        if invoice.state == InvoiceState.CANCELLED.value:
            raise ForbiddenOperationError(
                f"Invoice {invoice.name or invoice_id} is already cancelled."
            )

        if invoice.state in {InvoiceState.PAID.value, InvoiceState.PARTIAL.value}:
            raise ForbiddenOperationError(
                f"Invoice {invoice.name or invoice_id} has payments applied "
                f"(state={invoice.state}). Reverse the payments first.",
                detail={"state": invoice.state},
            )

        if invoice.state == InvoiceState.POSTED.value and invoice.journal_entry_id:
            # Fetch and reverse the original journal entry
            entry_result = await self._session.execute(
                select(JournalEntry).where(JournalEntry.id == invoice.journal_entry_id)
            )
            entry = entry_result.scalar_one_or_none()
            if entry is not None:
                reversal_date = cancellation_date or date.today()
                await self._engine.reverse_entry(
                    self._session,
                    entry=entry,
                    reversal_date=reversal_date,
                    reversal_narration=f"Cancellation of {invoice.name or invoice_id}",
                    posted_by=cancelled_by,
                )

        invoice.state = InvoiceState.CANCELLED.value
        self._session.add(invoice)
        await self._session.flush()

        logger.info(
            "invoice_cancelled",
            invoice_id=str(invoice_id),
            cancelled_by=cancelled_by,
        )
        return invoice

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_invoice(
        self, invoice_id: UUID, company_id: UUID | None = None
    ) -> Invoice:
        """
        Fetch a single invoice with all lines and tax summaries loaded.

        Raises:
            InvoiceNotFoundError: Invoice not found.
        """
        return await self._fetch_invoice(invoice_id, company_id)

    async def list_invoices(self, filters: InvoiceFilters) -> list[Invoice]:
        """
        Return a filtered, paginated list of invoices.

        Ordered by invoice_date DESC, created_at DESC.
        """
        stmt = select(Invoice).where(Invoice.is_deleted.is_(False))

        if filters.company_id is not None:
            stmt = stmt.where(Invoice.company_id == filters.company_id)
        if filters.partner_id is not None:
            stmt = stmt.where(Invoice.partner_id == filters.partner_id)
        if filters.invoice_type is not None:
            stmt = stmt.where(Invoice.invoice_type == filters.invoice_type)
        if filters.state is not None:
            stmt = stmt.where(Invoice.state == filters.state)
        if filters.journal_id is not None:
            stmt = stmt.where(Invoice.journal_id == filters.journal_id)
        if filters.date_from is not None:
            stmt = stmt.where(Invoice.invoice_date >= filters.date_from)
        if filters.date_to is not None:
            stmt = stmt.where(Invoice.invoice_date <= filters.date_to)
        if filters.due_from is not None:
            stmt = stmt.where(Invoice.due_date >= filters.due_from)
        if filters.due_to is not None:
            stmt = stmt.where(Invoice.due_date <= filters.due_to)

        stmt = (
            stmt.order_by(Invoice.invoice_date.desc(), Invoice.created_at.desc())
            .limit(filters.limit)
            .offset(filters.offset)
        )

        result = await self._session.execute(stmt)
        return list(result.scalars().all())
