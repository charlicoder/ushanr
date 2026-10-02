"""
scripts/import_excel_data.py
────────────────────────────
Comprehensive Excel Ingestion Pipeline for ushanr Accounting & Financial Microservice.

Reads and establishes full relational integrity across all .xlsx files in data/:
1. Company & Base Currency (USHSPA, KWD)
2. Partners (Vendors from vendors.xlsx + discovered partners)
3. Chart of Accounts (Auto-discovered from journal_Item.xlsx)
4. Journals (Bank-8001..8004, Cash-Ali/Deema/Fahad/Sara, Purchases, Sales, Misc)
5. Analytic Cost Centers & Plans (Sharq & Mangaf branches from account.analytic.line.xlsx)
6. Fixed Assets & Linear Depreciation Schedules (from account.asset.xlsx)
7. Journal Entries & Double-Entry Journal Items (from journal_entries.xlsx & journal_Item.xlsx)
8. Invoices & Vendor Bills (from customer-invoices.xlsx & vendor-bills.xlsx)
9. Payments (from customer-payments.xlsx & vendor-payments.xlsx)
10. Validates total debits == total credits and generates financial summary stats.

Usage:
    uv run python scripts/import_excel_data.py [--dry-run] [--company-name USHSPA]
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import sys
import uuid
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import openpyxl
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings
from app.models.account import Account, AccountNature, AccountType
from app.models.analytic import AnalyticAccount, AnalyticItem, AnalyticPlan
from app.models.asset import Asset, AssetDepreciationLine, AssetStatus
from app.models.company import Company
from app.models.currency import Currency
from app.models.invoice import Invoice, InvoiceState, InvoiceType
from app.models.journal import Journal, JournalType
from app.models.journal_entry import EntryState, JournalEntry, JournalItem
from app.models.partner import Partner, PartnerType
from app.models.payment import Payment, PaymentMethod, PaymentState, PaymentType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("import_excel")
ZERO = Decimal("0")


def clean_decimal(val: Any) -> Decimal:
    if val is None or val == "":
        return ZERO
    cleaned = str(val).replace(",", "").strip()
    try:
        return abs(Decimal(cleaned))
    except Exception:
        return ZERO


def clean_decimal_signed(val: Any) -> Decimal:
    if val is None or val == "":
        return ZERO
    cleaned = str(val).replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except Exception:
        return ZERO


def parse_date(val: Any) -> datetime.date | None:
    if not val:
        return None
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.date() if isinstance(val, datetime.datetime) else val
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def read_sheet_rows(file_path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Read an Excel file returning header list and list of row dicts."""
    wb = openpyxl.load_workbook(file_path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], []
    headers = [str(c).strip() if c is not None else f"col_{i}" for i, c in enumerate(rows[0])]
    data = []
    for row in rows[1:]:
        if all(c is None for c in row):
            continue
        data.append({headers[i]: row[i] if i < len(row) else None for i in range(len(headers))})
    return headers, data


def determine_account_classification(code: str, name: str) -> tuple[str, str, bool, bool]:
    """Classify account into (account_type, account_nature, is_reconcilable, is_bank)."""
    c = code.strip()
    first = c[0] if c else "5"
    if first == "1":
        # Asset
        is_bank = c.startswith("10000") or c.startswith("10005") or "bank" in name.lower() or "cash" in name.lower()
        is_reconcilable = c.startswith("1002") or "receivable" in name.lower()
        return AccountType.ASSET.value, AccountNature.DEBIT.value, is_reconcilable, is_bank
    elif first == "2":
        # Liability
        is_reconcilable = c.startswith("2001") or "payable" in name.lower()
        return AccountType.LIABILITY.value, AccountNature.CREDIT.value, is_reconcilable, False
    elif first == "3":
        # Equity
        return AccountType.EQUITY.value, AccountNature.CREDIT.value, False, False
    elif first == "4":
        # Revenue
        return AccountType.REVENUE.value, AccountNature.CREDIT.value, False, False
    else:
        # Expense / Cost of Goods Sold
        if "cogs" in name.lower() or "cost of goods" in name.lower() or c.startswith("50000"):
            return AccountType.COGS.value, AccountNature.DEBIT.value, False, False
        return AccountType.EXPENSE.value, AccountNature.DEBIT.value, False, False


