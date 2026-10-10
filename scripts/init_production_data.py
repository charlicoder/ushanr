"""
scripts/init_production_data.py
───────────────────────────────
Production Initialization & Bootstrap Script for ushanr Accounting Microservice.

Prepares the ushanr service for immediate production use with:
1. Base & Foreign Currencies (KWD base with 3 decimals; USD, EUR, GBP, SAR, AED)
2. Primary Company Tenant (USHSPA / Kuwait)
3. Standard Chart of Accounts (COA) for Spa, Wellness & Retail Operations:
   - Assets (Cash, Bank, Clearing, Accounts Receivable, Inventory, Fixed Assets)
   - Liabilities (Accounts Payable, Accruals, Tax Payable, Gift Voucher Liabilities)
   - Equity (Owner's Capital, Retained Earnings, Current P&L)
   - Revenue (Spa Services, Retail Products, Add-ons, Gift Vouchers)
   - Cost of Goods Sold (Retail Goods, Consumables & Treatment Supplies)
   - Operating Expenses (Salaries, Rent, Utilities, Marketing, POS/Bank Fees)
4. Production Journals:
   - Sales (Customer Invoices - INV)
   - Customer Credit Notes (RINV)
   - Purchases (Vendor Bills - BILL)
   - Vendor Refunds (RBILL)
   - Bank (NBK & KNET Online Gateways)
   - Cash (Main Till & Branches)
   - Miscellaneous Operations (MISC)
   - Opening Balances (OPEN)
5. Document Sequences (Auto-numbering for invoices, bills, vouchers, payments)
6. Fiscal Years & 12 Monthly Accounting Periods (Current year + Next year in OPEN state)
7. Tax Groups & Tax Codes (0% Exempt VAT, 5% Standard VAT)
8. Analytic Plans & Cost Centers (Sharq, Mangaf, HQ branches & Depts)
9. Bank & Cash Account Profiles (NBK, KNET, Cash Tills)
10. Default Partners (Walk-in Customer, General Cash Vendor)

This script is 100% IDEMPOTENT — safe to run multiple times without duplicating
records or failing on unique constraints.

Usage:
    python scripts/init_production_data.py
    python scripts/init_production_data.py --dry-run
    python scripts/init_production_data.py --company-name "USHSPA" --year 2026
"""
from __future__ import annotations

import argparse
import asyncio
import calendar
import datetime
import logging
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.core.database import Base
from app.models.account import Account, AccountGroup, AccountNature, AccountType
from app.models.analytic import AnalyticAccount, AnalyticPlan
from app.models.bank import BankAccount, BankAccountType
from app.models.company import Company
from app.models.currency import Currency
from app.models.fiscal import AccountingPeriod, FiscalYear, FiscalYearState, PeriodState
from app.models.journal import Journal, JournalType
from app.models.partner import Partner, PartnerType
from app.models.sequence import DocumentSequence
from app.models.tax import Tax, TaxComputation, TaxGroup, TaxType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("init_production_data")


# ─────────────────────────────────────────────────────────────────────────────
# CURRENCIES
# ─────────────────────────────────────────────────────────────────────────────
CURRENCIES_SPEC = [
    {"code": "KWD", "name": "Kuwaiti Dinar", "symbol": "KD", "decimal_places": 3, "is_base": True},
    {"code": "USD", "name": "US Dollar", "symbol": "$", "decimal_places": 2, "is_base": False},
    {"code": "EUR", "name": "Euro", "symbol": "€", "decimal_places": 2, "is_base": False},
    {"code": "GBP", "name": "British Pound", "symbol": "£", "decimal_places": 2, "is_base": False},
    {"code": "SAR", "name": "Saudi Riyal", "symbol": "SR", "decimal_places": 2, "is_base": False},
    {"code": "AED", "name": "UAE Dirham", "symbol": "AED", "decimal_places": 2, "is_base": False},
]


