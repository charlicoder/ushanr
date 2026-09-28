# ushanr — SME Accounting & Reporting Microservice

Production-ready, double-entry bookkeeping microservice for SME businesses.
Built with **Python 3.12+**, **FastAPI**, **SQLAlchemy 2.x (async)**, **PostgreSQL 15+**, **Alembic**, and **Pydantic v2**.

---

## Features

| Feature | Status |
|---|---|
| Double-entry bookkeeping engine | ✅ |
| Chart of Accounts (hierarchical) | ✅ |
| Companies & multi-company | ✅ |
| Partners (customers / vendors) | ✅ |
| Journals (sale, purchase, bank, cash, general) | ✅ |
| Journal entries + posting + reversal | ✅ |
| Invoices, bills, credit notes | ✅ |
| Customer & vendor payments + allocation/reconciliation | ✅ |
| Bank accounts, statements & reconciliation | ✅ |
| Analytic accounting (plans + accounts) | ✅ |
| Fiscal years & accounting periods (open/close/lock) | ✅ |
| Configurable taxes (percentage / fixed) | ✅ |
| Budgets + variance analysis | ✅ |
| Document sequences | ✅ |
| Audit trail (immutable) | ✅ |
| Reports: GL, Trial Balance, P&L, Balance Sheet, AR/AP Aging, Cash Flow | ✅ |
| KWD default currency (3 decimal places) | ✅ |
| REST API at `/api/v1/`, `/uanr/api/v1/`, and `/anr/api/v1/` | ✅ |

---

## Architecture

```
ushanr/
├── app/
│   ├── api/v1/
│   │   ├── endpoints/       # Thin FastAPI route handlers
│   │   └── router.py        # Main API router
│   ├── core/
│   │   ├── config.py        # Settings (pydantic-settings)
│   │   ├── database.py      # Async SQLAlchemy engine + session
│   │   ├── exceptions.py    # Domain exception hierarchy
│   │   ├── logging.py       # structlog configuration
│   │   └── security.py      # JWT auth utilities
│   ├── models/              # SQLAlchemy 2.x ORM models
│   ├── schemas/             # Pydantic v2 request/response schemas
│   ├── repositories/        # Generic async repository pattern
│   ├── services/
│   │   ├── double_entry.py  # ⭐ Core double-entry engine
│   │   ├── account_service.py
│   │   ├── invoice_service.py
│   │   ├── payment_service.py
│   │   └── report_service.py
│   └── main.py              # FastAPI app factory
├── migrations/              # Alembic migrations
├── tests/
│   ├── unit/
│   └── api/
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── pyproject.toml
```

### Key Accounting Rules

> **The journal ledger is the financial source of truth.**
> All reports are computed directly from posted `journal_items`.

1. **Every entry must balance** — `sum(debit) == sum(credit)` or `UnbalancedEntryError` is raised.
2. **Posted entries are immutable** — use `reverse_entry()` for corrections.
3. **All monetary math uses `Decimal`** — never Python `float`.
4. **Fiscal period enforcement** — if a period is `locked` or `closed`, posting is blocked.

---

## Quick Start

### 1. Copy environment file

```bash
cp .env.example .env
# Edit DATABASE_URL and SECRET_KEY
```

### 2. Run with Docker Compose

```bash
docker compose up --build
```

Service available at: http://localhost:8007

### 3. Run database migrations

```bash
docker exec -it ushanr alembic upgrade head
```

### 4. Access API docs (development only)

- Swagger UI: http://localhost:8007/api/docs/
- ReDoc: http://localhost:8007/api/redoc/

---

## API Reference

All routes available at `/api/v1/`, `/uanr/api/v1/`, and `/anr/api/v1/`.

