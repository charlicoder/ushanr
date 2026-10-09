"""
tests/unit/test_credit_note_and_refund.py
──────────────────────────────────────────
Unit tests for Credit Note creation and customer refund accounting:
1. Credit note reversing posted/paid invoice
2. Cancellation fee retention
3. Outbound customer refund payment (Dr AR, Cr Bank)
4. Payment allocation reconciles Credit Note (state -> PAID)
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.endpoints.internal import CreditNoteRequest, create_credit_note
from app.models.account import Account, AccountType
from app.models.invoice import Invoice, InvoiceLine, InvoiceState, InvoiceType
from app.models.journal import Journal, JournalType
from app.models.payment import Payment, PaymentAllocation, PaymentState, PaymentType
from app.services.invoice_service import CreateInvoiceData, InvoiceLineData, InvoiceService
from app.services.payment_service import CreatePaymentData, PaymentService


@pytest.mark.asyncio
async def test_outbound_refund_payment_debits_ar():
    """Verify that outbound customer refund debits AR (not AP)."""
    company_id = uuid.uuid4()
    journal_id = uuid.uuid4()
    bank_account_id = uuid.uuid4()
    ar_account_id = uuid.uuid4()

    journal = Journal(id=journal_id, company_id=company_id, name="Bank Journal", code="BNK1", journal_type=JournalType.BANK.value)
    bank_account = Account(id=bank_account_id, company_id=company_id, code="1010", name="Bank", account_type=AccountType.ASSET.value, is_bank_account=True)
    ar_account = Account(id=ar_account_id, company_id=company_id, code="1200", name="Accounts Receivable", account_type=AccountType.ASSET.value, is_reconcilable=True)

    mock_session = AsyncMock()

    payment = Payment(
        id=uuid.uuid4(),
        company_id=company_id,
        journal_id=journal_id,
        payment_type=PaymentType.OUTBOUND.value,
        amount=60.000,
        amount_residual=60.000,
        currency_code="KWD",
        payment_date=date.today(),
        state=PaymentState.DRAFT.value,
        is_refund=True,
        refund_number="REF/2026/10/000001",
    )

    svc = PaymentService(mock_session)

    with patch.object(svc, "_fetch_payment", return_value=payment), \
         patch.object(svc, "_fetch_journal", return_value=journal), \
         patch.object(svc, "_fetch_bank_account", return_value=bank_account), \
         patch.object(svc, "_fetch_ar_ap_account", return_value=ar_account), \
         patch("app.services.payment_service.double_entry_engine.create_and_post_entry", new_callable=AsyncMock) as mock_engine:

        mock_entry = MagicMock()
        mock_entry.id = uuid.uuid4()
        mock_engine.return_value = mock_entry

        posted_pmt = await svc.post_payment(payment.id, posted_by="ushnotice")

        assert posted_pmt.state == PaymentState.POSTED.value
        mock_engine.assert_awaited_once()

        # Check journal items passed to double_entry_engine
        entry_data = mock_engine.await_args[0][1]
        items = entry_data.items
        assert len(items) == 2

        # Item 1: Debit AR
        dr_item = next(it for it in items if it.debit > 0)
        assert dr_item.account_id == ar_account_id
        assert dr_item.debit == Decimal("60.000")
        assert "Customer refund" in dr_item.name

        # Item 2: Credit Bank
        cr_item = next(it for it in items if it.credit > 0)
        assert cr_item.account_id == bank_account_id
        assert cr_item.credit == Decimal("60.000")
        assert "Bank disbursed" in cr_item.name


@pytest.mark.asyncio
async def test_create_credit_note_with_cancellation_fee():
    """Verify that credit note deducts cancellation fee and creates outbound refund payment."""
    company_id = uuid.uuid4()
    journal_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    partner_id = uuid.uuid4()
    revenue_account_id = uuid.uuid4()

    original_invoice = Invoice(
        id=invoice_id,
        name="INV/2026/10/00001",
        company_id=company_id,
        partner_id=partner_id,
        journal_id=journal_id,
        invoice_type=InvoiceType.INVOICE.value,
        invoice_date=date.today(),
        state=InvoiceState.PAID.value,
        amount_total=60.000,
        amount_paid=60.000,
        amount_residual=0.000,
        currency_code="KWD",
        source_document_type="booking",
        source_document_id="booking-123",
        lines=[
            InvoiceLine(
                id=uuid.uuid4(),
                invoice_id=invoice_id,
                account_id=revenue_account_id,
                name="Spa Massage Service",
                quantity=1.0,
                unit_price=60.000,
                discount=0.0,
                subtotal=60.000,
                total=60.000,
            )
        ],
    )

    mock_session = AsyncMock()

    call_count = 0

    async def mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        mock_res = MagicMock()
        stmt_str = str(stmt).lower()
        if "from journals" in stmt_str:
            mock_res.scalar_one_or_none.return_value = Journal(
                id=journal_id, company_id=company_id, name="Cash", code="CSH", journal_type=JournalType.CASH.value
            )
        elif call_count == 1:
            mock_res.scalar_one_or_none.return_value = original_invoice
        else:
            mock_res.scalar_one_or_none.return_value = None
        return mock_res

    mock_session.execute.side_effect = mock_execute

    created_cn = Invoice(
        id=uuid.uuid4(),
        name="CN/2026/10/00001",
        company_id=company_id,
        partner_id=partner_id,
        invoice_type=InvoiceType.CREDIT_NOTE.value,
        state=InvoiceState.POSTED.value,
        amount_total=50.000,
        amount_paid=0.000,
        amount_residual=50.000,
        currency_code="KWD",
    )

    created_pmt = Payment(
        id=uuid.uuid4(),
        company_id=company_id,
        journal_id=journal_id,
        payment_type=PaymentType.OUTBOUND.value,
        amount=50.000,
        amount_residual=50.000,
        state=PaymentState.DRAFT.value,
        is_refund=True,
    )

    posted_pmt = Payment(
        id=created_pmt.id,
        company_id=company_id,
        journal_id=journal_id,
        payment_type=PaymentType.OUTBOUND.value,
        amount=50.000,
        amount_residual=50.000,
        state=PaymentState.POSTED.value,
        is_refund=True,
    )

    alloc = PaymentAllocation(
        payment_id=created_pmt.id,
        invoice_id=created_cn.id,
        amount=50.000,
        allocation_date=date.today(),
    )

    with patch("app.services.invoice_service.InvoiceService.create_invoice", new_callable=AsyncMock) as mock_create_inv, \
         patch("app.services.invoice_service.InvoiceService.post_invoice", new_callable=AsyncMock) as mock_post_inv, \
         patch("app.services.payment_service.PaymentService.create_payment", new_callable=AsyncMock) as mock_create_pmt, \
         patch("app.services.payment_service.PaymentService.post_payment", new_callable=AsyncMock) as mock_post_pmt, \
         patch("app.services.payment_service.PaymentService.allocate_payment", new_callable=AsyncMock) as mock_alloc:

        mock_create_inv.return_value = created_cn
        mock_post_inv.return_value = created_cn
        mock_create_pmt.return_value = created_pmt
        mock_post_pmt.return_value = posted_pmt
        mock_alloc.return_value = alloc

        req = CreditNoteRequest(
            company_id=company_id,
            journal_id=journal_id,
            source_document_type="booking",
            source_document_id="booking-123",
            cancellation_fee=Decimal("10.000"),  # 10 KWD cancellation fee
            refund_amount=Decimal("50.000"),
            refund_method="cash",
            refund_number="REF/2026/10/000002",
        )

        resp = await create_credit_note(
            body=req,
            _=None,
            session=mock_session,
        )

        assert resp.invoice_name == "CN/2026/10/00001"
        assert resp.amount_total == Decimal("50.000")

        # Verify Credit note creation parameters had net unit_price = 50 KWD
        cn_data_arg = mock_create_inv.await_args[0][0]
        assert cn_data_arg.lines[0].unit_price == Decimal("50.000")

        # Verify Outbound payment was created and posted
        mock_create_pmt.assert_awaited_once()
        pmt_arg = mock_create_pmt.await_args[0][0]
        assert pmt_arg.amount == Decimal("50.000")
        assert pmt_arg.is_refund is True
        assert pmt_arg.refund_number == "REF/2026/10/000002"

        # Verify payment was allocated against the credit note
        mock_alloc.assert_awaited_once()
