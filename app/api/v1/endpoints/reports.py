"""
app/api/v1/endpoints/reports.py
─────────────────────────────────
Financial reporting endpoints.

GET /api/v1/reports/general-ledger/   — General Ledger
GET /api/v1/reports/trial-balance/    — Trial Balance
GET /api/v1/reports/profit-loss/      — Profit & Loss
GET /api/v1/reports/balance-sheet/    — Balance Sheet
GET /api/v1/reports/ar-aging/         — Accounts Receivable Aging
GET /api/v1/reports/ap-aging/         — Accounts Payable Aging
GET /api/v1/reports/cash-flow/        — Simplified Cash Flow
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, and_, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_optional_company_id
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.account import Account, AccountType
from app.models.invoice import Invoice, InvoiceState, InvoiceType
from app.models.journal_entry import EntryState, JournalEntry, JournalItem
from app.models.partner import Partner
from app.utils.timezone import local_today

logger = get_logger(__name__)
router = APIRouter()

ZERO = Decimal("0")


@router.get("/general-ledger/", summary="General Ledger report")
async def general_ledger(
    company_id: UUID | None = Depends(get_optional_company_id),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    account_id: list[UUID] | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Returns all posted journal items in date range, grouped by account.
    """
    if company_id is None:
        raise HTTPException(status_code=400, detail="Company ID required")
    if date_to is None:
        date_to = local_today()
    if date_from is None:
        date_from = date(2020, 1, 1)
    q = (
        select(
            JournalItem.id,
            JournalEntry.accounting_date.label("date"),
            JournalEntry.name.label("entry_name"),
            Account.code.label("account_code"),
            Account.name.label("account_name"),
            Account.account_type,
            Account.account_nature,
            JournalItem.name.label("description"),
            JournalItem.debit_amount,
            JournalItem.credit_amount,
            JournalItem.currency_code,
        )
        .join(JournalEntry, JournalItem.entry_id == JournalEntry.id)
        .join(Account, JournalItem.account_id == Account.id)
        .where(
            JournalItem.company_id == company_id,
            JournalEntry.state == EntryState.POSTED.value,
            JournalEntry.accounting_date >= date_from,
            JournalEntry.accounting_date <= date_to,
        )
        .order_by(Account.code, JournalEntry.accounting_date, JournalEntry.id)
    )
    if account_id:
        q = q.where(JournalItem.account_id.in_(account_id))

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).all()

    # Compute running balance per account
    lines = []
    running: dict[str, Decimal] = {}
    for row in rows:
        key = row.account_code
        prev = running.get(key, ZERO)
        dr = Decimal(str(row.debit_amount))
        cr = Decimal(str(row.credit_amount))
        if row.account_nature == "debit":
            running[key] = prev + dr - cr
        else:
            running[key] = prev + cr - dr
        lines.append({
            "date": row.date.isoformat() if row.date else None,
            "entry_name": row.entry_name,
            "account_code": row.account_code,
            "account_name": row.account_name,
            "account_type": row.account_type,
            "description": row.description,
            "debit": float(dr),
            "credit": float(cr),
            "balance": float(running[key]),
        })

    return {
        "success": True,
        "data": {
            "lines": lines,
            "total": total,
            "page": page,
            "page_size": page_size,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        },
    }


