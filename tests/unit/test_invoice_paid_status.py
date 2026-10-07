"""
tests/unit/test_invoice_paid_status.py
───────────────────────────────────────
Unit tests verifying that when an invoice record is created
and associated with a payment, if the invoice full amount is paid
successfully, the invoice status is set to 'paid'.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest

from app.models.invoice import Invoice, InvoiceState, InvoiceType
from app.services.invoice_service import (
    CreateInvoiceData,
    InvoiceLineData,
    InvoiceService,
)

COMPANY_ID = uuid4()
JOURNAL_ID = uuid4()
PARTNER_ID = uuid4()
ACCOUNT_ID = uuid4()


def _make_line(unit_price: Decimal = Decimal("50.000")) -> InvoiceLineData:
    return InvoiceLineData(
        account_id=ACCOUNT_ID,
        name="Spa Treatment",
        quantity=Decimal("1"),
        unit_price=unit_price,
    )


@pytest.mark.asyncio
async def test_create_invoice_with_is_paid_sets_state_to_paid():
    session = AsyncMock()
    # Mock execute for generating name (no existing invoices with prefix)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_result

    svc = InvoiceService(session)

    data = CreateInvoiceData(
        company_id=COMPANY_ID,
        partner_id=PARTNER_ID,
        journal_id=JOURNAL_ID,
        invoice_type=InvoiceType.INVOICE.value,
        invoice_date=date.today(),
        lines=[_make_line(Decimal("75.000"))],
        is_paid=True,
    )

    invoice = await svc.create_invoice(data)

    assert invoice.state == InvoiceState.PAID.value
    assert invoice.amount_total == 75.0
    assert invoice.amount_paid == 75.0
    assert invoice.amount_residual == 0.0


@pytest.mark.asyncio
async def test_create_invoice_with_payment_status_paid_sets_state_to_paid():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_result

    svc = InvoiceService(session)

    data = CreateInvoiceData(
        company_id=COMPANY_ID,
        partner_id=PARTNER_ID,
        journal_id=JOURNAL_ID,
        invoice_type=InvoiceType.INVOICE.value,
        invoice_date=date.today(),
        lines=[_make_line(Decimal("100.000"))],
        payment_status="paid",
    )

    invoice = await svc.create_invoice(data)

    assert invoice.state == InvoiceState.PAID.value
    assert invoice.amount_total == 100.0
    assert invoice.amount_paid == 100.0
    assert invoice.amount_residual == 0.0


@pytest.mark.asyncio
async def test_create_invoice_with_payment_status_success_sets_state_to_paid():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_result

    svc = InvoiceService(session)

    data = CreateInvoiceData(
        company_id=COMPANY_ID,
        partner_id=PARTNER_ID,
        journal_id=JOURNAL_ID,
        invoice_type=InvoiceType.INVOICE.value,
        invoice_date=date.today(),
        lines=[_make_line(Decimal("40.000"))],
        payment_status="success",
        amount_paid=Decimal("40.000"),
    )

    invoice = await svc.create_invoice(data)

    assert invoice.state == InvoiceState.PAID.value
    assert invoice.amount_total == 40.0
    assert invoice.amount_paid == 40.0
    assert invoice.amount_residual == 0.0


@pytest.mark.asyncio
async def test_create_invoice_with_partial_payment_sets_state_to_partial():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    session.execute.return_value = mock_result

    svc = InvoiceService(session)

    data = CreateInvoiceData(
        company_id=COMPANY_ID,
        partner_id=PARTNER_ID,
        journal_id=JOURNAL_ID,
        invoice_type=InvoiceType.INVOICE.value,
        invoice_date=date.today(),
        lines=[_make_line(Decimal("100.000"))],
        amount_paid=Decimal("30.000"),
    )

    invoice = await svc.create_invoice(data)

    assert invoice.state == InvoiceState.PARTIAL.value
    assert invoice.amount_total == 100.0
    assert invoice.amount_paid == 30.0
    assert invoice.amount_residual == 70.0


@pytest.mark.asyncio
async def test_create_invoice_unpaid_defaults_to_draft():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    # No matching payment in anr_payments
    mock_result.scalars.return_value.first.return_value = None
    session.execute.return_value = mock_result

    svc = InvoiceService(session)

    data = CreateInvoiceData(
        company_id=COMPANY_ID,
        partner_id=PARTNER_ID,
        journal_id=JOURNAL_ID,
        invoice_type=InvoiceType.INVOICE.value,
        invoice_date=date.today(),
        lines=[_make_line(Decimal("60.000"))],
    )

    invoice = await svc.create_invoice(data)

    assert invoice.state == InvoiceState.DRAFT.value
    assert invoice.amount_total == 60.0
    assert invoice.amount_paid == 0.0
    assert invoice.amount_residual == 60.0


@pytest.mark.asyncio
async def test_post_invoice_preserves_paid_state():
    session = AsyncMock()
    mock_engine = AsyncMock()
    mock_je = MagicMock()
    mock_je.id = uuid4()
    mock_engine.create_and_post_entry.return_value = mock_je

    svc = InvoiceService(session, engine=mock_engine)

    # Existing invoice that is already paid
    inv = Invoice(
        id=uuid4(),
        name="INV/2026/10/00001",
        company_id=COMPANY_ID,
        partner_id=PARTNER_ID,
        journal_id=JOURNAL_ID,
        invoice_type=InvoiceType.INVOICE.value,
        invoice_date=date.today(),
        state=InvoiceState.PAID.value,
        amount_untaxed=50.0,
        amount_tax=0.0,
        amount_total=50.0,
        amount_paid=50.0,
        amount_residual=0.0,
        currency_code="KWD",
    )
    inv.lines = [MagicMock(account_id=ACCOUNT_ID, subtotal=50.0, name="Service", analytic_account_id=None)]
    inv.taxes = []

    # Mock fetch invoice
    svc._fetch_invoice = AsyncMock(return_value=inv)
    # Mock ar_ap_account
    mock_ar = MagicMock(id=uuid4())
    svc._fetch_ar_ap_account = AsyncMock(return_value=mock_ar)

    posted_inv = await svc.post_invoice(inv.id, posted_by="system")

    # State should remain PAID, not reset to POSTED
    assert posted_inv.state == InvoiceState.PAID.value
    assert posted_inv.journal_entry_id == mock_je.id


@pytest.mark.asyncio
async def test_create_invoice_with_gateway_non_uuid_payment_id():
    """Verify that a non-UUID gateway payment reference (e.g. MyFatoorah '100624710000000255')
    does not raise a ValueError and correctly sets the invoice to paid."""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalars.return_value.first.return_value = None
    session.execute.return_value = mock_result

    svc = InvoiceService(session)

    data = CreateInvoiceData(
        company_id=COMPANY_ID,
        partner_id=PARTNER_ID,
        journal_id=JOURNAL_ID,
        invoice_type=InvoiceType.INVOICE.value,
        invoice_date=date.today(),
        lines=[_make_line(Decimal("85.000"))],
        is_paid=True,
        payment_id="100624710000000255",  # non-UUID gateway string
    )

    invoice = await svc.create_invoice(data)

    assert invoice.state == InvoiceState.PAID.value
    assert invoice.amount_total == 85.0
    assert invoice.amount_paid == 85.0


@pytest.mark.asyncio
async def test_endpoint_create_invoice_from_source_with_gateway_payment_id():
    """Verify create_invoice_from_source endpoint accepts non-UUID payment_id without error."""
    from app.api.v1.endpoints.internal import (
        CreateInvoiceFromSourceRequest,
        InvoiceLineIn,
        create_invoice_from_source,
    )
    from app.models.company import Company
    from app.models.journal import Journal

    session = AsyncMock()

    # 1. Existing invoice check -> None
    # 2. Company check -> mock company
    # 3. Journal check -> mock journal
    # 4. Line account check -> mock account
    # 5. _generate_invoice_name -> []
    mock_comp = Company(id=COMPANY_ID, name="USHSPA", currency_code="KWD", is_active=True)
    mock_jour = Journal(id=JOURNAL_ID, company_id=COMPANY_ID, name="Sales", code="INV", is_active=True)

    def mock_exec(stmt):
        m = MagicMock()
        # if existing invoice check, return None
        m.scalar_one_or_none.return_value = None
        m.scalars.return_value.all.return_value = []
        m.scalars.return_value.first.return_value = None
        return m

    session.execute = AsyncMock(side_effect=mock_exec)

    req = CreateInvoiceFromSourceRequest(
        company_id=COMPANY_ID,
        partner_id=PARTNER_ID,
        journal_id=JOURNAL_ID,
        invoice_date=date.today(),
        source_document_type="booking",
        source_document_id=str(uuid4()),
        currency_code="KWD",
        lines=[InvoiceLineIn(account_id=ACCOUNT_ID, name="Massage", unit_price=Decimal("40.000"))],
        is_paid=True,
        payment_id="100624710000000255",  # MyFatoorah gateway id
    )

    # Mock InvoiceService.post_invoice
    mock_invoice = MagicMock()
    mock_invoice.id = uuid4()
    mock_invoice.name = "INV/2026/10/00099"
    mock_invoice.state = "paid"
    mock_invoice.amount_total = Decimal("40.000")

    from unittest.mock import patch
    with patch("app.api.v1.endpoints.internal.InvoiceService") as MockSvc:
        instance = MockSvc.return_value
        instance.create_invoice = AsyncMock(return_value=mock_invoice)
        instance.post_invoice = AsyncMock(return_value=mock_invoice)

        resp = await create_invoice_from_source(body=req, session=session)
        assert resp.invoice_name == "INV/2026/10/00099"
        assert resp.state == "paid"

