### Database Schema Analysis: `ushanr`

`ushanr` is an enterprise, double-entry financial and accounting microservice built with **FastAPI**, **SQLAlchemy 2.x (async)**, and **PostgreSQL**. Its design follows standard accounting principles (similar to Odoo / ERPNext / SAP subledger architectures).

```
                     ┌──────────────────┐
                     │     Company      │
                     │  (Tenant/USHSPA) │
                     └─────────┬────────┘
                               │
       ┌───────────────────────┼───────────────────────────┐
       ▼                       ▼                           ▼
┌──────────────┐       ┌──────────────┐            ┌──────────────┐
│  Currencies  │       │ Fiscal Years │            │  Analytic    │
│  (Base: KWD) │       │  & Periods   │            │    Plans     │
└──────────────┘       └──────────────┘            └──────────────┘
       │                       │                           │
       ▼                       ▼                           ▼
┌──────────────┐       ┌──────────────┐            ┌──────────────┐
│ Chart of     │◄──────┤   Journals   ├───────────►│ Cost Centers │
│ Accounts     │       │(Sale/Purch/  │            │(Sharq/Mangaf)│
│ (Asset, Liab,│       │ Bank/Cash/GL)│            └──────────────┘
│ Rev, Exp)    │       └──────┬───────┘
└──────┬───────┘              │
       │                      ▼
       │               ┌──────────────┐
       │               │ Document     │
       │               │ Sequences    │
       │               └──────────────┘
       │                      │
       ├──────────────────────┴────────────────────────────┐
       ▼                                                   ▼
┌─────────────────────────────────┐       ┌─────────────────────────────────┐
│     Commercial Subledgers       │       │      General Ledger (GL)        │
│ ─────────────────────────────── │       │ ─────────────────────────────── │
│ • Partners (Customer / Vendor)  │       │ • JournalEntry (Voucher header) │
│ • Invoices & Bills (AR / AP)    │──────►│ • JournalItem  (Debit / Credit) │
│ • Payments & Allocations        │       │   Invariant: Σ Debit == Σ Credit│
└─────────────────────────────────┘       └─────────────────────────────────┘
```

---

### Core Data Models & Relational Architecture

#### 1. Tenant & Foundation
* **`Company` (`companies`)**: Top-level tenant. Stores fiscal settings, base currency (`KWD`), default tax ID, address, and decimal precision (`3` for Kuwaiti Dinar).
* **`Currency` & `CurrencyRate` (`currencies`, `currency_rates`)**: Supports multi-currency operations with a single base currency (`is_base=True`).

#### 2. Chart of Accounts (COA)
* **`Account` (`accounts`)**:
  * **`account_type`**: `asset`, `liability`, `equity`, `revenue`, `expense`, `cogs`, `other`.
  * **`account_nature`**: Normal balance (`debit` or `credit`).
  * **`is_reconcilable`**: **Mandatory for AR and AP accounts**. The `InvoiceService` searches for `account_type == "asset"` and `is_reconcilable=True` to book customer receivables, and `account_type == "liability"` with `is_reconcilable=True` for vendor payables.
  * **`is_bank_account`**: Marks cash/bank GL accounts.

#### 3. Books of Original Entry (Journals)
* **`Journal` (`journals`)**: Groups transactions into distinct books:
  * `sale`: Customer Invoices & Credit Notes
  * `purchase`: Vendor Bills & Refunds
  * `bank`: Electronic & Bank settlements (NBK, KNET)
  * `cash`: Cash drawers & petty cash tills
  * `general`: Manual adjustments, payroll, and opening balances
  * Links to `default_account_id`, `payment_debit_account_id`, and `payment_credit_account_id`.

#### 4. Fiscal Control & Immutability
* **`FiscalYear` & `AccountingPeriod` (`fiscal_years`, `accounting_periods`)**:
  * `DoubleEntryEngine.validate_period_open()` enforces that **transaction dates must fall within an open accounting period**. If no periods exist or if a period is locked, posting journal entries is blocked.
* **`DocumentSequence` (`document_sequences`)**:
  * Manages gapless, auto-incrementing document numbers (`INV/2026/00001`, `BILL/2026/00001`, `PAY/2026/00001`, etc.).

#### 5. Commercial Invoicing & Double-Entry Engine
* **`Invoice` & `InvoiceLine` (`invoices`, `invoice_lines`)**: Commercial documents in states `draft` → `confirmed` → `posted` → `paid`.
  * Posting an invoice creates a balanced `JournalEntry` in the General Ledger:
    * **Customer Invoice**: `Dr Accounts Receivable` / `Cr Revenue` + `Cr Tax Payable`
    * **Vendor Bill**: `Dr Expense` + `Dr Tax Receivable` / `Cr Accounts Payable`