# ─────────────────────────────────────────────────────────────────────────────
# CHART OF ACCOUNTS SPECIFICATION
# (Code, Name, Type, Nature, Is_Reconcilable, Is_Bank, Description)
# ─────────────────────────────────────────────────────────────────────────────
COA_SPEC = [
    # ── 1. ASSETS (100000 - 199999) ──────────────────────────────────────────
    ("101000", "Petty Cash - Main Till", AccountType.ASSET.value, AccountNature.DEBIT.value, False, True, "Main cash drawer on premises"),
    ("101100", "Cash in Transit / Undeposited Funds", AccountType.ASSET.value, AccountNature.DEBIT.value, True, False, "Cash collected awaiting bank deposit"),
    ("102000", "Bank Current Account - NBK", AccountType.ASSET.value, AccountNature.DEBIT.value, False, True, "Primary operating bank account at NBK"),
    ("102100", "KNET & POS Clearing Account", AccountType.ASSET.value, AccountNature.DEBIT.value, True, False, "Settlement clearing for card/KNET receipts"),
    ("102200", "Online Payment Gateway Clearing (TAP/MyFatoorah)", AccountType.ASSET.value, AccountNature.DEBIT.value, True, False, "Clearing for mobile app and web payments"),
    ("105000", "Inventory - Retail & Spa Products", AccountType.ASSET.value, AccountNature.DEBIT.value, False, False, "Merchandise and oils inventory on hand"),
    ("120000", "Accounts Receivable (Customers)", AccountType.ASSET.value, AccountNature.DEBIT.value, True, False, "Trade customer receivables ledger"),
    ("130000", "Prepaid Rent & Expenses", AccountType.ASSET.value, AccountNature.DEBIT.value, False, False, "Advance payments and deposits"),
    ("150000", "Spa Equipment & Fixtures", AccountType.ASSET.value, AccountNature.DEBIT.value, False, False, "Capital spa furniture, beds, and machinery"),
    ("150900", "Accumulated Depreciation - Equipment", AccountType.ASSET.value, AccountNature.CREDIT.value, False, False, "Contra-asset: accumulated depreciation"),
    ("110500", "Tax Receivable (VAT Input)", AccountType.ASSET.value, AccountNature.DEBIT.value, False, False, "Input tax paid on purchases and vendor bills"),

    # ── 2. LIABILITIES (200000 - 299999) ─────────────────────────────────────
    ("210000", "Accounts Payable (Vendors)", AccountType.LIABILITY.value, AccountNature.CREDIT.value, True, False, "Trade vendor payables ledger"),
    ("210500", "Tax Payable (VAT Output)", AccountType.LIABILITY.value, AccountNature.CREDIT.value, False, False, "Tax collected from customers on sales"),
    ("220000", "Accrued Salaries & Benefits", AccountType.LIABILITY.value, AccountNature.CREDIT.value, False, False, "Staff payroll and indemnity accrued"),
    ("221000", "Accrued Rent & Utilities", AccountType.LIABILITY.value, AccountNature.CREDIT.value, False, False, "Accrued operating expenses"),
    ("230000", "Customer Advance / Gift Voucher Liability", AccountType.LIABILITY.value, AccountNature.CREDIT.value, True, False, "Unredeemed gift vouchers & customer advances"),

    # ── 3. EQUITY (300000 - 399999) ──────────────────────────────────────────
    ("301000", "Paid-in Capital", AccountType.EQUITY.value, AccountNature.CREDIT.value, False, False, "Owners / Shareholder equity"),
    ("302000", "Retained Earnings", AccountType.EQUITY.value, AccountNature.CREDIT.value, False, False, "Cumulative retained profits from prior periods"),
    ("303000", "Current Year Profit / Loss", AccountType.EQUITY.value, AccountNature.CREDIT.value, False, False, "Current financial year unallocated income"),

    # ── 4. REVENUE (400000 - 499999) ─────────────────────────────────────────
    ("401000", "Spa Treatment Services Revenue", AccountType.REVENUE.value, AccountNature.CREDIT.value, False, False, "Income from massage, facial, and spa services"),
    ("402000", "Retail Products Sales Revenue", AccountType.REVENUE.value, AccountNature.CREDIT.value, False, False, "Income from skincare and retail product sales"),
    ("403000", "Service Add-ons Revenue", AccountType.REVENUE.value, AccountNature.CREDIT.value, False, False, "Income from treatment upgrades and extra minutes"),
    ("404000", "Gift Voucher Sales Revenue", AccountType.REVENUE.value, AccountNature.CREDIT.value, False, False, "Income recognized upon voucher redemption"),
    ("409000", "Sales Discounts & Promotions Allowed", AccountType.REVENUE.value, AccountNature.DEBIT.value, False, False, "Contra-revenue: promotional and coupon discounts"),

    # ── 5. COST OF GOODS SOLD (500000 - 599999) ──────────────────────────────
    ("501000", "Cost of Retail Goods Sold", AccountType.COGS.value, AccountNature.DEBIT.value, False, False, "Cost of merchandise sold to clients"),
    ("502000", "Treatment Consumables & Supplies", AccountType.COGS.value, AccountNature.DEBIT.value, False, False, "Oils, herbs, linens, and disposable treatment items"),

    # ── 6. OPERATING EXPENSES (600000 - 699999) ──────────────────────────────
    ("601000", "Salaries, Wages & Commissions", AccountType.EXPENSE.value, AccountNature.DEBIT.value, False, False, "Therapist and administrative payroll"),
    ("602000", "Branch Rent Expense", AccountType.EXPENSE.value, AccountNature.DEBIT.value, False, False, "Lease of commercial premises and branches"),
    ("603000", "Utilities (Electricity & Water)", AccountType.EXPENSE.value, AccountNature.DEBIT.value, False, False, "Government utilities charges"),
    ("604000", "Advertising, Marketing & Promotion", AccountType.EXPENSE.value, AccountNature.DEBIT.value, False, False, "Digital ads, influencer campaigns, and print"),
    ("605000", "Bank & POS Terminal Commission Fees", AccountType.EXPENSE.value, AccountNature.DEBIT.value, False, False, "KNET, Visa, and gateway transaction charges"),
    ("606000", "Repairs & Maintenance", AccountType.EXPENSE.value, AccountNature.DEBIT.value, False, False, "Premises and spa equipment upkeep"),
    ("607000", "Telephone, Internet & Software Licenses", AccountType.EXPENSE.value, AccountNature.DEBIT.value, False, False, "Telecom and cloud software subscriptions"),
    ("608000", "Professional & Legal Fees", AccountType.EXPENSE.value, AccountNature.DEBIT.value, False, False, "Accounting, audit, and legal retainer costs"),
    ("609000", "Depreciation Expense", AccountType.EXPENSE.value, AccountNature.DEBIT.value, False, False, "Periodic depreciation of fixed assets"),
    ("690000", "Miscellaneous General Expense", AccountType.EXPENSE.value, AccountNature.DEBIT.value, False, False, "Sundry operational disbursements"),
]


# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENT SEQUENCES SPECIFICATION
# ─────────────────────────────────────────────────────────────────────────────
SEQUENCES_SPEC = [
    {"name": "Customer Invoice Sequence", "code": "invoice_inv", "prefix": "INV", "padding": 5},
    {"name": "Customer Credit Note Sequence", "code": "invoice_credit_note", "prefix": "RINV", "padding": 5},
    {"name": "Vendor Bill Sequence", "code": "invoice_bill", "prefix": "BILL", "padding": 5},
    {"name": "Vendor Refund Sequence", "code": "invoice_vendor_credit", "prefix": "RBILL", "padding": 5},
    {"name": "Customer Payment Receipt Sequence", "code": "payment_customer", "prefix": "PAY", "padding": 5},
    {"name": "Vendor Payment Voucher Sequence", "code": "payment_vendor", "prefix": "VPAY", "padding": 5},
    {"name": "General Journal Voucher Sequence", "code": "journal_misc", "prefix": "MISC", "padding": 4},
    {"name": "Bank Operation Voucher Sequence", "code": "journal_bnk", "prefix": "BNK", "padding": 4},
]


async def bootstrap_production_data(
    company_name: str = "USHSPA",
    base_currency_code: str = "KWD",
    target_year: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Execute the full bootstrap workflow."""
    settings = get_settings()
    engine = create_async_engine(settings.async_database_url, echo=False)

    current_year = target_year or datetime.date.today().year

    summary: dict[str, Any] = {
        "company_id": None,
        "ar_journal_id": None,
        "revenue_account_id": None,
        "addon_account_id": None,
        "ap_journal_id": None,
        "ap_account_id": None,
        "ar_account_id": None,
        "counts": {},
    }

    async with engine.begin() as conn:
        # Ensure all tables are created
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            logger.info("=" * 70)
            logger.info(f"BOOTSTRAPPING USHANR PRODUCTION DATA FOR COMPANY: {company_name}")
            logger.info(f"Base Currency: {base_currency_code} | Fiscal Year: {current_year}")
            logger.info(f"Mode: {'DRY RUN (Will Rollback)' if dry_run else 'PRODUCTION COMMIT'}")
            logger.info("=" * 70)

            # ── 1. Currencies ────────────────────────────────────────────────
            currency_map: dict[str, Currency] = {}
            created_curr = 0
            for c_spec in CURRENCIES_SPEC:
                stmt = select(Currency).where(Currency.code == c_spec["code"])
                curr = (await session.execute(stmt)).scalar_one_or_none()
                if not curr:
                    curr = Currency(
                        code=c_spec["code"],
                        name=c_spec["name"],
                        symbol=c_spec["symbol"],
                        decimal_places=c_spec["decimal_places"],
                        is_active=True,
                        is_base=c_spec["is_base"],
                    )
                    session.add(curr)
                    await session.flush()
                    created_curr += 1
                currency_map[c_spec["code"]] = curr
            logger.info(f"✓ Currencies configured: {len(currency_map)} ({created_curr} newly created)")
            summary["counts"]["currencies"] = len(currency_map)

            # ── 2. Company ───────────────────────────────────────────────────
            stmt = select(Company).where(Company.name == company_name)
            company = (await session.execute(stmt)).scalar_one_or_none()
            if not company:
                company = Company(
                    id=uuid.uuid4(),
                    name=company_name,
                    legal_name=f"{company_name} General Trading & Services Co.",
                    trade_name=company_name,
                    currency_code=base_currency_code,
                    country_code="KW",
                    fiscal_year_start_month=1,
                    decimal_places=3,
                    is_active=True,
                )
                session.add(company)
                await session.flush()
                logger.info(f"✓ Created Company: {company.name} [ID: {company.id}]")
            else:
                logger.info(f"✓ Found existing Company: {company.name} [ID: {company.id}]")
            summary["company_id"] = str(company.id)

            # ── 3. Chart of Accounts ─────────────────────────────────────────
            account_map: dict[str, Account] = {}
            created_accts = 0
            for code, name, acct_type, nature, reconcilable, is_bank, desc in COA_SPEC:
                stmt = select(Account).where(
                    Account.company_id == company.id,
                    Account.code == code,
                )
                acct = (await session.execute(stmt)).scalar_one_or_none()
                if not acct:
                    acct = Account(
                        company_id=company.id,
                        code=code,
                        name=name,
                        account_type=acct_type,
                        account_nature=nature,
                        is_reconcilable=reconcilable,
                        is_bank_account=is_bank,
                        allow_reconciliation=True,
                        currency_code=base_currency_code,
                        description=desc,
                        is_active=True,
                    )
                    session.add(acct)
                    await session.flush()
                    created_accts += 1
                account_map[code] = acct

            logger.info(f"✓ Chart of Accounts ready: {len(account_map)} accounts ({created_accts} newly created)")
            summary["counts"]["accounts"] = len(account_map)

            ar_acct = account_map["120000"]
            ap_acct = account_map["210000"]
            revenue_acct = account_map["401000"]
            addon_acct = account_map["403000"]
            exp_acct = account_map["690000"]
            bank_gl = account_map["102000"]
            cash_gl = account_map["101000"]

            summary["ar_account_id"] = str(ar_acct.id)
            summary["ap_account_id"] = str(ap_acct.id)
            summary["revenue_account_id"] = str(revenue_acct.id)
            summary["addon_account_id"] = str(addon_acct.id)

            # ── 4. Production Journals ───────────────────────────────────────
            # (Code, Name, Type, Default Account, Payment Debit Acct, Payment Credit Acct, Seq Prefix)
            JOURNALS_SPEC = [
                ("INV", "Customer Invoices (Sales)", JournalType.SALE.value, revenue_acct.id, ar_acct.id, None, "INV"),
                ("RINV", "Customer Credit Notes", JournalType.SALE.value, revenue_acct.id, None, ar_acct.id, "RINV"),
                ("BILL", "Vendor Bills (Purchases)", JournalType.PURCHASE.value, exp_acct.id, None, ap_acct.id, "BILL"),
                ("RBILL", "Vendor Refunds & Credits", JournalType.PURCHASE.value, exp_acct.id, ap_acct.id, None, "RBILL"),
                ("BNK-NBK", "National Bank of Kuwait (NBK)", JournalType.BANK.value, bank_gl.id, bank_gl.id, bank_gl.id, "BNK"),
                ("BNK-KNET", "KNET / POS Electronic Settlements", JournalType.BANK.value, account_map["102100"].id, account_map["102100"].id, account_map["102100"].id, "KNET"),
                ("CSH-MAIN", "Cash Register - Main Till", JournalType.CASH.value, cash_gl.id, cash_gl.id, cash_gl.id, "CSH"),
                ("MISC", "General & Miscellaneous Operations", JournalType.GENERAL.value, None, None, None, "MISC"),
                ("OPEN", "Opening Balances & Adjustments", JournalType.GENERAL.value, None, None, None, "OPEN"),
            ]

            journal_map: dict[str, Journal] = {}
            created_journals = 0
            for code, name, jtype, def_acct, debit_acct, credit_acct, prefix in JOURNALS_SPEC:
                stmt = select(Journal).where(
                    Journal.company_id == company.id,
                    Journal.code == code,
                )
                j = (await session.execute(stmt)).scalar_one_or_none()
                if not j:
                    j = Journal(
                        company_id=company.id,
                        code=code,
                        name=name,
                        journal_type=jtype,
                        default_account_id=def_acct,
                        payment_debit_account_id=debit_acct,
                        payment_credit_account_id=credit_acct,
                        sequence_prefix=prefix,
                        sequence_padding=5 if jtype in (JournalType.SALE.value, JournalType.PURCHASE.value) else 4,
                        currency_code=base_currency_code,
                        is_active=True,
                        show_on_dashboard=True,
                    )
                    session.add(j)
                    await session.flush()
                    created_journals += 1
                else:
                    # Ensure default accounts are updated if missing
                    updated = False
                    if not j.default_account_id and def_acct:
                        j.default_account_id = def_acct
                        updated = True
                    if not j.payment_debit_account_id and debit_acct:
                        j.payment_debit_account_id = debit_acct
                        updated = True
                    if not j.payment_credit_account_id and credit_acct:
                        j.payment_credit_account_id = credit_acct
                        updated = True
                    if updated:
                        session.add(j)
                        await session.flush()
                journal_map[code] = j

            logger.info(f"✓ Journals ready: {len(journal_map)} ({created_journals} newly created)")
            summary["counts"]["journals"] = len(journal_map)
            summary["ar_journal_id"] = str(journal_map["INV"].id)
            summary["ap_journal_id"] = str(journal_map["BILL"].id)

            # ── 5. Document Sequences ────────────────────────────────────────
            created_seqs = 0
            for s_spec in SEQUENCES_SPEC:
                stmt = select(DocumentSequence).where(
                    DocumentSequence.company_id == company.id,
                    DocumentSequence.code == s_spec["code"],
                )
                seq = (await session.execute(stmt)).scalar_one_or_none()
                if not seq:
                    seq = DocumentSequence(
                        company_id=company.id,
                        name=s_spec["name"],
                        code=s_spec["code"],
                        prefix=s_spec["prefix"],
                        padding=s_spec["padding"],
                        next_number=1,
                        step=1,
                        use_date_range=True,
                        is_active=True,
                    )
                    session.add(seq)
                    await session.flush()
                    created_seqs += 1
            logger.info(f"✓ Document Sequences ready: {len(SEQUENCES_SPEC)} ({created_seqs} newly created)")
            summary["counts"]["sequences"] = len(SEQUENCES_SPEC)

            # ── 6. Fiscal Years & 12 Accounting Periods ──────────────────────
            # Generate for current year and next year
            years_to_provision = [current_year, current_year + 1]
            total_periods = 0
            for yr in years_to_provision:
                fy_name = f"FY-{yr}"
                stmt = select(FiscalYear).where(
                    FiscalYear.company_id == company.id,
                    FiscalYear.name == fy_name,
                )
                fy = (await session.execute(stmt)).scalar_one_or_none()
                if not fy:
                    fy = FiscalYear(
                        company_id=company.id,
                        name=fy_name,
                        date_from=datetime.date(yr, 1, 1),
                        date_to=datetime.date(yr, 12, 31),
                        state=FiscalYearState.OPEN.value,
                        is_active=True,
                    )
                    session.add(fy)
                    await session.flush()
                    logger.info(f"  Created Fiscal Year: {fy_name}")

                # Monthly periods for this year
                for m in range(1, 13):
                    last_day = calendar.monthrange(yr, m)[1]
                    p_from = datetime.date(yr, m, 1)
                    p_to = datetime.date(yr, m, last_day)
                    p_name = f"{yr}-{m:02d}"

                    stmt = select(AccountingPeriod).where(
                        AccountingPeriod.company_id == company.id,
                        AccountingPeriod.date_from == p_from,
                        AccountingPeriod.date_to == p_to,
                    )
                    period = (await session.execute(stmt)).scalar_one_or_none()
                    if not period:
                        period = AccountingPeriod(
                            company_id=company.id,
                            fiscal_year_id=fy.id,
                            name=p_name,
                            date_from=p_from,
                            date_to=p_to,
                            state=PeriodState.OPEN.value,
                        )
                        session.add(period)
                        await session.flush()
                    total_periods += 1

            logger.info(f"✓ Fiscal Years & Periods ready: {len(years_to_provision)} years with {total_periods} monthly periods")
            summary["counts"]["fiscal_periods"] = total_periods

            # ── 7. Taxes & Tax Groups ────────────────────────────────────────
            stmt = select(TaxGroup).where(
                TaxGroup.company_id == company.id,
                TaxGroup.name == "VAT",
            )
            tax_group = (await session.execute(stmt)).scalar_one_or_none()
            if not tax_group:
                tax_group = TaxGroup(
                    company_id=company.id,
                    name="VAT",
                    sequence=10,
                )
                session.add(tax_group)
                await session.flush()

            # Zero VAT / Exempt tax (Standard in Kuwait)
            stmt = select(Tax).where(
                Tax.company_id == company.id,
                Tax.name == "Zero VAT (0%)",
            )
            tax_zero = (await session.execute(stmt)).scalar_one_or_none()
            if not tax_zero:
                tax_zero = Tax(
                    company_id=company.id,
                    name="Zero VAT (0%)",
                    description="Zero-rated / Exempt tax for Kuwait spa services",
                    tax_type=TaxType.SALE.value,
                    computation=TaxComputation.PERCENTAGE.value,
                    amount=0.0,
                    tax_account_id=account_map["210500"].id,
                    group_id=tax_group.id,
                    is_active=True,
                    sequence=10,
                )
                session.add(tax_zero)
                await session.flush()

            # Optional 5% Standard VAT (GCC Tax ready)
            stmt = select(Tax).where(
                Tax.company_id == company.id,
                Tax.name == "Standard VAT (5%)",
            )
            tax_5 = (await session.execute(stmt)).scalar_one_or_none()
            if not tax_5:
                tax_5 = Tax(
                    company_id=company.id,
                    name="Standard VAT (5%)",
                    description="Standard 5% VAT rate",
                    tax_type=TaxType.SALE.value,
                    computation=TaxComputation.PERCENTAGE.value,
                    amount=5.0,
                    tax_account_id=account_map["210500"].id,
                    group_id=tax_group.id,
                    is_active=True,
                    sequence=20,
                )
                session.add(tax_5)
                await session.flush()

            logger.info("✓ Tax groups & standard taxes configured")
            summary["counts"]["taxes"] = 2

            # ── 8. Analytic Accounting (Cost Centers) ────────────────────────
            stmt = select(AnalyticPlan).where(
                AnalyticPlan.company_id == company.id,
                AnalyticPlan.code == "BRANCHES",
            )
            plan_branches = (await session.execute(stmt)).scalar_one_or_none()
            if not plan_branches:
                plan_branches = AnalyticPlan(
                    company_id=company.id,
                    name="Branches & Physical Locations",
                    code="BRANCHES",
                    description="Cost centers for operating spa branch locations",
                    is_active=True,
                    sequence=10,
                )
                session.add(plan_branches)
                await session.flush()

            branches_cost_centers = [
                ("BR-SHARQ", "Sharq Branch"),
                ("BR-MANGAF", "Mangaf Branch"),
                ("HQ-ADMIN", "Headquarters / Central Admin"),
            ]
            for b_code, b_name in branches_cost_centers:
                stmt = select(AnalyticAccount).where(
                    AnalyticAccount.company_id == company.id,
                    AnalyticAccount.plan_id == plan_branches.id,
                    AnalyticAccount.code == b_code,
                )
                an_acct = (await session.execute(stmt)).scalar_one_or_none()
                if not an_acct:
                    an_acct = AnalyticAccount(
                        company_id=company.id,
                        plan_id=plan_branches.id,
                        code=b_code,
                        name=b_name,
                        is_active=True,
                    )
                    session.add(an_acct)
                    await session.flush()

            logger.info("✓ Analytic Plans & Branch Cost Centers created (Sharq, Mangaf, HQ)")
            summary["counts"]["analytic_accounts"] = len(branches_cost_centers)

            # ── 9. Bank Accounts ─────────────────────────────────────────────
            stmt = select(BankAccount).where(
                BankAccount.company_id == company.id,
                BankAccount.name == "NBK Operating Account",
            )
            nbk_bank = (await session.execute(stmt)).scalar_one_or_none()
            if not nbk_bank:
                nbk_bank = BankAccount(
                    company_id=company.id,
                    account_id=bank_gl.id,
                    journal_id=journal_map["BNK-NBK"].id,
                    name="NBK Operating Account",
                    bank_name="National Bank of Kuwait",
                    bank_code="NBK",
                    currency_code=base_currency_code,
                    account_type=BankAccountType.BANK.value,
                    is_active=True,
                )
                session.add(nbk_bank)
                await session.flush()

            stmt = select(BankAccount).where(
                BankAccount.company_id == company.id,
                BankAccount.name == "Main Cash Till",
            )
            cash_bank = (await session.execute(stmt)).scalar_one_or_none()
            if not cash_bank:
                cash_bank = BankAccount(
                    company_id=company.id,
                    account_id=cash_gl.id,
                    journal_id=journal_map["CSH-MAIN"].id,
                    name="Main Cash Till",
                    bank_name="Cash Till",
                    currency_code=base_currency_code,
                    account_type=BankAccountType.CASH.value,
                    is_active=True,
                )
                session.add(cash_bank)
                await session.flush()
            logger.info("✓ Bank and Cash Profiles configured")

            # ── 10. Default Partners (Walk-in Customer & Cash Vendor) ────────
            stmt = select(Partner).where(
                Partner.company_id == company.id,
                Partner.name == "Walk-in Customer",
            )
            walkin_customer = (await session.execute(stmt)).scalar_one_or_none()
            if not walkin_customer:
                walkin_customer = Partner(
                    company_id=company.id,
                    name="Walk-in Customer",
                    display_name="General Walk-in Customer",
                    partner_type=PartnerType.CUSTOMER.value,
                    is_customer=True,
                    is_vendor=False,
                    receivable_account_id=ar_acct.id,
                    currency_code=base_currency_code,
                    country_code="KW",
                    is_active=True,
                )
                session.add(walkin_customer)
                await session.flush()
                logger.info(f"  Created default customer: {walkin_customer.name}")

            stmt = select(Partner).where(
                Partner.company_id == company.id,
                Partner.name == "General Cash Vendor",
            )
            cash_vendor = (await session.execute(stmt)).scalar_one_or_none()
            if not cash_vendor:
                cash_vendor = Partner(
                    company_id=company.id,
                    name="General Cash Vendor",
                    display_name="General Petty Cash Vendor",
                    partner_type=PartnerType.VENDOR.value,
                    is_customer=False,
                    is_vendor=True,
                    payable_account_id=ap_acct.id,
                    currency_code=base_currency_code,
                    country_code="KW",
                    is_active=True,
                )
                session.add(cash_vendor)
                await session.flush()
                logger.info(f"  Created default vendor: {cash_vendor.name}")

            logger.info("✓ Default Partners provisioned")

            if dry_run:
                await session.rollback()
                logger.warning("\nDRY RUN: All changes were rolled back as requested.")
            else:
                logger.info("\n" + "=" * 70)
                logger.info("SUCCESS: Production bootstrap completed and committed successfully!")
                logger.info("=" * 70)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap ushanr production database with Chart of Accounts, Journals, and Sequences."
    )
    parser.add_argument(
        "--company-name",
        type=str,
        default="USHSPA",
        help="Primary tenant company name (default: USHSPA)",
    )
    parser.add_argument(
        "--currency",
        type=str,
        default="KWD",
        help="Base currency code (default: KWD)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Starting fiscal year (default: current year)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the bootstrap without committing changes",
    )

    args = parser.parse_args()

    try:
        summary = asyncio.run(
            bootstrap_production_data(
                company_name=args.company_name,
                base_currency_code=args.currency,
                target_year=args.year,
                dry_run=args.dry_run,
            )
        )
    except Exception as exc:
        logger.error(f"Bootstrap failed: {exc}", exc_info=True)
        sys.exit(1)

    print("\n" + "─" * 70)
    print("PRODUCTION INTEGRATION ENVIRONMENT VARIABLES (e.g. for ushnotice/.env):")
    print("─" * 70)
    print(f"USHANR_COMPANY_ID={summary['company_id']}")
    print(f"USHANR_AR_JOURNAL_ID={summary['ar_journal_id']}")
    print(f"USHANR_REVENUE_ACCOUNT_ID={summary['revenue_account_id']}")
    print(f"USHANR_ADDON_ACCOUNT_ID={summary['addon_account_id']}")
    print("─" * 70)


if __name__ == "__main__":
    main()
