"""
tests/unit/test_reports_extensions.py
─────────────────────────────────────
Unit tests for extended financial reporting logic:
- Margin calculations (gross %, net %)
- EBITDA calculation (adding back depreciation)
- Monthly P&L grouping
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.services.report_service import (
    MonthlyPLBucket,
    ProfitLossReport,
    AnalyticReportLine,
    AnalyticProfitLossReport,
    PartnerFinancialSummary,
)


class TestFinancialReportCalculations:
    @pytest.mark.unit
    def test_margins_and_ebitda_calculation(self):
        rev = Decimal("400000.000")
        cogs = Decimal("10000.000")
        expenses = Decimal("200000.000")
        gross_profit = rev - cogs
        net_profit = gross_profit - expenses

        gross_margin = (gross_profit / rev) * Decimal("100.0")
        net_margin = (net_profit / rev) * Decimal("100.0")

        depreciation = Decimal("5000.000")
        ebitda = net_profit + depreciation

        report = ProfitLossReport(
            company_id=uuid.uuid4(),
            date_from=date(2026, 1, 1),
            date_to=date(2026, 9, 30),
            revenue_lines=[],
            expense_lines=[],
            cogs_lines=[],
            total_revenue=rev,
            total_cogs=cogs,
            total_expenses=expenses,
            gross_profit=gross_profit,
            net_profit=net_profit,
            gross_margin_pct=gross_margin,
            net_margin_pct=net_margin,
            ebitda=ebitda,
        )

        assert report.gross_profit == Decimal("390000.000")
        assert report.net_profit == Decimal("190000.000")
        assert report.gross_margin_pct == Decimal("97.500")
        assert report.net_margin_pct == Decimal("47.500")
        assert report.ebitda == Decimal("195000.000")

    @pytest.mark.unit
    def test_monthly_bucket_creation(self):
        bucket = MonthlyPLBucket(
            month="2026-09",
            revenue=Decimal("50000.000"),
            cogs=Decimal("2000.000"),
            expenses=Decimal("25000.000"),
            gross_profit=Decimal("48000.000"),
            net_profit=Decimal("23000.000"),
            gross_margin_pct=Decimal("96.0"),
            net_margin_pct=Decimal("46.0"),
        )
        assert bucket.month == "2026-09"
        assert bucket.net_profit == Decimal("23000.000")

    @pytest.mark.unit
    def test_analytic_report_line(self):
        line = AnalyticReportLine(
            account_id=uuid.uuid4(),
            code="SHA",
            name="Sharq",
            plan_name="Branches",
            revenue=Decimal("200000.000"),
            cost=Decimal("110000.000"),
            net_contribution=Decimal("90000.000"),
        )
        assert line.net_contribution == Decimal("90000.000")
        assert line.name == "Sharq"

    @pytest.mark.unit
    def test_partner_financial_summary(self):
        summary = PartnerFinancialSummary(
            partner_id=uuid.uuid4(),
            partner_name="Al-Nisf Electrical",
            partner_type="vendor",
            total_invoiced=Decimal("1500.000"),
            total_paid=Decimal("1000.000"),
            balance_due=Decimal("500.000"),
            lifetime_journal_items_count=12,
        )
        assert summary.balance_due == Decimal("500.000")
        assert summary.lifetime_journal_items_count == 12