@router.get("/trial-balance/", summary="Trial Balance report")
async def trial_balance(
    company_id: UUID | None = Depends(get_optional_company_id),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Returns debit/credit totals per account for the period.
    """
    if company_id is None:
        raise HTTPException(status_code=400, detail="Company ID required")
    if date_to is None:
        date_to = local_today()
    if date_from is None:
        date_from = date(2020, 1, 1)
    q = (
        select(
            Account.id.label("account_id"),
            Account.code,
            Account.name,
            Account.account_type,
            Account.account_nature,
            func.coalesce(func.sum(JournalItem.debit_amount), 0).label("period_debit"),
            func.coalesce(func.sum(JournalItem.credit_amount), 0).label("period_credit"),
        )
        .join(JournalItem, JournalItem.account_id == Account.id, isouter=True)
        .join(
            JournalEntry,
            and_(
                JournalItem.entry_id == JournalEntry.id,
                JournalEntry.state == EntryState.POSTED.value,
                JournalEntry.accounting_date >= date_from,
                JournalEntry.accounting_date <= date_to,
            ),
            isouter=True,
        )
        .where(Account.company_id == company_id, Account.is_deleted == False)
        .group_by(Account.id, Account.code, Account.name, Account.account_type, Account.account_nature)
        .order_by(Account.code)
    )
    rows = (await db.execute(q)).all()

    lines = []
    total_dr = ZERO
    total_cr = ZERO
    for row in rows:
        dr = Decimal(str(row.period_debit))
        cr = Decimal(str(row.period_credit))
        if row.account_nature == "debit":
            balance = dr - cr
        else:
            balance = cr - dr
        total_dr += dr
        total_cr += cr
        lines.append({
            "account_id": str(row.account_id),
            "account_code": row.code,
            "account_name": row.name,
            "account_type": row.account_type,
            "period_debit": float(dr),
            "period_credit": float(cr),
            "balance": float(balance),
        })

    return {
        "success": True,
        "data": {
            "lines": lines,
            "totals": {"total_debit": float(total_dr), "total_credit": float(total_cr)},
            "is_balanced": total_dr == total_cr,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        },
    }


@router.get("/profit-loss/", summary="Profit & Loss report")
async def profit_and_loss(
    company_id: UUID | None = Depends(get_optional_company_id),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Groups accounts by type:
      Revenue  → credit-normal  → credit balance = revenue earned
      Expense / COGS → debit-normal → debit balance = cost
    """
    if company_id is None:
        raise HTTPException(status_code=400, detail="Company ID required")
    if date_to is None:
        date_to = local_today()
    if date_from is None:
        date_from = date(2020, 1, 1)
    revenue_types = [AccountType.REVENUE.value]
    expense_types = [AccountType.EXPENSE.value, AccountType.COGS.value]

    q = (
        select(
            Account.id,
            Account.code,
            Account.name,
            Account.account_type,
            Account.account_nature,
            func.coalesce(func.sum(JournalItem.debit_amount), 0).label("period_debit"),
            func.coalesce(func.sum(JournalItem.credit_amount), 0).label("period_credit"),
        )
        .join(JournalItem, JournalItem.account_id == Account.id, isouter=True)
        .join(
            JournalEntry,
            and_(
                JournalItem.entry_id == JournalEntry.id,
                JournalEntry.state == EntryState.POSTED.value,
                JournalEntry.accounting_date >= date_from,
                JournalEntry.accounting_date <= date_to,
            ),
            isouter=True,
        )
        .where(
            Account.company_id == company_id,
            Account.account_type.in_(revenue_types + expense_types),
            Account.is_deleted == False,
        )
        .group_by(Account.id, Account.code, Account.name, Account.account_type, Account.account_nature)
        .order_by(Account.account_type, Account.code)
    )
    rows = (await db.execute(q)).all()

    revenue_lines = []
    expense_lines = []
    total_revenue = ZERO
    total_cogs = ZERO
    total_expense = ZERO

    for row in rows:
        dr = Decimal(str(row.period_debit))
        cr = Decimal(str(row.period_credit))
        if row.account_nature == "credit":
            balance = cr - dr  # Revenue
        else:
            balance = dr - cr  # Expense

        entry = {
            "account_id": str(row.id),
            "account_code": row.code,
            "account_name": row.name,
            "account_type": row.account_type,
            "amount": float(balance),
        }
        if row.account_type == AccountType.REVENUE.value:
            revenue_lines.append(entry)
            total_revenue += balance
        elif row.account_type == AccountType.COGS.value:
            expense_lines.append(entry)
            total_cogs += balance
        else:
            expense_lines.append(entry)
            total_expense += balance

    gross_profit = total_revenue - total_cogs
    net_income = gross_profit - total_expense

    return {
        "success": True,
        "data": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "revenue": {"lines": revenue_lines, "total": float(total_revenue)},
            "cost_of_goods_sold": float(total_cogs),
            "gross_profit": float(gross_profit),
            "expenses": {"lines": expense_lines, "total": float(total_expense)},
            "net_income": float(net_income),
        },
    }