### Health
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/health/` | Health check |

### Master Data
| Method | Path | Description |
|---|---|---|
| `GET/POST` | `/api/v1/companies/` | List / create companies |
| `GET/PUT` | `/api/v1/companies/{id}/` | Get / update company |
| `GET/POST` | `/api/v1/partners/` | List / create partners |
| `GET/PUT/DELETE` | `/api/v1/partners/{id}/` | Get / update / delete partner |
| `GET/POST` | `/api/v1/accounts/` | List / create accounts |
| `GET/PUT/DELETE` | `/api/v1/accounts/{id}/` | Get / update / delete account |
| `GET` | `/api/v1/accounts/{id}/balance/` | Account balance (date range) |
| `GET/POST` | `/api/v1/journals/` | List / create journals |
| `GET/PUT` | `/api/v1/journals/{id}/` | Get / update journal |
| `GET/POST` | `/api/v1/taxes/` | List / create taxes |
| `GET/POST` | `/api/v1/fiscal/years/` | Fiscal year management |
| `POST` | `/api/v1/fiscal/years/{id}/close/` | Close fiscal year |
| `GET/POST` | `/api/v1/fiscal/periods/` | Accounting periods |
| `PATCH` | `/api/v1/fiscal/periods/{id}/state/` | Lock / close period |

### Transactions
| Method | Path | Description |
|---|---|---|
| `GET/POST` | `/api/v1/journal-entries/` | List / create draft entries |
| `GET` | `/api/v1/journal-entries/{id}/` | Get entry with items |
| `POST` | `/api/v1/journal-entries/{id}/post/` | Post entry |
| `POST` | `/api/v1/journal-entries/{id}/reverse/` | Create reversal |
| `DELETE` | `/api/v1/journal-entries/{id}/` | Cancel draft |
| `GET/POST` | `/api/v1/invoices/` | List / create invoices |
| `GET/PUT` | `/api/v1/invoices/{id}/` | Get / update invoice |
| `POST` | `/api/v1/invoices/{id}/post/` | Post invoice → journal entry |
| `POST` | `/api/v1/invoices/{id}/cancel/` | Cancel invoice |
| `POST` | `/api/v1/invoices/{id}/credit-note/` | Create credit note |
| `GET/POST` | `/api/v1/payments/` | List / create payments |
| `POST` | `/api/v1/payments/{id}/post/` | Post payment → journal entry |
| `POST` | `/api/v1/payments/{id}/allocate/` | Allocate to invoice |
| `POST` | `/api/v1/payments/{id}/cancel/` | Cancel payment |

### Banking
| Method | Path | Description |
|---|---|---|
| `GET/POST` | `/api/v1/bank/accounts/` | Bank accounts |
| `GET/POST` | `/api/v1/bank/statements/` | Bank statements |
| `POST` | `/api/v1/bank/statements/{id}/lines/` | Add statement line |
| `POST` | `/api/v1/bank/statements/{id}/reconcile/` | Reconcile line |

### Reports
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/reports/general-ledger/` | General Ledger |
| `GET` | `/api/v1/reports/trial-balance/` | Trial Balance |
| `GET` | `/api/v1/reports/profit-loss/` | Profit & Loss |
| `GET` | `/api/v1/reports/balance-sheet/` | Balance Sheet |
| `GET` | `/api/v1/reports/ar-aging/` | AR Aging |
| `GET` | `/api/v1/reports/ap-aging/` | AP Aging |
| `GET` | `/api/v1/reports/cash-flow/` | Cash Flow |

---

## Response Format

All endpoints return a consistent envelope:

```json
{
  "success": true,
  "data": { ... }
}
```

Errors:
```json
{
  "success": false,
  "error": {
    "code": "UNBALANCED_ENTRY",
    "message": "Journal entry does not balance: debits 100.000 ≠ credits 90.000 (diff: 10.000)",
    "detail": null
  }
}
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | — | PostgreSQL async DSN |
| `SECRET_KEY` | — | JWT signing key |
| `APP_ENV` | `development` | `development` / `production` |
| `LOG_LEVEL` | `INFO` | Log level |
| `USHANR_BASE_PATH` | `/anr` | URL prefix for reverse proxy |
| `PORT` | `8007` | Server port |
| `DEFAULT_CURRENCY` | `KWD` | Default currency code |

---

## Running Tests

```bash
# Unit tests (no DB required)
python -m pytest tests/unit/ -v

# All tests (requires DB)
python -m pytest -v
```

---

## Integration with other microservices

- **ushbooknpay** (port 8003) — source of orders/bookings; use `source_document_type="order"` when creating invoices
- **ushauth** (port 8001/8002) — user identity; use `Partner.external_id` to link partner ↔ user

---

## Port

`8007` — microservice port:
- `ushauth`: 8002
- `ushbooknpay`: 8003
- `ushanr`: **8007**
