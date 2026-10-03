"""
app/api/v1/router.py
─────────────────────
Main API v1 router — aggregates all endpoint routers.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    accounts,
    analytic,
    assets,
    bank,
    budgets,
    companies,
    fiscal,
    health,
    internal,
    invoices,
    journal_entries,
    journal_items,
    journals,
    partners,
    payments,
    reports,
    taxes,
)

api_router = APIRouter(prefix="/api/v1")

# Health
api_router.include_router(health.router, prefix="/health", tags=["Health"])

# Master data
api_router.include_router(companies.router, prefix="/companies", tags=["Companies"])
api_router.include_router(partners.router, prefix="/partners", tags=["Partners"])
api_router.include_router(accounts.router, prefix="/accounts", tags=["Accounts"])
api_router.include_router(journals.router, prefix="/journals", tags=["Journals"])
api_router.include_router(taxes.router, prefix="/taxes", tags=["Taxes"])
api_router.include_router(fiscal.router, prefix="/fiscal", tags=["Fiscal"])
api_router.include_router(analytic.router, prefix="/analytic", tags=["Analytic"])
api_router.include_router(assets.router, prefix="/assets", tags=["Assets"])

# Transactions
api_router.include_router(
    journal_entries.router, prefix="/journal-entries", tags=["Journal Entries"]
)
api_router.include_router(
    journal_items.router, prefix="/journal-items", tags=["Journal Items"]
)
api_router.include_router(invoices.router, prefix="/invoices", tags=["Invoices"])
api_router.include_router(payments.router, prefix="/payments", tags=["Payments"])

# Banking
api_router.include_router(bank.router, prefix="/bank", tags=["Banking"])

# Budgets
api_router.include_router(budgets.router, prefix="/budgets", tags=["Budgets"])

# Reports
api_router.include_router(reports.router, prefix="/reports", tags=["Reports"])

# Internal (service-to-service only — protected by X-Internal-Key)
api_router.include_router(internal.router, prefix="/internal", tags=["Internal"])