@router.get("/balance-sheet/", summary="Balance Sheet report")
async def balance_sheet(
    company_id: UUID | None = Depends(get_optional_company_id),
    as_of_date: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if company_id is None:
        raise HTTPException(status_code=400, detail="Company ID required")
    if as_of_date is None:
        as_of_date = local_today()
    asset_types = [AccountType.ASSET.value]
    liability_types = [AccountType.LIABILITY.value]
    equity_types = [AccountType.EQUITY.value]

    q = (
        select(
            Account.id,
            Account.code,
            Account.name,
            Account.account_type,
            Account.account_nature,
            func.coalesce(func.sum(JournalItem.debit_amount), 0).label("total_debit"),
            func.coalesce(func.sum(JournalItem.credit_amount), 0).label("total_credit"),
        )
        .join(JournalItem, JournalItem.account_id == Account.id, isouter=True)
        .join(
            JournalEntry,
            and_(
                JournalItem.entry_id == JournalEntry.id,
                JournalEntry.state == EntryState.POSTED.value,
                JournalEntry.accounting_date <= as_of_date,
            ),
            isouter=True,
        )
        .where(
            Account.company_id == company_id,
            Account.account_type.in_(asset_types + liability_types + equity_types),
            Account.is_deleted == False,
        )
        .group_by(Account.id, Account.code, Account.name, Account.account_type, Account.account_nature)
        .order_by(Account.account_type, Account.code)
    )
    rows = (await db.execute(q)).all()

    assets, liabilities, equity = [], [], []
    total_assets = ZERO
    total_liabilities = ZERO
    total_equity = ZERO

    for row in rows:
        dr = Decimal(str(row.total_debit))
        cr = Decimal(str(row.total_credit))
        if row.account_nature == "debit":
            balance = dr - cr
        else:
            balance = cr - dr

        entry = {
            "account_id": str(row.id),
            "account_code": row.code,
            "account_name": row.name,
            "balance": float(balance),
        }
        if row.account_type in asset_types:
            assets.append(entry)
            total_assets += balance
        elif row.account_type in liability_types:
            liabilities.append(entry)
            total_liabilities += balance
        else:
            equity.append(entry)
            total_equity += balance

    # Include Current Year Earnings / Unallocated Net Income in Equity
    pnl_q = (
        select(
            Account.account_type,
            func.coalesce(func.sum(JournalItem.debit_amount), 0).label("total_debit"),
            func.coalesce(func.sum(JournalItem.credit_amount), 0).label("total_credit"),
        )
        .join(JournalItem, JournalItem.account_id == Account.id)
        .join(
            JournalEntry,
            and_(
                JournalItem.entry_id == JournalEntry.id,
                JournalEntry.state == EntryState.POSTED.value,
                JournalEntry.accounting_date <= as_of_date,
            ),
        )
        .where(
            Account.company_id == company_id,
            Account.account_type.in_([AccountType.REVENUE.value, AccountType.EXPENSE.value, AccountType.COGS.value]),
            Account.is_deleted == False,
        )
        .group_by(Account.account_type)
    )
    pnl_rows = (await db.execute(pnl_q)).all()
    net_income = ZERO
    for pr in pnl_rows:
        p_dr = Decimal(str(pr.total_debit))
        p_cr = Decimal(str(pr.total_credit))
        if pr.account_type == AccountType.REVENUE.value:
            net_income += (p_cr - p_dr)
        else:
            net_income -= (p_dr - p_cr)

    if net_income != ZERO:
        equity.append({
            "account_id": None,
            "account_code": "NET_INCOME",
            "account_name": "Current Period Earnings (Net Income)",
            "balance": float(net_income),
        })
        total_equity += net_income

    return {
        "success": True,
        "data": {
            "as_of_date": as_of_date.isoformat(),
            "assets": {"lines": assets, "total": float(total_assets)},
            "liabilities": {"lines": liabilities, "total": float(total_liabilities)},
            "equity": {"lines": equity, "total": float(total_equity)},
            "total_liabilities_and_equity": float(total_liabilities + total_equity),
            "balanced": abs(total_assets - (total_liabilities + total_equity)) < Decimal("0.01"),
        },
    }


@router.get("/ar-aging/", summary="Accounts Receivable Aging")
async def ar_aging(
    company_id: UUID | None = Depends(get_optional_company_id),
    as_of_date: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if company_id is None:
        raise HTTPException(status_code=400, detail="Company ID required")
    if as_of_date is None:
        as_of_date = local_today()
    return await _aging_report(db, company_id, as_of_date, is_ar=True)


@router.get("/ap-aging/", summary="Accounts Payable Aging")
async def ap_aging(
    company_id: UUID | None = Depends(get_optional_company_id),
    as_of_date: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if company_id is None:
        raise HTTPException(status_code=400, detail="Company ID required")
    if as_of_date is None:
        as_of_date = local_today()
    return await _aging_report(db, company_id, as_of_date, is_ar=False)


async def _aging_report(
    db: AsyncSession,
    company_id: UUID,
    as_of_date: date,
    is_ar: bool,
) -> dict:
    """Build AR or AP aging by partner."""
    inv_types = (
        [InvoiceType.INVOICE.value, InvoiceType.CREDIT_NOTE.value]
        if is_ar
        else [InvoiceType.BILL.value, InvoiceType.VENDOR_CREDIT.value]
    )

    q = (
        select(
            Invoice.id,
            Invoice.name,
            Invoice.partner_id,
            Invoice.invoice_date,
            Invoice.due_date,
            Invoice.amount_residual,
            Invoice.currency_code,
        )
        .where(
            Invoice.company_id == company_id,
            Invoice.invoice_type.in_(inv_types),
            Invoice.state.in_([InvoiceState.POSTED.value, InvoiceState.PARTIAL.value]),
            Invoice.amount_residual > 0,
            Invoice.invoice_date <= as_of_date,
        )
        .order_by(Invoice.partner_id, Invoice.due_date)
    )
    invoices = (await db.execute(q)).all()

    # Load partners
    partner_ids = list({str(inv.partner_id) for inv in invoices})
    partners: dict[str, str] = {}
    if partner_ids:
        p_rows = (
            await db.execute(
                select(Partner.id, Partner.name).where(Partner.id.in_([UUID(p) for p in partner_ids]))
            )
        ).all()
        partners = {str(r.id): r.name for r in p_rows}

    buckets = ["current", "1-30", "31-60", "61-90", "91-120", "120+"]
    by_partner: dict[str, dict] = {}

    for inv in invoices:
        pid = str(inv.partner_id)
        if pid not in by_partner:
            by_partner[pid] = {
                "partner_id": pid,
                "partner_name": partners.get(pid, "Unknown"),
                "current": 0.0,
                "1-30": 0.0,
                "31-60": 0.0,
                "61-90": 0.0,
                "91-120": 0.0,
                "120+": 0.0,
                "total": 0.0,
            }
        residual = float(inv.amount_residual)
        due = inv.due_date or inv.invoice_date
        days_overdue = (as_of_date - due).days if due else 0

        if days_overdue <= 0:
            bucket = "current"
        elif days_overdue <= 30:
            bucket = "1-30"
        elif days_overdue <= 60:
            bucket = "31-60"
        elif days_overdue <= 90:
            bucket = "61-90"
        elif days_overdue <= 120:
            bucket = "91-120"
        else:
            bucket = "120+"

        by_partner[pid][bucket] += residual
        by_partner[pid]["total"] += residual

    grand_total = sum(p["total"] for p in by_partner.values())

    return {
        "success": True,
        "data": {
            "as_of_date": as_of_date.isoformat(),
            "type": "ar" if is_ar else "ap",
            "partners": list(by_partner.values()),
            "grand_total": grand_total,
        },
    }


@router.get("/cash-flow/", summary="Simplified Cash Flow statement")
async def cash_flow(
    company_id: UUID | None = Depends(get_optional_company_id),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Simplified indirect cash flow:
    Operating = movements on Bank/Cash accounts from SALE/PURCHASE journals.
    """
    if company_id is None:
        raise HTTPException(status_code=400, detail="Company ID required")
    if date_to is None:
        date_to = local_today()
    if date_from is None:
        date_from = date(2020, 1, 1)
    from app.models.journal import Journal, JournalType

    # Get bank/cash journal IDs
    journal_result = await db.execute(
        select(Journal.id, Journal.name, Journal.journal_type).where(
            Journal.company_id == company_id,
            Journal.journal_type.in_([JournalType.BANK.value, JournalType.CASH.value]),
        )
    )
    journals = {str(r.id): r for r in journal_result.all()}

    if not journals:
        return {
            "success": True,
            "data": {
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "operating": 0.0,
                "investing": 0.0,
                "financing": 0.0,
                "net_change": 0.0,
                "note": "No bank/cash journals configured",
            },
        }

    q = (
        select(
            func.coalesce(func.sum(JournalItem.debit_amount), 0).label("total_debit"),
            func.coalesce(func.sum(JournalItem.credit_amount), 0).label("total_credit"),
        )
        .join(JournalEntry, JournalItem.entry_id == JournalEntry.id)
        .where(
            JournalItem.company_id == company_id,
            JournalEntry.state == EntryState.POSTED.value,
            JournalEntry.journal_id.in_([UUID(j) for j in journals]),
            JournalEntry.accounting_date >= date_from,
            JournalEntry.accounting_date <= date_to,
        )
    )
    row = (await db.execute(q)).one()
    inflows = Decimal(str(row.total_debit))
    outflows = Decimal(str(row.total_credit))
    net = inflows - outflows

    return {
        "success": True,
        "data": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "operating": {
                "inflows": float(inflows),
                "outflows": float(outflows),
                "net": float(net),
            },
            "net_change": float(net),
        },
    }


@router.get("/monthly-profit-loss/", summary="Monthly Profit and Loss trend")
async def monthly_profit_loss(
    company_id: UUID | None = Depends(get_optional_company_id),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Returns month-by-month profit and loss breakdown with revenue, COGS,
    operating expenses, net profit, and gross/net profit margins.
    Auto-generates earnings and loss timeline.
    """
    if company_id is None:
        raise HTTPException(status_code=400, detail="Company ID required")
    if date_to is None:
        date_to = local_today()
    if date_from is None:
        date_from = date(date_to.year, 1, 1)

    from app.services.report_service import ReportService
    service = ReportService(db)
    monthly_data = await service.get_monthly_profit_and_loss(
        company_id=company_id,
        date_from=date_from,
        date_to=date_to,
    )

    return {
        "success": True,
        "data": {
            "company_id": str(company_id),
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "months": [
                {
                    "month": m.month,
                    "revenue": float(m.revenue),
                    "cogs": float(m.cogs),
                    "expenses": float(m.expenses),
                    "gross_profit": float(m.gross_profit),
                    "net_profit": float(m.net_profit),
                    "gross_margin_pct": float(m.gross_margin_pct),
                    "net_margin_pct": float(m.net_margin_pct),
                }
                for m in monthly_data
            ],
            "total_revenue": float(sum((m.revenue for m in monthly_data), Decimal("0"))),
            "total_net_profit": float(sum((m.net_profit for m in monthly_data), Decimal("0"))),
        },
    }


@router.get("/partner-financial-summary/{partner_id}", summary="360 Partner financial relation summary")
async def partner_financial_summary(
    partner_id: UUID,
    company_id: UUID | None = Depends(get_optional_company_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Returns 360-degree relation report for a customer or vendor:
    total invoiced, total settled payments, balance due, lifetime transactions.
    """
    if company_id is None:
        raise HTTPException(status_code=400, detail="Company ID required")

    from app.services.report_service import ReportService
    service = ReportService(db)
    summary = await service.get_partner_financial_summary(
        company_id=company_id,
        partner_id=partner_id,
    )
    if not summary:
        raise HTTPException(status_code=404, detail="Partner not found")

    return {
        "success": True,
        "data": {
            "partner_id": str(summary.partner_id),
            "partner_name": summary.partner_name,
            "partner_type": summary.partner_type,
            "total_invoiced": float(summary.total_invoiced),
            "total_paid": float(summary.total_paid),
            "balance_due": float(summary.balance_due),
            "lifetime_journal_items_count": summary.lifetime_journal_items_count,
        },
    }


@router.get("/analytic-profit-loss/", summary="Cost center / branch profit and loss report")
async def analytic_profit_loss(
    company_id: UUID | None = Depends(get_optional_company_id),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Segments income, expenses, and net profit contribution by cost center / branch project plan.
    """
    if company_id is None:
        raise HTTPException(status_code=400, detail="Company ID required")
    if date_to is None:
        date_to = local_today()
    if date_from is None:
        date_from = date(2020, 1, 1)

    from app.services.report_service import ReportService
    service = ReportService(db)
    report = await service.get_analytic_profit_and_loss(
        company_id=company_id,
        date_from=date_from,
        date_to=date_to,
    )

    return {
        "success": True,
        "data": {
            "company_id": str(company_id),
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "lines": [
                {
                    "account_id": str(line.account_id),
                    "code": line.code,
                    "name": line.name,
                    "plan_name": line.plan_name,
                    "revenue": float(line.revenue),
                    "cost": float(line.cost),
                    "net_contribution": float(line.net_contribution),
                }
                for line in report.lines
            ],
            "total_revenue": float(report.total_revenue),
            "total_cost": float(report.total_cost),
            "total_net": float(report.total_net),
        },
    }

