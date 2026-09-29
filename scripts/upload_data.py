"""
scripts/upload_data.py
───────────────────────
Comprehensive import script for ushanr SME accounting microservice.

Imports:
1. Company & Currency (USHSPA, KWD)
2. Chart of Accounts (standard + auto-discovered from journal items)
3. Journals (Purchases, Sales, Bank accounts, Cash accounts, Operations)
4. Partners (Vendors, Customers, Internal accounts)
5. Invoices & Vendor Bills (with InvoiceLines and reversal links)
6. Journal Entries (all document vouchers)
7. Journal Items (General Ledger double-entry lines with debit/credit balance)
8. Payments (Bank / cash receipts and disbursements)
9. Analytic Accounting (Branches plan: Sharq & Mangaf cost centers)

Usage:
    python scripts/upload_data.py
    python scripts/upload_data.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import re
import sys
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.models.account import Account, AccountNature, AccountType
from app.models.analytic import AnalyticAccount, AnalyticItem, AnalyticPlan
from app.models.company import Company
from app.models.currency import Currency
from app.models.invoice import Invoice, InvoiceLine, InvoiceState, InvoiceType
from app.models.journal import Journal, JournalType
from app.models.journal_entry import EntryState, JournalEntry, JournalItem
from app.models.partner import Partner, PartnerType
from app.models.payment import Payment, PaymentMethod, PaymentState, PaymentType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("upload_data")

ZERO = Decimal("0")


def clean_decimal(val: str | None) -> Decimal:
    """Parse string representation of currency amount into Decimal."""
    if not val:
        return ZERO
    cleaned = str(val).replace(",", "").strip()
    cleaned = cleaned.replace('"', "").replace("'", "")
    try:
        return abs(Decimal(cleaned))
    except Exception:
        return ZERO


def clean_decimal_signed(val: str | None) -> Decimal:
    """Parse string representation of currency amount preserving sign."""
    if not val:
        return ZERO
    cleaned = str(val).replace(",", "").strip()
    cleaned = cleaned.replace('"', "").replace("'", "")
    try:
        return Decimal(cleaned)
    except Exception:
        return ZERO


def parse_date(date_str: str | None) -> date | None:
    """Parse YYYY-MM-DD or return None."""
    if not date_str or not date_str.strip():
        return None
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


COUNTRY_MAP = {
    "kuwait": "KW",
    "united kingdom": "GB",
    "uk": "GB",
    "saudi arabia": "SA",
    "uae": "AE",
    "united arab emirates": "AE",
    "egypt": "EG",
    "usa": "US",
    "united states": "US",
}


def map_country(country_name: str | None) -> str | None:
    if not country_name:
        return None
    key = country_name.strip().lower()
    return COUNTRY_MAP.get(key, key[:2].upper() if len(key) >= 2 else None)


def determine_account_type_and_nature(code: str, name: str) -> tuple[str, str, bool, bool]:
    """
    Determine (account_type, account_nature, is_reconcilable, is_bank_account)
    from standard accounting numbering.
    """
    first_char = code[0] if code else "5"
    if first_char == "1":
        # Asset
        is_bank = code.startswith("10000") or code.startswith("10005")
        is_reconcilable = code.startswith("1002")  # AR
        return AccountType.ASSET.value, AccountNature.DEBIT.value, is_reconcilable, is_bank
    elif first_char == "2":
        # Liability
        is_reconcilable = code.startswith("2001")  # AP
        return AccountType.LIABILITY.value, AccountNature.CREDIT.value, is_reconcilable, False
    elif first_char == "3":
        # Equity
        return AccountType.EQUITY.value, AccountNature.CREDIT.value, False, False
    elif first_char == "4":
        # Revenue / Income
        return AccountType.REVENUE.value, AccountNature.CREDIT.value, False, False
    else:
        # 5 or default: Expense
        return AccountType.EXPENSE.value, AccountNature.DEBIT.value, False, False


async def get_or_create_company(session: AsyncSession, name: str = "USHSPA") -> Company:
    res = await session.execute(select(Company).where(Company.name == name))
    company = res.scalar_one_or_none()
    if not company:
        company = Company(
            name=name,
            legal_name="USHSPA General Trading & Services Co.",
            trade_name="USHSPA",
            currency_code="KWD",
            country_code="KW",
            fiscal_year_start_month=1,
            decimal_places=3,
        )
        session.add(company)
        await session.flush()
        logger.info(f"Created company: {company.name}")
    return company


async def get_or_create_currency(session: AsyncSession, code: str = "KWD") -> Currency:
    res = await session.execute(select(Currency).where(Currency.code == code))
    curr = res.scalar_one_or_none()
    if not curr:
        curr = Currency(
            code=code,
            name="Kuwaiti Dinar",
            symbol="KD",
            decimal_places=3,
            is_active=True,
            is_base=True,
        )
        session.add(curr)
        await session.flush()
        logger.info(f"Created base currency: {code}")
    return curr


async def import_accounts(
    session: AsyncSession, company_id: uuid.UUID, journal_items_path: Path
) -> dict[str, Account]:
    """Ensure standard and auto-discovered accounts exist."""
    accounts: dict[str, Account] = {}

    # 1. Base standard accounts
    standard_spec = [
        ("2100", "Accounts Payable (Vendors)", AccountType.LIABILITY.value, AccountNature.CREDIT.value, True, False),
        ("1200", "Accounts Receivable (Customers)", AccountType.ASSET.value, AccountNature.DEBIT.value, True, False),
        ("5000", "Operating Expenses", AccountType.EXPENSE.value, AccountNature.DEBIT.value, False, False),
        ("1010", "Bank", AccountType.ASSET.value, AccountNature.DEBIT.value, False, True),
    ]
    for code, name, acct_type, nature, reconcilable, is_bank in standard_spec:
        res = await session.execute(
            select(Account).where(Account.company_id == company_id, Account.code == code)
        )
        acct = res.scalar_one_or_none()
        if not acct:
            acct = Account(
                company_id=company_id,
                code=code,
                name=name,
                account_type=acct_type,
                account_nature=nature,
                is_reconcilable=reconcilable,
                is_bank_account=is_bank,
                currency_code="KWD",
            )
            session.add(acct)
            await session.flush()
        accounts[code] = acct

    # 2. Extract accounts from journal_items.csv
    if journal_items_path.exists():
        with open(journal_items_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                acct_str = (row.get("Account") or "").strip()
                if not acct_str:
                    continue
                parts = acct_str.split(" ", 1)
                code = parts[0].strip()
                name = parts[1].strip() if len(parts) > 1 else code
                if code not in accounts:
                    res = await session.execute(
                        select(Account).where(Account.company_id == company_id, Account.code == code)
                    )
                    acct = res.scalar_one_or_none()
                    if not acct:
                        acct_type, nature, rec, is_bank = determine_account_type_and_nature(code, name)
                        acct = Account(
                            company_id=company_id,
                            code=code,
                            name=name,
                            account_type=acct_type,
                            account_nature=nature,
                            is_reconcilable=rec,
                            is_bank_account=is_bank,
                            currency_code="KWD",
                        )
                        session.add(acct)
                        await session.flush()
                    accounts[code] = acct
                    accounts[acct_str] = acct

    logger.info(f"Accounts provisioned: {len(accounts)} accounts ready")
    return accounts


async def import_journals(
    session: AsyncSession, company_id: uuid.UUID, accounts: dict[str, Account]
) -> dict[str, Journal]:
    """Ensure standard and specific journals exist."""
    def_exp = accounts.get("5000") or list(accounts.values())[0]
    def_ap = accounts.get("2100") or list(accounts.values())[0]

    journals_spec = [
        ("BILL", "Vendor Bills", JournalType.PURCHASE.value),
        ("Purchases", "Purchases", JournalType.PURCHASE.value),
        ("Sales", "Sales", JournalType.SALE.value),
        ("BNK", "Bank", JournalType.BANK.value),
        ("Bank-8001", "Bank-8001", JournalType.BANK.value),
        ("Bank-8002", "Bank-8002", JournalType.BANK.value),
        ("Bank-8003", "Bank-8003", JournalType.BANK.value),
        ("Bank-8004", "Bank-8004", JournalType.BANK.value),
        ("Cash-Ali", "Cash-Ali", JournalType.CASH.value),
        ("Cash-Deema", "Cash-Deema", JournalType.CASH.value),
        ("Cash-Fahad", "Cash-Fahad", JournalType.CASH.value),
        ("Cash-Sara", "Cash-Sara", JournalType.CASH.value),
        ("Miscellaneous Operations", "Miscellaneous Operations", JournalType.GENERAL.value),
        ("Interncompany Transfers", "Interncompany Transfers", JournalType.GENERAL.value),
    ]

    journals: dict[str, Journal] = {}
    for code, name, jtype in journals_spec:
        res = await session.execute(
            select(Journal).where(Journal.company_id == company_id, Journal.name == name)
        )
        j = res.scalar_one_or_none()
        if not j:
            j = Journal(
                company_id=company_id,
                code=code[:10],
                name=name,
                journal_type=jtype,
                default_account_id=def_exp.id,
                payment_credit_account_id=def_ap.id,
                sequence_prefix=code[:10],
                currency_code="KWD",
            )
            session.add(j)
            await session.flush()
        journals[name] = j
        journals[code] = j

    logger.info(f"Journals provisioned: {len(journals_spec)} journals ready")
    return journals


async def import_partners(
    session: AsyncSession, company_id: uuid.UUID, partners_path: Path, ap_id: uuid.UUID, ar_id: uuid.UUID
) -> dict[str, Partner]:
    """Import partners from partners.csv and index by name."""
    partner_map: dict[str, Partner] = {}
    if not partners_path.exists():
        logger.warning(f"Partners file {partners_path} not found.")
        return partner_map

    with open(partners_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            display_name = (row.get("Display Name") or row.get("name") or "").strip()
            if not display_name:
                continue

            email = (row.get("Email") or "").strip() or None
            phone = (row.get("Phone") or "").strip() or None
            country_code = map_country(row.get("Country"))
            is_internal = (
                display_name.endswith("JV")
                or "Transfer" in display_name
                or "Bank Fees" in display_name
                or "Suspense" in display_name
            )
            ptype = PartnerType.INTERNAL.value if is_internal else PartnerType.VENDOR.value

            res = await session.execute(
                select(Partner).where(
                    Partner.company_id == company_id,
                    Partner.name == display_name,
                    Partner.is_deleted == False,
                )
            )
            partner = res.scalar_one_or_none()
            if not partner:
                partner = Partner(
                    company_id=company_id,
                    name=display_name,
                    display_name=display_name,
                    partner_type=ptype,
                    is_vendor=True,
                    is_customer=False,
                    is_company=not is_internal,
                    email=email,
                    phone=phone,
                    country_code=country_code,
                    currency_code="KWD",
                    payable_account_id=ap_id,
                    receivable_account_id=ar_id,
                )
                session.add(partner)
                await session.flush()
            partner_map[display_name] = partner
            partner_map[display_name.lower()] = partner

    logger.info(f"Partners ready: {len(partner_map)//2} unique partners")
    return partner_map


async def import_analytic_data(
    session: AsyncSession, company_id: uuid.UUID, csv_path: Path
) -> None:
    """Import cost center analytic items (Sharq, Mangaf)."""
    if not csv_path.exists():
        logger.warning(f"Analytic items file {csv_path} not found.")
        return

    # 1. Plan
    plan_res = await session.execute(
        select(AnalyticPlan).where(AnalyticPlan.company_id == company_id, AnalyticPlan.name == "Branches")
    )
    plan = plan_res.scalar_one_or_none()
    if not plan:
        plan = AnalyticPlan(
            company_id=company_id,
            name="Branches",
            code="BRANCH",
            description="Branch Location Cost Centers",
            default_applicability=100.0,
        )
        session.add(plan)
        await session.flush()

    # 2. Accounts
    accounts: dict[str, AnalyticAccount] = {}
    for code, name in [("SHARQ", "Sharq"), ("MANGAF", "Mangaf")]:
        acc_res = await session.execute(
            select(AnalyticAccount).where(
                AnalyticAccount.company_id == company_id,
                AnalyticAccount.plan_id == plan.id,
                AnalyticAccount.name == name,
            )
        )
        acc = acc_res.scalar_one_or_none()
        if not acc:
            acc = AnalyticAccount(
                company_id=company_id,
                plan_id=plan.id,
                code=code,
                name=name,
            )
            session.add(acc)
            await session.flush()
        accounts[name] = acc
        accounts[name.lower()] = acc

    # 3. Items
    count = 0
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            desc = (row.get("Description") or "").strip()
            branch = (row.get("Project Plan") or "").strip()
            amt = clean_decimal_signed(row.get("Amount"))
            dt = parse_date(row.get("Date"))

            acc = accounts.get(branch) or accounts.get(branch.lower())
            if not acc:
                continue

            item = AnalyticItem(
                company_id=company_id,
                analytic_account_id=acc.id,
                name=desc,
                date=dt,
                amount=float(amt),
                percentage=100.0,
            )
            session.add(item)
            count += 1

    await session.flush()
    logger.info(f"Analytic items imported: {count} entries allocated across Sharq & Mangaf")


async def import_journal_entries_and_items(
    session: AsyncSession,
    company_id: uuid.UUID,
    entries_path: Path,
    items_path: Path,
    journals: dict[str, Journal],
    accounts: dict[str, Account],
    partner_map: dict[str, Partner],
) -> tuple[int, int]:
    """Import full double-entry ledger vouchers and line items."""
    entries_by_num: dict[str, JournalEntry] = {}
    default_journal = journals.get("Miscellaneous Operations") or list(journals.values())[0]

    # Load existing entries
    exist_res = await session.execute(
        select(JournalEntry).where(JournalEntry.company_id == company_id)
    )
    for je in exist_res.scalars().all():
        if je.name:
            entries_by_num[je.name] = je

    # 1. Create Journal Entries from journal_entries.csv
    entries_count = 0
    if entries_path.exists():
        with open(entries_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                num = (row.get("Number") or "").strip()
                if not num:
                    continue
                dt = parse_date(row.get("Date")) or date.today()
                jname = (row.get("Journal") or "").strip()
                pname = (row.get("Partner") or "").strip()
                ref = (row.get("Reference") or "").strip() or None
                total = clean_decimal(row.get("Total Signed"))
                status = (row.get("Status") or "Posted").strip().lower()
                state = EntryState.POSTED.value if status == "posted" else EntryState.DRAFT.value

                journal = journals.get(jname) or default_journal
                partner = partner_map.get(pname) or partner_map.get(pname.lower())

                entry = entries_by_num.get(num)
                if not entry:
                    entry = JournalEntry(
                        company_id=company_id,
                        name=num,
                        reference=ref,
                        journal_id=journal.id,
                        partner_id=partner.id if partner else None,
                        entry_date=dt,
                        accounting_date=dt,
                        state=state,
                        amount_total=float(total),
                        currency_code="KWD",
                    )
                    session.add(entry)
                    entries_by_num[num] = entry
                    entries_count += 1

        await session.flush()

    # 2. Create Journal Items from journal_items.csv
    items_count = 0
    if items_path.exists():
        # Check existing items count to prevent duplicating
        exist_items = await session.execute(
            select(func.count(JournalItem.id)).where(JournalItem.company_id == company_id)
        )
        if exist_items.scalar_one() > 0:
            logger.info("Journal items already exist in database; updating existing.")
        else:
            with open(items_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    num = (row.get("Number") or "").strip()
                    dt = parse_date(row.get("Date")) or date.today()
                    acct_str = (row.get("Account") or "").strip()
                    pname = (row.get("Partner") or "").strip()
                    label = (row.get("Label") or "").strip() or None
                    dr = clean_decimal(row.get("Debit"))
                    cr = clean_decimal(row.get("Credit"))
                    match_id = (row.get("Matching #") or "").strip()

                    code = acct_str.split(" ", 1)[0].strip()
                    account = accounts.get(code) or accounts.get(acct_str)
                    if not account:
                        continue

                    entry = entries_by_num.get(num)
                    if not entry:
                        entry = JournalEntry(
                            company_id=company_id,
                            name=num,
                            reference=label,
                            journal_id=default_journal.id,
                            entry_date=dt,
                            accounting_date=dt,
                            state=EntryState.POSTED.value,
                            currency_code="KWD",
                        )
                        session.add(entry)
                        await session.flush()
                        entries_by_num[num] = entry
                        entries_count += 1

                    partner = partner_map.get(pname) or partner_map.get(pname.lower())
                    ji = JournalItem(
                        company_id=company_id,
                        entry_id=entry.id,
                        account_id=account.id,
                        partner_id=partner.id if partner else None,
                        name=label,
                        reference=match_id or None,
                        debit_amount=float(dr),
                        credit_amount=float(cr),
                        reconciled=bool(match_id),
                        date=dt,
                        currency_code="KWD",
                    )
                    session.add(ji)
                    items_count += 1

            await session.flush()

    logger.info(f"Journal vouchers: {entries_count} created/loaded, {items_count} items created")
    return entries_count, items_count


async def import_payments(
    session: AsyncSession,
    company_id: uuid.UUID,
    csv_path: Path,
    journals: dict[str, Journal],
    partner_map: dict[str, Partner],
) -> int:
    """Import customer/vendor payments from payments.csv."""
    if not csv_path.exists():
        logger.warning(f"Payments file {csv_path} not found.")
        return 0

    count = 0
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            num = (row.get("Number") or "").strip()
            if not num:
                continue

            dt = parse_date(row.get("Date")) or date.today()
            jname = (row.get("Journal") or "").strip()
            pname = (row.get("Customer/Vendor") or "").strip()
            amt = clean_decimal(row.get("Amount Company Currency Signed"))
            raw_state = (row.get("State") or "Draft").strip().lower()

            journal = journals.get(jname) or journals.get("Bank-8001") or list(journals.values())[0]
            partner = partner_map.get(pname) or partner_map.get(pname.lower())

            if raw_state in ("paid", "posted"):
                pstate = PaymentState.POSTED.value
            elif raw_state == "reconciled":
                pstate = PaymentState.RECONCILED.value
            elif raw_state in ("canceled", "cancelled"):
                pstate = PaymentState.CANCELLED.value
            else:
                pstate = PaymentState.DRAFT.value

            res = await session.execute(
                select(Payment).where(Payment.company_id == company_id, Payment.name == num)
            )
            pmt = res.scalar_one_or_none()
            if not pmt:
                pmt = Payment(
                    company_id=company_id,
                    name=num,
                    reference=f"Payment via {jname}",
                    payment_type=PaymentType.INBOUND.value,
                    payment_method=PaymentMethod.BANK.value,
                    state=pstate,
                    journal_id=journal.id,
                    partner_id=partner.id if partner else None,
                    partner_name=pname or None,
                    payment_date=dt,
                    amount=float(amt),
                    amount_residual=0.0 if pstate in (PaymentState.POSTED.value, PaymentState.RECONCILED.value) else float(amt),
                    currency_code="KWD",
                )
                session.add(pmt)
                count += 1

    await session.flush()
    logger.info(f"Payments imported: {count} payments created in anr_payments")
    return count


async def import_bills(
    session: AsyncSession,
    company_id: uuid.UUID,
    bills_path: Path,
    journals: dict[str, Journal],
    partner_map: dict[str, Partner],
) -> int:
    """Import vendor bills and credit notes from bills.csv."""
    if not bills_path.exists():
        logger.warning(f"Bills file {bills_path} not found.")
        return 0

    purchase_journal = journals.get("Purchases") or journals.get("BILL") or list(journals.values())[0]

    count = 0
    with open(bills_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            num = (row.get("Number") or "").strip()
            if not num:
                continue

            res = await session.execute(
                select(Invoice).where(Invoice.company_id == company_id, Invoice.name == num)
            )
            if res.scalar_one_or_none():
                continue

            pname = (row.get("Partner") or "").strip()
            inv_date = parse_date(row.get("Invoice/Bill Date")) or date.today()
            due_date = parse_date(row.get("Due Date")) or inv_date
            ref = (row.get("Reference") or "").strip() or None
            untaxed_amt = clean_decimal(row.get("Untaxed Amount Signed Currency"))
            total_amt = clean_decimal(row.get("Total in Currency Signed"))
            due_amt = clean_decimal(row.get("Amount Due Signed"))
            status = (row.get("Status In Payment") or "").strip().lower()

            is_vendor_credit = num.startswith("RBILL")
            inv_type = InvoiceType.VENDOR_CREDIT.value if is_vendor_credit else InvoiceType.BILL.value
            if status == "paid":
                inv_state = InvoiceState.PAID.value
            elif status in ("posted", "in payment"):
                inv_state = InvoiceState.POSTED.value
            else:
                inv_state = InvoiceState.DRAFT.value

            partner = partner_map.get(pname) or partner_map.get(pname.lower())
            if not partner and pname:
                partner = Partner(
                    company_id=company_id,
                    name=pname,
                    display_name=pname,
                    partner_type=PartnerType.VENDOR.value,
                    is_vendor=True,
                    is_customer=False,
                    is_company=True,
                    currency_code="KWD",
                )
                session.add(partner)
                await session.flush()
                partner_map[pname] = partner
                partner_map[pname.lower()] = partner

            if not partner:
                continue

            paid_amt = total_amt - due_amt if total_amt >= due_amt else ZERO
            tax_amt = total_amt - untaxed_amt if total_amt >= untaxed_amt else ZERO

            invoice = Invoice(
                company_id=company_id,
                name=num,
                reference=ref,
                invoice_type=inv_type,
                state=inv_state,
                partner_id=partner.id,
                journal_id=purchase_journal.id,
                invoice_date=inv_date,
                due_date=due_date,
                accounting_date=inv_date,
                currency_code="KWD",
                amount_untaxed=float(untaxed_amt),
                amount_tax=float(tax_amt),
                amount_total=float(total_amt),
                amount_paid=float(paid_amt),
                amount_residual=float(due_amt),
                is_reversal=is_vendor_credit,
            )
            session.add(invoice)
            count += 1

    await session.flush()
    logger.info(f"Vendor bills imported: {count} bills created in invoices")
    return count


async def link_invoices_to_journal_entries(
    session: AsyncSession, company_id: uuid.UUID
) -> int:
    """Link Invoices to their posted JournalEntry by matching document name."""
    inv_res = await session.execute(
        select(Invoice).where(
            Invoice.company_id == company_id,
            Invoice.journal_entry_id == None,
            Invoice.name != None,
        )
    )
    invoices = inv_res.scalars().all()
    linked = 0
    for inv in invoices:
        je_res = await session.execute(
            select(JournalEntry).where(
                JournalEntry.company_id == company_id,
                JournalEntry.name == inv.name,
            )
        )
        je = je_res.scalar_one_or_none()
        if je:
            inv.journal_entry_id = je.id
            session.add(inv)
            linked += 1

    await session.flush()
    logger.info(f"Linked {linked} invoices to their corresponding general ledger journal entries")
    return linked


async def run_import(data_dir: Path, company_name: str = "USHSPA", dry_run: bool = False) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.async_database_url, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            logger.info("=" * 65)
            logger.info(f"STARTING COMPREHENSIVE IMPORT FOR: {company_name}")
            logger.info(f"Mode: {'DRY RUN' if dry_run else 'COMMITTING TO DATABASE'}")
            logger.info("=" * 65)

            # 1. Company & Currency
            company = await get_or_create_company(session, name=company_name)
            await get_or_create_currency(session, "KWD")

            # 2. Accounts (60+ standard and discovered accounts)
            accounts = await import_accounts(session, company.id, data_dir / "journal_items.csv")

            # 3. Journals (12 bank, cash, purchase, sale journals)
            journals = await import_journals(session, company.id, accounts)

            # 4. Partners
            partner_map = await import_partners(
                session, company.id, data_dir / "partners.csv",
                ap_id=accounts.get("2100", list(accounts.values())[0]).id,
                ar_id=accounts.get("1200", list(accounts.values())[0]).id,
            )

            # 5. Analytic Items (Branches: Sharq & Mangaf)
            await import_analytic_data(session, company.id, data_dir / "analytic_items.csv")

            # 6. Journal Entries & Journal Items (Double-entry source of truth)
            await import_journal_entries_and_items(
                session=session,
                company_id=company.id,
                entries_path=data_dir / "journal_entries.csv",
                items_path=data_dir / "journal_items.csv",
                journals=journals,
                accounts=accounts,
                partner_map=partner_map,
            )

            # 7. Payments
            await import_payments(session, company.id, data_dir / "payments.csv", journals, partner_map)

            # 8. Vendor Bills & Credit Notes
            await import_bills(session, company.id, data_dir / "bills.csv", journals, partner_map)

            # 9. Link Invoices to Journal Entries
            await link_invoices_to_journal_entries(session, company.id)

            if dry_run:
                logger.info("DRY RUN: Rolling back transaction...")
                await session.rollback()
            else:
                logger.info("Transaction committed successfully.")

            logger.info("=" * 65)
            logger.info("ALL ACCOUNTING DATA IMPORTED SUCCESSFULLY!")
            logger.info("=" * 65)

    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Upload accounting datasets into ushanr")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=PROJECT_ROOT / "data",
        help="Path to folder containing CSV files (default: data/)",
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
        run_import(
            data_dir=args.data_dir,
            company_name=args.company_name,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
