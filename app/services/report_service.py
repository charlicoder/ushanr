"""
app/services/report_service.py
────────────────────────────────
Financial reporting service — read-only analytical queries.

Reports provided:
  1. General Ledger (GL)         — line-by-line journal movement per account
  2. Trial Balance               — aggregated debit/credit per account
  3. Profit & Loss (P&L)         — revenue vs. expenses for a period
  4. Balance Sheet               — assets, liabilities, equity as at a date
  5. AR Aging                    — outstanding receivables by age bucket
  6. AP Aging                    — outstanding payables by age bucket
  7. Cash Flow (indirect method) — operating / investing / financing activities

Design notes:
  - Strictly READ-ONLY: no writes, no transaction side-effects.
  - All monetary values returned as Decimal.
  - Uses SQLAlchemy 2.x async patterns with func.sum() + group_by for aggregations.
  - Joins are done in SQL for performance (not Python-side iteration).
  - Only POSTED journal entries are included in all reports.
  - Account type classification follows ACCOUNT_NATURE_MAP from account model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ReportGenerationError
from app.core.logging import get_logger
from app.models.account import Account, AccountNature, AccountType
from app.models.invoice import Invoice, InvoiceState, InvoiceType
from app.models.journal import Journal
from app.models.journal_entry import EntryState, JournalEntry, JournalItem

logger = get_logger(__name__)

ZERO = Decimal("0")
PRECISION = Decimal("0.001")


# ── Result dataclasses ────────────────────────────────────────────────────────


@dataclass
class GLLine:
    """One row in the General Ledger report."""
    entry_date: date
    entry_name: str | None
    account_id: UUID
    account_code: str
    account_name: str
    partner_id: UUID | None
    narration: str | None
    debit: Decimal
    credit: Decimal
    balance: Decimal          # running balance (populated post-query)
    journal_entry_id: UUID
    journal_name: str | None
    item_name: str | None


@dataclass
class TrialBalanceLine:
    """One row in the Trial Balance report."""
    account_id: UUID
    account_code: str
    account_name: str
    account_type: str
    account_nature: str
    debit_total: Decimal
    credit_total: Decimal
    balance_debit: Decimal    # net balance in the debit column
    balance_credit: Decimal   # net balance in the credit column


@dataclass
class ProfitLossLine:
    """One revenue or expense line in the P&L."""
    account_id: UUID
    account_code: str
    account_name: str
    account_type: str         # AccountType value
    amount: Decimal           # positive = revenue credit; positive = expense debit
    is_revenue: bool


@dataclass
class ProfitLossReport:
    """Full Profit & Loss report."""
    company_id: UUID
    date_from: date
    date_to: date
    revenue_lines: list[ProfitLossLine]
    expense_lines: list[ProfitLossLine]
    cogs_lines: list[ProfitLossLine]
    total_revenue: Decimal
    total_cogs: Decimal
    total_expenses: Decimal
    gross_profit: Decimal     # total_revenue - total_cogs
    net_profit: Decimal       # gross_profit - total_expenses
    gross_margin_pct: Decimal = Decimal("0.0")
    net_margin_pct: Decimal = Decimal("0.0")
    ebitda: Decimal = Decimal("0.0")


@dataclass
class MonthlyPLBucket:
    """Monthly breakdown of revenue, COGS, expenses, and net profit."""
    month: str                # YYYY-MM
    revenue: Decimal
    cogs: Decimal
    expenses: Decimal
    gross_profit: Decimal
    net_profit: Decimal
    gross_margin_pct: Decimal
    net_margin_pct: Decimal


@dataclass
class AnalyticReportLine:
    """One cost center / project / branch summary line."""
    account_id: UUID
    code: str | None
    name: str
    plan_name: str | None
    revenue: Decimal
    cost: Decimal
    net_contribution: Decimal


@dataclass
class AnalyticProfitLossReport:
    """Profit and Loss broken down by cost centers / analytic branches."""
    company_id: UUID
    date_from: date
    date_to: date
    lines: list[AnalyticReportLine]
    total_revenue: Decimal
    total_cost: Decimal
    total_net: Decimal


@dataclass
class PartnerFinancialSummary:
    """360-degree financial overview of a partner (vendor or customer)."""
    partner_id: UUID
    partner_name: str
    partner_type: str
    total_invoiced: Decimal
    total_paid: Decimal
    balance_due: Decimal
    lifetime_journal_items_count: int


@dataclass
class BalanceSheetSection:
    """A grouping of accounts within the balance sheet."""
    label: str
    account_type: str
    lines: list[TrialBalanceLine]
    total: Decimal


@dataclass
class BalanceSheetReport:
    """Full Balance Sheet as at a date."""
    company_id: UUID
    as_of_date: date
    assets: BalanceSheetSection
    liabilities: BalanceSheetSection
    equity: BalanceSheetSection
    total_assets: Decimal
    total_liabilities_and_equity: Decimal
    is_balanced: bool         # total_assets == total_liabilities_and_equity


@dataclass
class ARAgingBucket:
    """Age bucket in an AR or AP aging report."""
    label: str                # e.g. "0-30 days"
    days_from: int
    days_to: int              # -1 = unbounded (> last bucket)
    total: Decimal


@dataclass
class ARAgingLine:
    """One partner row in AR aging."""
    partner_id: UUID | None
    partner_name: str | None
    invoice_id: UUID
    invoice_name: str | None
    invoice_date: date
    due_date: date | None
    amount_total: Decimal
    amount_residual: Decimal
    days_overdue: int
    bucket_label: str


@dataclass
class ARAgingReport:
    """Full AR aging report."""
    company_id: UUID
    as_of_date: date
    bucket_days: list[int]    # e.g. [30, 60, 90, 120]
    lines: list[ARAgingLine]
    buckets: list[ARAgingBucket]
    total_outstanding: Decimal


@dataclass
class APAgingLine:
    """One vendor row in AP aging (mirrors AR)."""
    partner_id: UUID | None
    partner_name: str | None
    invoice_id: UUID
    invoice_name: str | None
    invoice_date: date
    due_date: date | None
    amount_total: Decimal
    amount_residual: Decimal
    days_overdue: int
    bucket_label: str


@dataclass
class APAgingReport:
    """Full AP aging report."""
    company_id: UUID
    as_of_date: date
    bucket_days: list[int]
    lines: list[APAgingLine]
    buckets: list[ARAgingBucket]
    total_outstanding: Decimal


@dataclass
class CashFlowLine:
    """One line in the Cash Flow statement."""
    label: str
    amount: Decimal
    account_type: str | None = None


@dataclass
class CashFlowSection:
    """A section in the Cash Flow statement."""
    title: str
    lines: list[CashFlowLine]
    total: Decimal


@dataclass
class CashFlowReport:
    """
    Cash Flow statement (indirect method approximation from GL data).

    Note: A fully accurate indirect-method cash flow requires tagging each
    account to a cash-flow category. Here we use account_type as proxy:
      - Operating:  REVENUE, EXPENSE, COGS, AR/AP items
      - Investing:  Long-term ASSET accounts (is_bank_account=False)
      - Financing:  EQUITY + LIABILITY accounts
    """
    company_id: UUID
    date_from: date
    date_to: date
    operating: CashFlowSection
    investing: CashFlowSection
    financing: CashFlowSection
    net_change_in_cash: Decimal
    opening_cash: Decimal
    closing_cash: Decimal


# ── Internal helpers ──────────────────────────────────────────────────────────


def _d(value: object) -> Decimal:
    """Safe Decimal conversion from any numeric type."""
    if value is None:
        return ZERO
    return Decimal(str(value)).quantize(PRECISION, rounding=ROUND_HALF_UP)


def _classify_bucket(days_overdue: int, bucket_days: list[int]) -> str:
    """Return the bucket label string for a given overdue-days count."""
    prev = 0
    for threshold in sorted(bucket_days):
        if days_overdue <= threshold:
            return f"{prev + 1}-{threshold} days" if prev > 0 else f"0-{threshold} days"
        prev = threshold
    return f">{prev} days"


# ── Service ───────────────────────────────────────────────────────────────────


class ReportService:
    """
    Read-only financial reporting service.

    All methods query POSTED journal entries only and return Decimal amounts.

    Usage::

        async with async_session() as session:
            svc = ReportService(session)
            tb = await svc.get_trial_balance(company_id, date_from, date_to)
            pl = await svc.get_profit_and_loss(company_id, date_from, date_to)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── General Ledger ────────────────────────────────────────────────────────

    async def get_general_ledger(
        self,
        company_id: UUID,
        date_from: date,
        date_to: date,
        account_ids: list[UUID] | None = None,
    ) -> list[GLLine]:
        """
        Return all posted journal item movements for the given company and period.

        Rows are ordered by account_code ASC, entry_date ASC, entry created_at ASC.

        A running `balance` (debit – credit) is computed per account in Python
        after the SQL aggregation. For large datasets, consider using a DB window
        function (not done here for portability).

        Args:
            company_id:  Company to report on.
            date_from:   Inclusive start date (filters on JournalItem.date).
            date_to:     Inclusive end date.
            account_ids: Optional whitelist of account IDs to include.

        Returns:
            list[GLLine] sorted by account then date.
        """
        stmt = (
            select(
                JournalItem.id.label("item_id"),
                JournalItem.date.label("entry_date"),
                JournalItem.debit_amount.label("debit"),
                JournalItem.credit_amount.label("credit"),
                JournalItem.name.label("item_name"),
                JournalItem.partner_id.label("partner_id"),
                JournalEntry.id.label("journal_entry_id"),
                JournalEntry.name.label("entry_name"),
                JournalEntry.narration.label("narration"),
                JournalEntry.journal_id.label("journal_id"),
                Account.id.label("account_id"),
                Account.code.label("account_code"),
                Account.name.label("account_name"),
                Account.account_nature.label("account_nature"),
            )
            .join(JournalEntry, JournalItem.entry_id == JournalEntry.id)
            .join(Account, JournalItem.account_id == Account.id)
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.state == EntryState.POSTED.value,
                JournalItem.date >= date_from,
                JournalItem.date <= date_to,
            )
            .order_by(
                Account.code.asc(),
                JournalItem.date.asc(),
                JournalEntry.created_at.asc(),
            )
        )

        if account_ids:
            stmt = stmt.where(JournalItem.account_id.in_(account_ids))

        rows = (await self._session.execute(stmt)).all()

        lines: list[GLLine] = []
        running: dict[str, Decimal] = {}   # account_code → running balance

        for row in rows:
            code = row.account_code
            debit = _d(row.debit)
            credit = _d(row.credit)

            is_debit_normal = row.account_nature == AccountNature.DEBIT.value
            if is_debit_normal:
                movement = debit - credit
            else:
                movement = credit - debit

            running[code] = running.get(code, ZERO) + movement

            lines.append(
                GLLine(
                    entry_date=row.entry_date,
                    entry_name=row.entry_name,
                    account_id=row.account_id,
                    account_code=row.account_code,
                    account_name=row.account_name,
                    partner_id=row.partner_id,
                    narration=row.narration,
                    debit=debit,
                    credit=credit,
                    balance=running[code],
                    journal_entry_id=row.journal_entry_id,
                    journal_name=None,   # join to Journal if needed
                    item_name=row.item_name,
                )
            )

        logger.info(
            "general_ledger_generated",
            company_id=str(company_id),
            date_from=str(date_from),
            date_to=str(date_to),
            rows=len(lines),
        )
        return lines

    # ── Trial Balance ─────────────────────────────────────────────────────────

    async def get_trial_balance(
        self,
        company_id: UUID,
        date_from: date,
        date_to: date,
    ) -> list[TrialBalanceLine]:
        """
        Aggregate all posted journal movements by account for the given period.

        Returns accounts that have any posted activity in the period.
        Includes opening_debit / opening_credit from the Account record.

        Total of balance_debit columns MUST equal balance_credit columns
        (double-entry invariant). Callers should assert this.
        """
        stmt = (
            select(
                Account.id.label("account_id"),
                Account.code.label("account_code"),
                Account.name.label("account_name"),
                Account.account_type.label("account_type"),
                Account.account_nature.label("account_nature"),
                Account.opening_debit.label("opening_debit"),
                Account.opening_credit.label("opening_credit"),
                func.coalesce(func.sum(JournalItem.debit_amount), 0).label("total_debit"),
                func.coalesce(func.sum(JournalItem.credit_amount), 0).label("total_credit"),
            )
            .join(JournalItem, JournalItem.account_id == Account.id)
            .join(JournalEntry, JournalItem.entry_id == JournalEntry.id)
            .where(
                Account.company_id == company_id,
                Account.is_deleted.is_(False),
                JournalEntry.company_id == company_id,
                JournalEntry.state == EntryState.POSTED.value,
                JournalItem.date >= date_from,
                JournalItem.date <= date_to,
            )
            .group_by(
                Account.id,
                Account.code,
                Account.name,
                Account.account_type,
                Account.account_nature,
                Account.opening_debit,
                Account.opening_credit,
            )
            .order_by(Account.account_type.asc(), Account.code.asc())
        )

        rows = (await self._session.execute(stmt)).all()
        lines: list[TrialBalanceLine] = []

        for row in rows:
            total_debit = _d(row.total_debit) + _d(row.opening_debit)
            total_credit = _d(row.total_credit) + _d(row.opening_credit)

            is_debit_normal = row.account_nature == AccountNature.DEBIT.value

            if is_debit_normal:
                net = total_debit - total_credit
                balance_debit = max(net, ZERO)
                balance_credit = max(-net, ZERO)
            else:
                net = total_credit - total_debit
                balance_credit = max(net, ZERO)
                balance_debit = max(-net, ZERO)

            lines.append(
                TrialBalanceLine(
                    account_id=row.account_id,
                    account_code=row.account_code,
                    account_name=row.account_name,
                    account_type=row.account_type,
                    account_nature=row.account_nature,
                    debit_total=total_debit,
                    credit_total=total_credit,
                    balance_debit=balance_debit,
                    balance_credit=balance_credit,
                )
            )

        logger.info(
            "trial_balance_generated",
            company_id=str(company_id),
            date_from=str(date_from),
            date_to=str(date_to),
            lines=len(lines),
        )
        return lines

    # ── Profit & Loss ─────────────────────────────────────────────────────────

    async def get_profit_and_loss(
        self,
        company_id: UUID,
        date_from: date,
        date_to: date,
    ) -> ProfitLossReport:
        """
        Compute the Profit & Loss statement for a period.

        Revenue  = SUM(credit) - SUM(debit) on REVENUE accounts
        COGS     = SUM(debit) - SUM(credit) on COGS accounts
        Expenses = SUM(debit) - SUM(credit) on EXPENSE accounts

        Gross Profit = Revenue - COGS
        Net Profit   = Gross Profit - Expenses

        Returns:
            ProfitLossReport with per-account lines and section totals.
        """
        target_types = [
            AccountType.REVENUE.value,
            AccountType.EXPENSE.value,
            AccountType.COGS.value,
        ]

        stmt = (
            select(
                Account.id.label("account_id"),
                Account.code.label("account_code"),
                Account.name.label("account_name"),
                Account.account_type.label("account_type"),
                Account.account_nature.label("account_nature"),
                func.coalesce(func.sum(JournalItem.debit_amount), 0).label("total_debit"),
                func.coalesce(func.sum(JournalItem.credit_amount), 0).label("total_credit"),
            )
            .join(JournalItem, JournalItem.account_id == Account.id)
            .join(JournalEntry, JournalItem.entry_id == JournalEntry.id)
            .where(
                Account.company_id == company_id,
                Account.is_deleted.is_(False),
                Account.account_type.in_(target_types),
                JournalEntry.company_id == company_id,
                JournalEntry.state == EntryState.POSTED.value,
                JournalItem.date >= date_from,
                JournalItem.date <= date_to,
            )
            .group_by(
                Account.id,
                Account.code,
                Account.name,
                Account.account_type,
                Account.account_nature,
            )
            .order_by(Account.account_type.asc(), Account.code.asc())
        )

        rows = (await self._session.execute(stmt)).all()

        revenue_lines: list[ProfitLossLine] = []
        expense_lines: list[ProfitLossLine] = []
        cogs_lines: list[ProfitLossLine] = []

        for row in rows:
            total_debit = _d(row.total_debit)
            total_credit = _d(row.total_credit)

            if row.account_type == AccountType.REVENUE.value:
                # Credit-normal: revenue = credit - debit
                amount = total_credit - total_debit
                revenue_lines.append(
                    ProfitLossLine(
                        account_id=row.account_id,
                        account_code=row.account_code,
                        account_name=row.account_name,
                        account_type=row.account_type,
                        amount=amount,
                        is_revenue=True,
                    )
                )
            elif row.account_type == AccountType.COGS.value:
                # Debit-normal: COGS = debit - credit
                amount = total_debit - total_credit
                cogs_lines.append(
                    ProfitLossLine(
                        account_id=row.account_id,
                        account_code=row.account_code,
                        account_name=row.account_name,
                        account_type=row.account_type,
                        amount=amount,
                        is_revenue=False,
                    )
                )
            else:
                # EXPENSE: debit-normal
                amount = total_debit - total_credit
                expense_lines.append(
                    ProfitLossLine(
                        account_id=row.account_id,
                        account_code=row.account_code,
                        account_name=row.account_name,
                        account_type=row.account_type,
                        amount=amount,
                        is_revenue=False,
                    )
                )

        total_revenue = sum((ln.amount for ln in revenue_lines), ZERO).quantize(
            PRECISION, rounding=ROUND_HALF_UP
        )
        total_cogs = sum((ln.amount for ln in cogs_lines), ZERO).quantize(
            PRECISION, rounding=ROUND_HALF_UP
        )
        total_expenses = sum((ln.amount for ln in expense_lines), ZERO).quantize(
            PRECISION, rounding=ROUND_HALF_UP
        )
        gross_profit = (total_revenue - total_cogs).quantize(PRECISION, rounding=ROUND_HALF_UP)
        net_profit = (gross_profit - total_expenses).quantize(PRECISION, rounding=ROUND_HALF_UP)

        gross_margin_pct = (
            (gross_profit / total_revenue * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if total_revenue > ZERO else ZERO
        )
        net_margin_pct = (
            (net_profit / total_revenue * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if total_revenue > ZERO else ZERO
        )
        # EBITDA: Net Profit + Depreciation & Amortization (add back 5008* depreciation expense accounts)
        deprec_amount = sum(
            (ln.amount for ln in expense_lines if "depreciation" in ln.account_name.lower() or ln.account_code.startswith("5008")),
            ZERO,
        )
        ebitda = (net_profit + deprec_amount).quantize(PRECISION, rounding=ROUND_HALF_UP)

        logger.info(
            "profit_loss_generated",
            company_id=str(company_id),
            date_from=str(date_from),
            date_to=str(date_to),
            net_profit=float(net_profit),
            gross_margin_pct=float(gross_margin_pct),
            net_margin_pct=float(net_margin_pct),
        )

        return ProfitLossReport(
            company_id=company_id,
            date_from=date_from,
            date_to=date_to,
            revenue_lines=revenue_lines,
            expense_lines=expense_lines,
            cogs_lines=cogs_lines,
            total_revenue=total_revenue,
            total_cogs=total_cogs,
            total_expenses=total_expenses,
            gross_profit=gross_profit,
            net_profit=net_profit,
            gross_margin_pct=gross_margin_pct,
            net_margin_pct=net_margin_pct,
            ebitda=ebitda,
        )

    # ── Balance Sheet ─────────────────────────────────────────────────────────

    async def get_balance_sheet(
        self,
        company_id: UUID,
        as_of_date: date,
    ) -> BalanceSheetReport:
        """
        Compute the Balance Sheet as at a given date (cumulative from all history).

        Assets     = SUM(debit) - SUM(credit) on ASSET accounts
        Liabilities= SUM(credit) - SUM(debit) on LIABILITY accounts
        Equity     = SUM(credit) - SUM(debit) on EQUITY accounts

        Balance Sheet Equation:  Assets == Liabilities + Equity

        All posted entries up to and including as_of_date are included.

        Returns:
            BalanceSheetReport with section groupings and is_balanced flag.
        """
        target_types = [
            AccountType.ASSET.value,
            AccountType.LIABILITY.value,
            AccountType.EQUITY.value,
        ]

        stmt = (
            select(
                Account.id.label("account_id"),
                Account.code.label("account_code"),
                Account.name.label("account_name"),
                Account.account_type.label("account_type"),
                Account.account_nature.label("account_nature"),
                Account.opening_debit.label("opening_debit"),
                Account.opening_credit.label("opening_credit"),
                func.coalesce(func.sum(JournalItem.debit_amount), 0).label("total_debit"),
                func.coalesce(func.sum(JournalItem.credit_amount), 0).label("total_credit"),
            )
            .join(JournalItem, JournalItem.account_id == Account.id, isouter=True)
            .join(
                JournalEntry,
                and_(
                    JournalItem.entry_id == JournalEntry.id,
                    JournalEntry.state == EntryState.POSTED.value,
                    JournalEntry.company_id == company_id,
                    JournalItem.date <= as_of_date,
                ),
                isouter=True,
            )
            .where(
                Account.company_id == company_id,
                Account.is_deleted.is_(False),
                Account.account_type.in_(target_types),
            )
            .group_by(
                Account.id,
                Account.code,
                Account.name,
                Account.account_type,
                Account.account_nature,
                Account.opening_debit,
                Account.opening_credit,
            )
            .order_by(Account.account_type.asc(), Account.code.asc())
        )

        rows = (await self._session.execute(stmt)).all()

        asset_lines: list[TrialBalanceLine] = []
        liability_lines: list[TrialBalanceLine] = []
        equity_lines: list[TrialBalanceLine] = []

        for row in rows:
            total_debit = _d(row.total_debit) + _d(row.opening_debit)
            total_credit = _d(row.total_credit) + _d(row.opening_credit)
            is_debit_normal = row.account_nature == AccountNature.DEBIT.value

            if is_debit_normal:
                net = total_debit - total_credit
                balance_debit = max(net, ZERO)
                balance_credit = max(-net, ZERO)
            else:
                net = total_credit - total_debit
                balance_credit = max(net, ZERO)
                balance_debit = max(-net, ZERO)

            tbl = TrialBalanceLine(
                account_id=row.account_id,
                account_code=row.account_code,
                account_name=row.account_name,
                account_type=row.account_type,
                account_nature=row.account_nature,
                debit_total=total_debit,
                credit_total=total_credit,
                balance_debit=balance_debit,
                balance_credit=balance_credit,
            )
            if row.account_type == AccountType.ASSET.value:
                asset_lines.append(tbl)
            elif row.account_type == AccountType.LIABILITY.value:
                liability_lines.append(tbl)
            else:
                equity_lines.append(tbl)

        # Section totals — assets use balance_debit; liabilities/equity use balance_credit
        total_assets = sum(
            (ln.balance_debit for ln in asset_lines), ZERO
        ).quantize(PRECISION, rounding=ROUND_HALF_UP)

        total_liabilities = sum(
            (ln.balance_credit for ln in liability_lines), ZERO
        ).quantize(PRECISION, rounding=ROUND_HALF_UP)

        total_equity = sum(
            (ln.balance_credit for ln in equity_lines), ZERO
        ).quantize(PRECISION, rounding=ROUND_HALF_UP)

        total_liabilities_and_equity = (total_liabilities + total_equity).quantize(
            PRECISION, rounding=ROUND_HALF_UP
        )

        is_balanced = total_assets == total_liabilities_and_equity

        if not is_balanced:
            logger.warning(
                "balance_sheet_out_of_balance",
                company_id=str(company_id),
                as_of_date=str(as_of_date),
                total_assets=float(total_assets),
                total_liabilities_and_equity=float(total_liabilities_and_equity),
                diff=float(abs(total_assets - total_liabilities_and_equity)),
            )

        logger.info(
            "balance_sheet_generated",
            company_id=str(company_id),
            as_of_date=str(as_of_date),
            total_assets=float(total_assets),
            is_balanced=is_balanced,
        )

        return BalanceSheetReport(
            company_id=company_id,
            as_of_date=as_of_date,
            assets=BalanceSheetSection(
                label="Assets",
                account_type=AccountType.ASSET.value,
                lines=asset_lines,
                total=total_assets,
            ),
            liabilities=BalanceSheetSection(
                label="Liabilities",
                account_type=AccountType.LIABILITY.value,
                lines=liability_lines,
                total=total_liabilities,
            ),
            equity=BalanceSheetSection(
                label="Equity",
                account_type=AccountType.EQUITY.value,
                lines=equity_lines,
                total=total_equity,
            ),
            total_assets=total_assets,
            total_liabilities_and_equity=total_liabilities_and_equity,
            is_balanced=is_balanced,
        )

    # ── AR Aging ──────────────────────────────────────────────────────────────

    async def get_ar_aging(
        self,
        company_id: UUID,
        as_of_date: date,
        bucket_days: list[int] | None = None,
    ) -> ARAgingReport:
        """
        Accounts Receivable Aging — outstanding customer invoices as of a date.

        Buckets (default: 30, 60, 90, 120 days past due):
          - Current (not yet due)
          - 1–30 days
          - 31–60 days
          - 61–90 days
          - 91–120 days
          - > 120 days

        Only POSTED and PARTIAL invoices of type INVOICE are included.
        Credit notes (CREDIT_NOTE) are excluded from AR aging.

        Args:
            company_id:  Company to report on.
            as_of_date:  Aging reference date.
            bucket_days: Ordered list of day thresholds for buckets.

        Returns:
            ARAgingReport with per-invoice lines and bucket summary.
        """
        if bucket_days is None:
            bucket_days = [30, 60, 90, 120]

        ar_states = [InvoiceState.POSTED.value, InvoiceState.PARTIAL.value]

        stmt = (
            select(Invoice)
            .where(
                Invoice.company_id == company_id,
                Invoice.invoice_type == InvoiceType.INVOICE.value,
                Invoice.state.in_(ar_states),
                Invoice.invoice_date <= as_of_date,
                Invoice.is_deleted.is_(False),
            )
            .order_by(Invoice.due_date.asc().nullsfirst(), Invoice.invoice_date.asc())
        )

        result = await self._session.execute(stmt)
        invoices: list[Invoice] = list(result.scalars().all())

        lines: list[ARAgingLine] = []
        buckets_map: dict[str, Decimal] = {"Current": ZERO}
        for b in sorted(bucket_days):
            prev = 0
            label = (
                f"0-{b} days"
                if bucket_days.index(b) == 0
                else f"{bucket_days[bucket_days.index(b) - 1] + 1}-{b} days"
            )
            buckets_map[label] = ZERO
        buckets_map[f">{max(bucket_days)} days"] = ZERO

        for inv in invoices:
            residual = _d(inv.amount_residual)
            if residual <= ZERO:
                continue

            due = inv.due_date
            if due is None or due >= as_of_date:
                days_overdue = 0
                bucket_label = "Current"
            else:
                days_overdue = (as_of_date - due).days
                bucket_label = _classify_bucket(days_overdue, bucket_days)

            # Ensure bucket exists
            if bucket_label not in buckets_map:
                buckets_map[bucket_label] = ZERO
            buckets_map[bucket_label] += residual

            lines.append(
                ARAgingLine(
                    partner_id=inv.partner_id,
                    partner_name=None,  # join partners if needed
                    invoice_id=inv.id,
                    invoice_name=inv.name,
                    invoice_date=inv.invoice_date,
                    due_date=inv.due_date,
                    amount_total=_d(inv.amount_total),
                    amount_residual=residual,
                    days_overdue=days_overdue,
                    bucket_label=bucket_label,
                )
            )

        total_outstanding = sum(
            (ln.amount_residual for ln in lines), ZERO
        ).quantize(PRECISION, rounding=ROUND_HALF_UP)

        # Build ordered bucket objects
        sorted_labels = ["Current"] + [
            (
                f"0-{bucket_days[0]} days"
                if i == 0
                else f"{bucket_days[i - 1] + 1}-{bucket_days[i]} days"
            )
            for i in range(len(bucket_days))
        ] + [f">{max(bucket_days)} days"]

        bucket_objects: list[ARAgingBucket] = []
        prev_day = 0
        for i, label in enumerate(sorted_labels):
            if label == "Current":
                d_from, d_to = 0, 0
            elif label.startswith(">"):
                d_from = max(bucket_days)
                d_to = -1
            else:
                parts = label.replace(" days", "").split("-")
                d_from, d_to = int(parts[0]), int(parts[1])

            bucket_objects.append(
                ARAgingBucket(
                    label=label,
                    days_from=d_from,
                    days_to=d_to,
                    total=buckets_map.get(label, ZERO).quantize(
                        PRECISION, rounding=ROUND_HALF_UP
                    ),
                )
            )

        logger.info(
            "ar_aging_generated",
            company_id=str(company_id),
            as_of_date=str(as_of_date),
            invoices=len(lines),
            total_outstanding=float(total_outstanding),
        )

        return ARAgingReport(
            company_id=company_id,
            as_of_date=as_of_date,
            bucket_days=bucket_days,
            lines=lines,
            buckets=bucket_objects,
            total_outstanding=total_outstanding,
        )

    # ── AP Aging ──────────────────────────────────────────────────────────────

    async def get_ap_aging(
        self,
        company_id: UUID,
        as_of_date: date,
        bucket_days: list[int] | None = None,
    ) -> APAgingReport:
        """
        Accounts Payable Aging — outstanding vendor bills as of a date.

        Mirrors AR aging but for BILL invoice types.

        Args:
            company_id:  Company to report on.
            as_of_date:  Aging reference date.
            bucket_days: Ordered list of day thresholds for buckets.

        Returns:
            APAgingReport with per-invoice lines and bucket summary.
        """
        if bucket_days is None:
            bucket_days = [30, 60, 90, 120]

        ap_states = [InvoiceState.POSTED.value, InvoiceState.PARTIAL.value]

        stmt = (
            select(Invoice)
            .where(
                Invoice.company_id == company_id,
                Invoice.invoice_type == InvoiceType.BILL.value,
                Invoice.state.in_(ap_states),
                Invoice.invoice_date <= as_of_date,
                Invoice.is_deleted.is_(False),
            )
            .order_by(Invoice.due_date.asc().nullsfirst(), Invoice.invoice_date.asc())
        )

        result = await self._session.execute(stmt)
        invoices: list[Invoice] = list(result.scalars().all())

        lines: list[APAgingLine] = []
        buckets_map: dict[str, Decimal] = {"Current": ZERO}
        for b in sorted(bucket_days):
            prev_idx = bucket_days.index(b)
            label = (
                f"0-{b} days"
                if prev_idx == 0
                else f"{bucket_days[prev_idx - 1] + 1}-{b} days"
            )
            buckets_map[label] = ZERO
        buckets_map[f">{max(bucket_days)} days"] = ZERO

        for inv in invoices:
            residual = _d(inv.amount_residual)
            if residual <= ZERO:
                continue

            due = inv.due_date
            if due is None or due >= as_of_date:
                days_overdue = 0
                bucket_label = "Current"
            else:
                days_overdue = (as_of_date - due).days
                bucket_label = _classify_bucket(days_overdue, bucket_days)

            if bucket_label not in buckets_map:
                buckets_map[bucket_label] = ZERO
            buckets_map[bucket_label] += residual

            lines.append(
                APAgingLine(
                    partner_id=inv.partner_id,
                    partner_name=None,
                    invoice_id=inv.id,
                    invoice_name=inv.name,
                    invoice_date=inv.invoice_date,
                    due_date=inv.due_date,
                    amount_total=_d(inv.amount_total),
                    amount_residual=residual,
                    days_overdue=days_overdue,
                    bucket_label=bucket_label,
                )
            )

        total_outstanding = sum(
            (ln.amount_residual for ln in lines), ZERO
        ).quantize(PRECISION, rounding=ROUND_HALF_UP)

        # Build ordered bucket objects (same helper as AR)
        sorted_labels = ["Current"] + [
            (
                f"0-{bucket_days[0]} days"
                if i == 0
                else f"{bucket_days[i - 1] + 1}-{bucket_days[i]} days"
            )
            for i in range(len(bucket_days))
        ] + [f">{max(bucket_days)} days"]

        bucket_objects: list[ARAgingBucket] = []
        for label in sorted_labels:
            if label == "Current":
                d_from, d_to = 0, 0
            elif label.startswith(">"):
                d_from = max(bucket_days)
                d_to = -1
            else:
                parts = label.replace(" days", "").split("-")
                d_from, d_to = int(parts[0]), int(parts[1])

            bucket_objects.append(
                ARAgingBucket(
                    label=label,
                    days_from=d_from,
                    days_to=d_to,
                    total=buckets_map.get(label, ZERO).quantize(
                        PRECISION, rounding=ROUND_HALF_UP
                    ),
                )
            )

        logger.info(
            "ap_aging_generated",
            company_id=str(company_id),
            as_of_date=str(as_of_date),
            invoices=len(lines),
            total_outstanding=float(total_outstanding),
        )

        return APAgingReport(
            company_id=company_id,
            as_of_date=as_of_date,
            bucket_days=bucket_days,
            lines=lines,
            buckets=bucket_objects,
            total_outstanding=total_outstanding,
        )

    # ── Cash Flow ─────────────────────────────────────────────────────────────

    async def get_cash_flow(
        self,
        company_id: UUID,
        date_from: date,
        date_to: date,
    ) -> CashFlowReport:
        """
        Approximate Cash Flow Statement (indirect method) derived from GL data.

        Classification by account type:
          Operating:  REVENUE, EXPENSE, COGS, ASSET (reconcilable / AR), LIABILITY (AP)
          Investing:  ASSET (non-bank, non-reconcilable, long-term)
          Financing:  EQUITY, LIABILITY (long-term debt)
          Cash:       ASSET (is_bank_account=True) — used for opening/closing balances

        Net change in cash = Inflows - Outflows across all sections.
        Opening cash = cumulative bank balance before date_from.
        Closing cash = opening + net change.

        Note: For a precise IFRS/GAAP cash flow, each account should be
        explicitly tagged to a cash flow category. This implementation uses
        account_type as a reasonable approximation.

        Returns:
            CashFlowReport with operating, investing, financing sections.
        """
        # --- 1. Fetch bank / cash opening balance (cumulative before date_from) ---
        bank_opening_stmt = (
            select(
                func.coalesce(func.sum(JournalItem.debit_amount), 0).label("total_debit"),
                func.coalesce(func.sum(JournalItem.credit_amount), 0).label("total_credit"),
            )
            .join(Account, JournalItem.account_id == Account.id)
            .join(JournalEntry, JournalItem.entry_id == JournalEntry.id)
            .where(
                Account.company_id == company_id,
                Account.is_bank_account.is_(True),
                Account.is_deleted.is_(False),
                JournalEntry.company_id == company_id,
                JournalEntry.state == EntryState.POSTED.value,
                JournalItem.date < date_from,
            )
        )
        bank_row = (await self._session.execute(bank_opening_stmt)).one()
        opening_cash = (_d(bank_row.total_debit) - _d(bank_row.total_credit)).quantize(
            PRECISION, rounding=ROUND_HALF_UP
        )

        # --- 2. Period movements per account ---
        period_stmt = (
            select(
                Account.id.label("account_id"),
                Account.code.label("account_code"),
                Account.name.label("account_name"),
                Account.account_type.label("account_type"),
                Account.account_nature.label("account_nature"),
                Account.is_bank_account.label("is_bank_account"),
                Account.is_reconcilable.label("is_reconcilable"),
                func.coalesce(func.sum(JournalItem.debit_amount), 0).label("total_debit"),
                func.coalesce(func.sum(JournalItem.credit_amount), 0).label("total_credit"),
            )
            .join(JournalItem, JournalItem.account_id == Account.id)
            .join(JournalEntry, JournalItem.entry_id == JournalEntry.id)
            .where(
                Account.company_id == company_id,
                Account.is_deleted.is_(False),
                JournalEntry.company_id == company_id,
                JournalEntry.state == EntryState.POSTED.value,
                JournalItem.date >= date_from,
                JournalItem.date <= date_to,
            )
            .group_by(
                Account.id,
                Account.code,
                Account.name,
                Account.account_type,
                Account.account_nature,
                Account.is_bank_account,
                Account.is_reconcilable,
            )
            .order_by(Account.account_type.asc(), Account.code.asc())
        )

        rows = (await self._session.execute(period_stmt)).all()

        operating_lines: list[CashFlowLine] = []
        investing_lines: list[CashFlowLine] = []
        financing_lines: list[CashFlowLine] = []
        net_cash_change = ZERO

        for row in rows:
            total_debit = _d(row.total_debit)
            total_credit = _d(row.total_credit)
            is_debit_normal = row.account_nature == AccountNature.DEBIT.value

            # Net movement (positive = cash inflow equivalent for debit-normal accounts)
            if is_debit_normal:
                net = total_debit - total_credit
            else:
                net = total_credit - total_debit

            acct_type = row.account_type
            is_bank = bool(row.is_bank_account)
            is_reconcilable = bool(row.is_reconcilable)

            # Skip pure bank/cash accounts — they form the opening/closing balance
            if is_bank:
                net_cash_change += (total_debit - total_credit)
                continue

            label = f"{row.account_code} — {row.account_name}"

            if acct_type in {
                AccountType.REVENUE.value,
                AccountType.EXPENSE.value,
                AccountType.COGS.value,
            } or (acct_type == AccountType.ASSET.value and is_reconcilable) or (
                acct_type == AccountType.LIABILITY.value and is_reconcilable
            ):
                # Operating activity
                operating_lines.append(
                    CashFlowLine(label=label, amount=net, account_type=acct_type)
                )
            elif acct_type == AccountType.ASSET.value and not is_reconcilable:
                # Investing: purchase of long-term assets is cash outflow
                investing_lines.append(
                    CashFlowLine(label=label, amount=-net, account_type=acct_type)
                )
            elif acct_type in {AccountType.EQUITY.value, AccountType.LIABILITY.value}:
                # Financing
                financing_lines.append(
                    CashFlowLine(label=label, amount=net, account_type=acct_type)
                )
            else:
                # OTHER
                operating_lines.append(
                    CashFlowLine(label=label, amount=net, account_type=acct_type)
                )

        total_operating = sum(
            (ln.amount for ln in operating_lines), ZERO
        ).quantize(PRECISION, rounding=ROUND_HALF_UP)
        total_investing = sum(
            (ln.amount for ln in investing_lines), ZERO
        ).quantize(PRECISION, rounding=ROUND_HALF_UP)
        total_financing = sum(
            (ln.amount for ln in financing_lines), ZERO
        ).quantize(PRECISION, rounding=ROUND_HALF_UP)

        net_change = net_cash_change.quantize(PRECISION, rounding=ROUND_HALF_UP)
        closing_cash = (opening_cash + net_change).quantize(PRECISION, rounding=ROUND_HALF_UP)

        logger.info(
            "cash_flow_generated",
            company_id=str(company_id),
            date_from=str(date_from),
            date_to=str(date_to),
            net_change=float(net_change),
            closing_cash=float(closing_cash),
        )

        return CashFlowReport(
            company_id=company_id,
            date_from=date_from,
            date_to=date_to,
            operating=CashFlowSection(
                title="Operating Activities",
                lines=operating_lines,
                total=total_operating,
            ),
            investing=CashFlowSection(
                title="Investing Activities",
                lines=investing_lines,
                total=total_investing,
            ),
            financing=CashFlowSection(
                title="Financing Activities",
                lines=financing_lines,
                total=total_financing,
            ),
            net_change_in_cash=net_change,
            opening_cash=opening_cash,
            closing_cash=closing_cash,
        )

    # ── Monthly Profit & Loss Trend ───────────────────────────────────────────

    async def get_monthly_profit_and_loss(
        self,
        company_id: UUID,
        date_from: date,
        date_to: date,
    ) -> list[MonthlyPLBucket]:
        """
        Compute month-by-month financial performance (revenue, COGS, expenses,
        gross profit, net profit, margins) across a date range.
        Auto-generates complete earnings and loss trend history.
        """
        target_types = [
            AccountType.REVENUE.value,
            AccountType.EXPENSE.value,
            AccountType.COGS.value,
        ]

        # Extract year-month string formatted as YYYY-MM
        month_expr = func.to_char(JournalItem.date, "YYYY-MM").label("month_str")

        stmt = (
            select(
                month_expr,
                Account.account_type.label("account_type"),
                func.coalesce(func.sum(JournalItem.debit_amount), 0).label("total_debit"),
                func.coalesce(func.sum(JournalItem.credit_amount), 0).label("total_credit"),
            )
            .join(JournalItem, JournalItem.account_id == Account.id)
            .join(JournalEntry, JournalItem.entry_id == JournalEntry.id)
            .where(
                Account.company_id == company_id,
                Account.is_deleted.is_(False),
                Account.account_type.in_(target_types),
                JournalEntry.company_id == company_id,
                JournalEntry.state == EntryState.POSTED.value,
                JournalItem.date >= date_from,
                JournalItem.date <= date_to,
            )
            .group_by(
                month_expr,
                Account.account_type,
            )
            .order_by(month_expr.asc())
        )

        rows = (await self._session.execute(stmt)).all()

        monthly_map: dict[str, dict[str, Decimal]] = {}
        for row in rows:
            m = row.month_str
            if m not in monthly_map:
                monthly_map[m] = {"revenue": ZERO, "cogs": ZERO, "expenses": ZERO}
            
            dr = _d(row.total_debit)
            cr = _d(row.total_credit)

            if row.account_type == AccountType.REVENUE.value:
                # Credit-normal
                monthly_map[m]["revenue"] += (cr - dr)
            elif row.account_type == AccountType.COGS.value:
                # Debit-normal
                monthly_map[m]["cogs"] += (dr - cr)
            else:
                # Expense: debit-normal
                monthly_map[m]["expenses"] += (dr - cr)

        result: list[MonthlyPLBucket] = []
        for m in sorted(monthly_map.keys()):
            data = monthly_map[m]
            rev = data["revenue"].quantize(PRECISION, rounding=ROUND_HALF_UP)
            cogs = data["cogs"].quantize(PRECISION, rounding=ROUND_HALF_UP)
            exp = data["expenses"].quantize(PRECISION, rounding=ROUND_HALF_UP)
            gp = (rev - cogs).quantize(PRECISION, rounding=ROUND_HALF_UP)
            np = (gp - exp).quantize(PRECISION, rounding=ROUND_HALF_UP)
            
            gm_pct = (gp / rev * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if rev > ZERO else ZERO
            nm_pct = (np / rev * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if rev > ZERO else ZERO

            result.append(
                MonthlyPLBucket(
                    month=m,
                    revenue=rev,
                    cogs=cogs,
                    expenses=exp,
                    gross_profit=gp,
                    net_profit=np,
                    gross_margin_pct=gm_pct,
                    net_margin_pct=nm_pct,
                )
            )

        return result

    # ── Partner Financial 360 Summary ─────────────────────────────────────────

    async def get_partner_financial_summary(
        self,
        company_id: UUID,
        partner_id: UUID,
    ) -> PartnerFinancialSummary | None:
        """
        Aggregate comprehensive financial relations for a partner:
        total invoiced, total payments allocated/settled, outstanding residual balance,
        and lifetime journal items.
        """
        # 1. Partner lookup
        partner_stmt = select(Partner).where(
            Partner.id == partner_id,
            Partner.company_id == company_id,
            Partner.is_deleted.is_(False),
        )
        partner = (await self._session.execute(partner_stmt)).scalar_one_or_none()
        if not partner:
            return None

        # 2. Invoices aggregated
        inv_stmt = select(
            func.coalesce(func.sum(Invoice.amount_total), 0).label("tot_invoiced"),
            func.coalesce(func.sum(Invoice.amount_residual), 0).label("tot_residual"),
        ).where(
            Invoice.company_id == company_id,
            Invoice.partner_id == partner_id,
            Invoice.state.in_([InvoiceState.POSTED.value, InvoiceState.PAID.value]),
        )
        inv_res = (await self._session.execute(inv_stmt)).one()
        tot_invoiced = _d(inv_res.tot_invoiced)
        balance_due = _d(inv_res.tot_residual)
        tot_paid = (tot_invoiced - balance_due).quantize(PRECISION, rounding=ROUND_HALF_UP)

        # 3. Count lifetime journal items
        ji_stmt = select(func.count(JournalItem.id)).where(
            JournalItem.company_id == company_id,
            JournalItem.partner_id == partner_id,
        )
        ji_count = (await self._session.execute(ji_stmt)).scalar_one() or 0

        return PartnerFinancialSummary(
            partner_id=partner.id,
            partner_name=partner.name,
            partner_type=partner.partner_type.value if hasattr(partner.partner_type, "value") else str(partner.partner_type),
            total_invoiced=tot_invoiced,
            total_paid=tot_paid,
            balance_due=balance_due,
            lifetime_journal_items_count=ji_count,
        )

    # ── Analytic Profit & Loss Report ─────────────────────────────────────────

    async def get_analytic_profit_and_loss(
        self,
        company_id: UUID,
        date_from: date,
        date_to: date,
    ) -> AnalyticProfitLossReport:
        """
        Profit & Loss segmented by analytic accounts (cost centers / branch plans).
        """
        from app.models.analytic import AnalyticAccount, AnalyticItem, AnalyticPlan

        stmt = (
            select(
                AnalyticAccount.id.label("account_id"),
                AnalyticAccount.code.label("code"),
                AnalyticAccount.name.label("name"),
                AnalyticPlan.name.label("plan_name"),
                func.coalesce(func.sum(case((AnalyticItem.amount > 0, AnalyticItem.amount), else_=0)), 0).label("revenue"),
                func.coalesce(func.sum(case((AnalyticItem.amount < 0, -AnalyticItem.amount), else_=0)), 0).label("cost"),
            )
            .join(AnalyticPlan, AnalyticAccount.plan_id == AnalyticPlan.id)
            .join(AnalyticItem, AnalyticItem.analytic_account_id == AnalyticAccount.id)
            .where(
                AnalyticAccount.company_id == company_id,
                AnalyticItem.date >= date_from,
                AnalyticItem.date <= date_to,
            )
            .group_by(
                AnalyticAccount.id,
                AnalyticAccount.code,
                AnalyticAccount.name,
                AnalyticPlan.name,
            )
            .order_by(AnalyticAccount.name.asc())
        )

        rows = (await self._session.execute(stmt)).all()

        lines: list[AnalyticReportLine] = []
        tot_rev = ZERO
        tot_cost = ZERO

        for r in rows:
            rev = _d(r.revenue)
            cost = _d(r.cost)
            net = (rev - cost).quantize(PRECISION, rounding=ROUND_HALF_UP)
            tot_rev += rev
            tot_cost += cost
            lines.append(
                AnalyticReportLine(
                    account_id=r.account_id,
                    code=r.code,
                    name=r.name,
                    plan_name=r.plan_name,
                    revenue=rev,
                    cost=cost,
                    net_contribution=net,
                )
            )

        return AnalyticProfitLossReport(
            company_id=company_id,
            date_from=date_from,
            date_to=date_to,
            lines=lines,
            total_revenue=tot_rev.quantize(PRECISION, rounding=ROUND_HALF_UP),
            total_cost=tot_cost.quantize(PRECISION, rounding=ROUND_HALF_UP),
            total_net=(tot_rev - tot_cost).quantize(PRECISION, rounding=ROUND_HALF_UP),
        )