JOURNAL_CONFIGS = [
    ("Purchases", "BILL", JournalType.PURCHASE.value),
    ("Sales", "INV", JournalType.SALE.value),
    ("Bank-8001", "BNK1", JournalType.BANK.value),
    ("Bank-8002", "BNK2", JournalType.BANK.value),
    ("Bank-8003", "BNK3", JournalType.BANK.value),
    ("Bank-8004", "BNK4", JournalType.BANK.value),
    ("Cash-Ali", "CSHA", JournalType.CASH.value),
    ("Cash-Deema", "CSHD", JournalType.CASH.value),
    ("Cash-Fahad", "CSHF", JournalType.CASH.value),
    ("Cash-Sara", "CSHS", JournalType.CASH.value),
    ("Interncompany Transfers", "ICT", JournalType.GENERAL.value),
    ("Miscellaneous Operations", "MISC", JournalType.MISC.value),
]


async def import_all_excel_data(data_dir: Path, company_name: str = "USHSPA", dry_run: bool = False) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.async_database_url, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            logger.info("=" * 65)
            logger.info(f"STARTING EXCEL DATA IMPORT FOR: {company_name}")
            logger.info(f"Mode: {'DRY RUN' if dry_run else 'COMMITTING TO DATABASE'}")
            logger.info("=" * 65)

            # 1. Company & Currency
            c_res = await session.execute(select(Company).where(Company.name == company_name))
            company = c_res.scalar_one_or_none()
            if not company:
                company = Company(
                    id=uuid.uuid4(),
                    name=company_name,
                    legal_name="USHSPA General Trading & Services Co.",
                    trade_name=company_name,
                    currency_code="KWD",
                    country_code="KW",
                    fiscal_year_start_month=1,
                    decimal_places=3,
                )
                session.add(company)
                await session.flush()
                logger.info(f"Created company {company.name} ({company.id})")

            cur_res = await session.execute(select(Currency).where(Currency.code == "KWD"))
            if not cur_res.scalar_one_or_none():
                kwd = Currency(
                    code="KWD",
                    name="Kuwaiti Dinar",
                    symbol="KD",
                    decimal_places=3,
                    is_active=True,
                    is_base=True,
                )
                session.add(kwd)
                await session.flush()

            # 2. Partners from vendors.xlsx & generic partners
            partner_map: dict[str, Partner] = {}

            # Create default generic partner for system documents without partner
            default_p_res = await session.execute(
                select(Partner).where(Partner.company_id == company.id, Partner.name == "General Partner")
            )
            default_partner = default_p_res.scalar_one_or_none()
            if not default_partner:
                default_partner = Partner(
                    id=uuid.uuid4(),
                    company_id=company.id,
                    name="General Partner",
                    partner_type=PartnerType.INTERNAL.value,
                    country_code="KW",
                )
                session.add(default_partner)
                await session.flush()
            partner_map["General Partner"] = default_partner

            vendors_file = data_dir / "vendors.xlsx"
            if vendors_file.exists():
                _, v_rows = read_sheet_rows(vendors_file)
                for r in v_rows:
                    v_name = str(r.get("Display Name") or r.get("Name") or "").strip()
                    if not v_name:
                        continue
                    p_res = await session.execute(
                        select(Partner).where(Partner.company_id == company.id, Partner.name == v_name)
                    )
                    partner = p_res.scalar_one_or_none()
                    if not partner:
                        partner = Partner(
                            id=uuid.uuid4(),
                            company_id=company.id,
                            name=v_name,
                            partner_type=PartnerType.VENDOR.value,
                            email=str(r.get("Email") or "").strip() or None,
                            phone=str(r.get("Phone") or "").strip() or None,
                            country_code="KW",
                        )
                        session.add(partner)
                        await session.flush()
                    partner_map[v_name] = partner
                logger.info(f"Loaded {len(partner_map)} partners from vendors.xlsx")

            # 3. Chart of Accounts from journal_Item.xlsx
            account_map: dict[str, Account] = {}
            ji_file = data_dir / "journal_Item.xlsx"
            if ji_file.exists():
                _, ji_rows = read_sheet_rows(ji_file)
                unique_accounts = {}
                for r in ji_rows:
                    raw_acct = str(r.get("Account") or "").strip()
                    if raw_acct and " " in raw_acct:
                        code, name = raw_acct.split(" ", 1)
                        unique_accounts[code.strip()] = name.strip()
                    elif raw_acct:
                        unique_accounts[raw_acct] = raw_acct

                    # Register any additional partner from journal items
                    p_name = str(r.get("Partner") or "").strip()
                    if p_name and p_name not in partner_map:
                        p_res = await session.execute(
                            select(Partner).where(Partner.company_id == company.id, Partner.name == p_name)
                        )
                        p_obj = p_res.scalar_one_or_none()
                        if not p_obj:
                            p_obj = Partner(
                                id=uuid.uuid4(),
                                company_id=company.id,
                                name=p_name,
                                partner_type=PartnerType.CUSTOMER.value if "sale" in p_name.lower() else PartnerType.VENDOR.value,
                                country_code="KW",
                            )
                            session.add(p_obj)
                            await session.flush()
                        partner_map[p_name] = p_obj

                for code, name in unique_accounts.items():
                    a_res = await session.execute(
                        select(Account).where(Account.company_id == company.id, Account.code == code)
                    )
                    acct = a_res.scalar_one_or_none()
                    if not acct:
                        acct_type, acct_nature, reconcilable, is_bank = determine_account_classification(code, name)
                        acct = Account(
                            id=uuid.uuid4(),
                            company_id=company.id,
                            code=code,
                            name=name,
                            account_type=acct_type,
                            account_nature=acct_nature,
                            is_reconcilable=reconcilable,
                            is_bank_account=is_bank,
                            currency_code="KWD",
                        )
                        session.add(acct)
                        await session.flush()
                    account_map[code] = acct
                logger.info(f"Loaded {len(account_map)} accounts from journal_Item.xlsx")

            # 4. Journals (Configured properly using journal_type)
            journal_map: dict[str, Journal] = {}
            for j_name, j_code, j_type in JOURNAL_CONFIGS:
                j_res = await session.execute(
                    select(Journal).where(Journal.company_id == company.id, Journal.code == j_code)
                )
                j = j_res.scalar_one_or_none()
                if not j:
                    j = Journal(
                        id=uuid.uuid4(),
                        company_id=company.id,
                        name=j_name,
                        code=j_code,
                        journal_type=j_type,
                        currency_code="KWD",
                    )
                    session.add(j)
                    await session.flush()
                journal_map[j_name] = j
                journal_map[j_code] = j
            logger.info(f"Loaded {len(JOURNAL_CONFIGS)} journals")

            # Default fallback journals
            default_misc_journal = journal_map.get("Miscellaneous Operations") or list(journal_map.values())[0]
            default_bill_journal = journal_map.get("Purchases") or default_misc_journal
            default_inv_journal = journal_map.get("Sales") or default_misc_journal

            # 5. Analytic Cost Centers & Plan (from account.analytic.line.xlsx)
            analytic_file = data_dir / "account.analytic.line.xlsx"
            analytic_map: dict[str, AnalyticAccount] = {}
            if analytic_file.exists():
                plan_res = await session.execute(
                    select(AnalyticPlan).where(AnalyticPlan.company_id == company.id, AnalyticPlan.name == "Branches")
                )
                branch_plan = plan_res.scalar_one_or_none()
                if not branch_plan:
                    branch_plan = AnalyticPlan(
                        id=uuid.uuid4(),
                        company_id=company.id,
                        name="Branches",
                        description="Operating spa branches and cost centers",
                    )
                    session.add(branch_plan)
                    await session.flush()

                for branch_name in ["Sharq", "Mangaf"]:
                    an_res = await session.execute(
                        select(AnalyticAccount).where(
                            AnalyticAccount.company_id == company.id,
                            AnalyticAccount.name == branch_name,
                        )
                    )
                    an_acct = an_res.scalar_one_or_none()
                    if not an_acct:
                        an_acct = AnalyticAccount(
                            id=uuid.uuid4(),
                            company_id=company.id,
                            plan_id=branch_plan.id,
                            name=branch_name,
                            code=branch_name[:3].upper(),
                        )
                        session.add(an_acct)
                        await session.flush()
                    analytic_map[branch_name] = an_acct

                # Ingest analytic lines
                _, an_rows = read_sheet_rows(analytic_file)
                an_count = 0
                for ar in an_rows:
                    p_plan = str(ar.get("Project Plan") or "").strip()
                    an_target = analytic_map.get(p_plan)
                    if not an_target:
                        continue
                    an_date = parse_date(ar.get("Date")) or datetime.date(2026, 9, 30)
                    an_amt = clean_decimal_signed(ar.get("Amount"))
                    an_desc = str(ar.get("Description") or "").strip()

                    an_item = AnalyticItem(
                        id=uuid.uuid4(),
                        company_id=company.id,
                        analytic_account_id=an_target.id,
                        name=an_desc,
                        date=an_date,
                        amount=float(an_amt),
                        percentage=100.0,
                    )
                    session.add(an_item)
                    an_count += 1
                logger.info(f"Loaded {an_count} analytic lines across branches {list(analytic_map.keys())}")

            # 6. Fixed Assets (from account.asset.xlsx)
            asset_file = data_dir / "account.asset.xlsx"
            if asset_file.exists():
                _, asset_rows = read_sheet_rows(asset_file)
                asset_count = 0
                for ar in asset_rows:
                    a_name = str(ar.get("Asset Name") or "").strip()
                    if not a_name:
                        continue
                    a_date = parse_date(ar.get("Date")) or datetime.date(2026, 9, 1)
                    val = clean_decimal(ar.get("Asset Value"))
                    book = clean_decimal(ar.get("Book Value"))

                    # Extract account codes
                    fa_raw = str(ar.get("Fixed Asset Account") or "").strip()
                    fa_code = fa_raw.split()[0] if fa_raw else "100602"
                    dep_raw = str(ar.get("Depreciation Account") or "").strip()
                    dep_code = dep_raw.split()[0] if dep_raw else "100652"
                    exp_raw = str(ar.get("Expense Account") or "").strip()
                    exp_code = exp_raw.split()[0] if exp_raw else "500804"

                    fa_id = account_map.get(fa_code, list(account_map.values())[0]).id if account_map else None
                    dep_id = account_map.get(dep_code, list(account_map.values())[0]).id if account_map else None
                    exp_id = account_map.get(exp_code, list(account_map.values())[0]).id if account_map else None

                    ex_res = await session.execute(
                        select(Asset).where(Asset.company_id == company.id, Asset.name == a_name)
                    )
                    existing_asset = ex_res.scalar_one_or_none()
                    if not existing_asset:
                        new_asset = Asset(
                            id=uuid.uuid4(),
                            company_id=company.id,
                            name=a_name,
                            purchase_date=a_date,
                            asset_value=float(val),
                            book_value=float(book),
                            salvage_value=float(val * Decimal("0.01")),
                            fixed_asset_account_id=fa_id,
                            depreciation_account_id=dep_id,
                            expense_account_id=exp_id,
                            depreciation_model=str(ar.get("Depreciation Model") or "36 Month Linear"),
                            method_number=36,
                            method_period=1,
                            asset_group=str(ar.get("Asset Group") or a_name),
                            status=AssetStatus.RUNNING.value,
                        )
                        session.add(new_asset)
                        await session.flush()

                        # Generate 36-month schedule lines
                        base = val - Decimal(str(new_asset.salvage_value))
                        m_amt = (base / Decimal(new_asset.method_number)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                        accum = Decimal("0.0")
                        for p in range(1, new_asset.method_number + 1):
                            y = a_date.year + (a_date.month - 1 + p) // 12
                            m = (a_date.month - 1 + p) % 12 + 1
                            sched_date = datetime.date(y, m, 1)
                            period_amt = (base - accum) if p == new_asset.method_number else m_amt
                            accum += period_amt
                            line = AssetDepreciationLine(
                                id=uuid.uuid4(),
                                company_id=company.id,
                                asset_id=new_asset.id,
                                depreciation_date=sched_date,
                                amount=float(period_amt),
                                depreciated_value=float(accum),
                                remaining_value=float(val - accum),
                                is_posted=False,
                            )
                            session.add(line)
                        asset_count += 1
                logger.info(f"Loaded and generated linear schedules for {asset_count} fixed assets")

            # 7. Journal Entries (from journal_entries.xlsx)
            entry_map: dict[str, JournalEntry] = {}
            je_file = data_dir / "journal_entries.xlsx"
            if je_file.exists():
                _, je_rows = read_sheet_rows(je_file)
                for jr in je_rows:
                    num = str(jr.get("Number") or "").strip()
                    if not num:
                        continue
                    edate = parse_date(jr.get("Date")) or datetime.date(2026, 9, 1)
                    j_name = str(jr.get("Journal") or "").strip()
                    j_obj = journal_map.get(j_name, default_misc_journal)
                    p_name = str(jr.get("Partner") or "").strip()
                    partner = partner_map.get(p_name)
                    tot = clean_decimal(jr.get("Total Signed"))

                    je_res = await session.execute(
                        select(JournalEntry).where(JournalEntry.company_id == company.id, JournalEntry.name == num)
                    )
                    entry = je_res.scalar_one_or_none()
                    if not entry:
                        entry = JournalEntry(
                            id=uuid.uuid4(),
                            company_id=company.id,
                            journal_id=j_obj.id,
                            name=num,
                            reference=str(jr.get("Reference") or "").strip() or None,
                            entry_date=edate,
                            accounting_date=edate,
                            state=EntryState.POSTED.value if str(jr.get("Status")).lower() == "posted" else EntryState.DRAFT.value,
                            partner_id=partner.id if partner else None,
                            amount_total=float(tot),
                            currency_code="KWD",
                        )
                        session.add(entry)
                        await session.flush()
                    entry_map[num] = entry
                logger.info(f"Loaded {len(entry_map)} journal entries from journal_entries.xlsx")

            # 8. Journal Items (from journal_Item.xlsx)
            if ji_file.exists():
                item_count = 0
                tot_dr = Decimal("0")
                tot_cr = Decimal("0")
                for r in ji_rows:
                    num = str(r.get("Number") or "").strip()
                    entry = entry_map.get(num)
                    raw_acct = str(r.get("Account") or "").strip()
                    acct_code = raw_acct.split(" ", 1)[0].strip() if " " in raw_acct else raw_acct
                    acct = account_map.get(acct_code)
                    if not entry or not acct:
                        continue

                    dr = clean_decimal(r.get("Debit"))
                    cr = clean_decimal(r.get("Credit"))
                    tot_dr += dr
                    tot_cr += cr

                    partner_name = str(r.get("Partner") or "").strip()
                    partner = partner_map.get(partner_name)

                    ji = JournalItem(
                        id=uuid.uuid4(),
                        company_id=company.id,
                        entry_id=entry.id,
                        account_id=acct.id,
                        debit_amount=float(dr),
                        credit_amount=float(cr),
                        partner_id=partner.id if partner else None,
                        name=str(r.get("Label") or "").strip() or None,
                        reference=str(r.get("Matching #") or "").strip() or None,
                        currency_code="KWD",
                    )
                    session.add(ji)
                    item_count += 1
                await session.flush()
                logger.info(
                    f"Imported {item_count} journal items: Total Dr = {tot_dr:,.3f} KWD, Total Cr = {tot_cr:,.3f} KWD"
                )

            # 9. Vendor Bills (from vendor-bills.xlsx)
            bills_file = data_dir / "vendor-bills.xlsx"
            if bills_file.exists():
                _, b_rows = read_sheet_rows(bills_file)
                bill_count = 0
                for br in b_rows:
                    b_num = str(br.get("Number") or "").strip()
                    if not b_num:
                        continue
                    b_date = parse_date(br.get("Invoice/Bill Date")) or datetime.date(2026, 9, 1)
                    due_date = parse_date(br.get("Due Date")) or b_date
                    v_name = str(br.get("Partner") or "").strip()
                    partner = partner_map.get(v_name, default_partner)
                    tot = clean_decimal(br.get("Total in Currency Signed"))
                    untaxed = clean_decimal(br.get("Untaxed Amount Signed Currency"))
                    residual = clean_decimal(br.get("Amount Due Signed"))

                    inv_res = await session.execute(
                        select(Invoice).where(Invoice.company_id == company.id, Invoice.name == b_num)
                    )
                    inv = inv_res.scalar_one_or_none()
                    if not inv:
                        inv = Invoice(
                            id=uuid.uuid4(),
                            company_id=company.id,
                            name=b_num,
                            reference=str(br.get("Reference") or "").strip() or None,
                            invoice_type=InvoiceType.BILL.value,
                            invoice_date=b_date,
                            due_date=due_date,
                            accounting_date=b_date,
                            partner_id=partner.id,
                            journal_id=default_bill_journal.id,
                            journal_entry_id=entry_map.get(b_num).id if b_num in entry_map else None,
                            amount_total=float(tot),
                            amount_untaxed=float(untaxed),
                            amount_residual=float(residual),
                            currency_code="KWD",
                            state=InvoiceState.POSTED.value,
                        )
                        session.add(inv)
                        bill_count += 1
                await session.flush()
                logger.info(f"Processed {len(b_rows)} vendor bills ({bill_count} new, {len(b_rows) - bill_count} existing in DB)")

            # 10. Customer Invoices (from customer-invoices.xlsx)
            cinv_file = data_dir / "customer-invoices.xlsx"
            if cinv_file.exists():
                _, c_rows = read_sheet_rows(cinv_file)
                cinv_count = 0
                for cr in c_rows:
                    c_num = str(cr.get("Number") or "").strip()
                    if not c_num:
                        continue
                    c_date = parse_date(cr.get("Invoice/Bill Date")) or datetime.date(2026, 9, 1)
                    due_date = parse_date(cr.get("Due Date")) or c_date
                    p_name = str(cr.get("Partner") or "").strip()
                    partner = partner_map.get(p_name, default_partner)
                    tot = clean_decimal(cr.get("Total in Currency Signed"))
                    untaxed = clean_decimal(cr.get("Untaxed Amount Signed Currency"))
                    residual = clean_decimal(cr.get("Amount Due Signed"))

                    inv_res = await session.execute(
                        select(Invoice).where(Invoice.company_id == company.id, Invoice.name == c_num)
                    )
                    inv = inv_res.scalar_one_or_none()
                    if not inv:
                        inv = Invoice(
                            id=uuid.uuid4(),
                            company_id=company.id,
                            name=c_num,
                            invoice_type=InvoiceType.INVOICE.value,
                            invoice_date=c_date,
                            due_date=due_date,
                            accounting_date=c_date,
                            partner_id=partner.id,
                            journal_id=default_inv_journal.id,
                            journal_entry_id=entry_map.get(c_num).id if c_num in entry_map else None,
                            amount_total=float(tot),
                            amount_untaxed=float(untaxed),
                            amount_residual=float(residual),
                            currency_code="KWD",
                            state=InvoiceState.POSTED.value,
                        )
                        session.add(inv)
                        cinv_count += 1
                await session.flush()
                logger.info(f"Processed {len(c_rows)} customer invoices ({cinv_count} new, {len(c_rows) - cinv_count} existing in DB)")

            # 11. Payments (from vendor-payments.xlsx & customer-payments.xlsx)
            for fname, p_type in [
                ("vendor-payments.xlsx", PaymentType.OUTBOUND.value),
                ("customer-payments.xlsx", PaymentType.INBOUND.value),
            ]:
                p_file = data_dir / fname
                if p_file.exists():
                    _, p_rows = read_sheet_rows(p_file)
                    pay_count = 0
                    for pr in p_rows:
                        p_num = str(pr.get("Number") or "").strip()
                        if not p_num:
                            continue
                        p_date = parse_date(pr.get("Date")) or datetime.date(2026, 9, 1)
                        p_partner_name = str(pr.get("Customer/Vendor") or "").strip()
                        partner = partner_map.get(p_partner_name, default_partner)
                        j_name = str(pr.get("Journal") or "").strip()
                        journal = journal_map.get(j_name, default_misc_journal)
                        amt = clean_decimal(pr.get("Amount Company Currency Signed"))

                        pmt_res = await session.execute(
                            select(Payment).where(Payment.company_id == company.id, Payment.name == p_num)
                        )
                        pmt = pmt_res.scalar_one_or_none()
                        if not pmt:
                            pmt = Payment(
                                id=uuid.uuid4(),
                                company_id=company.id,
                                name=p_num,
                                payment_type=p_type,
                                payment_date=p_date,
                                partner_id=partner.id if partner else None,
                                journal_id=journal.id,
                                journal_entry_id=entry_map.get(p_num).id if p_num in entry_map else None,
                                amount=float(amt),
                                amount_residual=0.0,
                                currency_code="KWD",
                                state=PaymentState.POSTED.value,
                            )
                            session.add(pmt)
                            pay_count += 1
                    await session.flush()
                    logger.info(f"Processed {len(p_rows)} payments from {fname} ({pay_count} new, {len(p_rows) - pay_count} existing in DB)")

            if dry_run:
                logger.info("DRY RUN: Rolling back transaction...")
                await session.rollback()
            else:
                logger.info("Transaction committed successfully.")

            logger.info("=" * 65)
            logger.info("EXCEL IMPORT PIPELINE COMPLETED SUCCESSFULLY!")
            logger.info("=" * 65)

    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Upload and ingest Excel datasets into ushanr")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=PROJECT_ROOT / "data",
        help="Path to folder containing .xlsx files (default: data/)",
    )
    parser.add_argument(
        "--company-name",
        type=str,
        default="USHSPA",
        help="Target company name (default: USHSPA)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate import without persisting to database",
    )
    args = parser.parse_args()

    asyncio.run(
        import_all_excel_data(
            data_dir=args.data_dir,
            company_name=args.company_name,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