* **`JournalEntry` & `JournalItem` (`journal_entries`, `journal_items`)**:
  * The financial source of truth.
  * Strict invariant: `total_debit == total_credit`. Posted entries are immutable.

#### 6. Auxiliary Modules
* **`Partner` (`partners`)**: Customers and vendors; tracks `receivable_account_id` and `payable_account_id`.
* **`Tax` & `TaxGroup` (`taxes`, `tax_groups`)**: Output/Input tax configurations.
* **`AnalyticPlan` & `AnalyticAccount` (`analytic_plans`, `analytic_accounts`)**: Dimensional cost centers (e.g., branches: Sharq, Mangaf; departments: Operations, Marketing).
* **`BankAccount` (`bank_accounts`)**: Links bank/cash accounts to their corresponding GL accounts and journals.

---

### Production Initialization Script

The production bootstrap script has been created at:
[`ushanr/scripts/init_production_data.py`](file:///Users/charlicoder/Documents/projects/live/ushspa_projects/microservices/ushanr/scripts/init_production_data.py)

#### What this script provisions:
1. **Currencies**: `KWD` (base, 3 decimals) + trade currencies (`USD`, `EUR`, `GBP`, `SAR`, `AED`).
2. **Company**: `USHSPA` with 3-decimal precision and Kuwait defaults.
3. **Chart of Accounts (30+ production accounts)**:
   - **Assets (100000s)**: Cash Main Till, Bank NBK, KNET/POS Clearing, Gateway Clearing, Accounts Receivable (`120000`, reconcilable), Inventory, Fixed Assets, Tax Input Receivable.
   - **Liabilities (200000s)**: Accounts Payable (`210000`, reconcilable), Tax Payable Output (`210500`), Accrued Salaries, Customer Advances/Gift Liabilities.
   - **Equity (300000s)**: Capital, Retained Earnings, Current Year P&L.
   - **Revenue (400000s)**: Spa Services Revenue (`401000`), Retail Sales Revenue (`402000`), Add-ons Revenue (`403000`), Gift Voucher Revenue (`404000`), Discounts Allowed.
   - **COGS (500000s)**: Retail COGS, Treatment Consumables & Supplies.
   - **Operating Expenses (600000s)**: Payroll, Rent, Utilities, Marketing, POS Terminal Fees, Maintenance, General Admin.
4. **Journals**:
   - `INV` (Sales / Customer Invoices)
   - `RINV` (Customer Credit Notes)
   - `BILL` (Purchases / Vendor Bills)
   - `RBILL` (Vendor Refunds)
   - `BNK-NBK` (Operating Bank Account)
   - `BNK-KNET` (POS & Electronic Settlements)
   - `CSH-MAIN` (Cash Register)
   - `MISC` (General Operations)
   - `OPEN` (Opening Balances)
5. **Document Sequences**: Pre-configures sequences for `INV`, `BILL`, `RINV`, `RBILL`, `PAY`, `VPAY`, `MISC`, `BNK`.
6. **Fiscal Year & Periods**: Current year (e.g., `2026`) and next year (`2027`), with **all 12 monthly periods per year initialized in `OPEN` state**.
7. **Taxes**: Zero VAT (0% Exempt, Kuwait standard) and 5% VAT.
8. **Analytic Branches**: `BR-SHARQ` (Sharq Branch), `BR-MANGAF` (Mangaf Branch), `HQ-ADMIN` (Headquarters).
9. **Bank Profiles**: `NBK Operating Account` and `Main Cash Till`.
10. **Default Partners**: "Walk-in Customer" and "General Cash Vendor" pre-linked to AR/AP accounts.

---

### Running the Script

#### In Docker (Recommended):
```bash
docker exec -it ushanr python scripts/init_production_data.py
```

#### Locally (with virtualenv):
```bash
cd ushanr
python scripts/init_production_data.py
```

#### Optional Flags:
* **Dry run (test without writing)**:
  ```bash
  python scripts/init_production_data.py --dry-run
  ```
* **Custom company or year**:
  ```bash
  python scripts/init_production_data.py --company-name "USHSPA" --year 2026
  ```

---

### Service Integration Output

Upon execution, the script prints the exact UUID values to configure in other services (such as [`ushnotice/.env`](file:///Users/charlicoder/Documents/projects/live/ushspa_projects/microservices/ushnotice/.env)):

```dotenv
USHANR_COMPANY_ID=<generated-company-uuid>
USHANR_AR_JOURNAL_ID=<generated-sales-journal-uuid>
USHANR_REVENUE_ACCOUNT_ID=<generated-spa-services-revenue-uuid>
USHANR_ADDON_ACCOUNT_ID=<generated-addon-revenue-uuid>
```