"""
app/api/v1/endpoints/accounts.py
──────────────────────────────────
Chart of Accounts REST endpoints.

GET    /api/v1/accounts/           — list accounts (filterable)
POST   /api/v1/accounts/           — create account
GET    /api/v1/accounts/{id}/      — get account detail
PUT    /api/v1/accounts/{id}/      — update account
DELETE /api/v1/accounts/{id}/      — soft-delete account
GET    /api/v1/accounts/{id}/balance/ — get account balance
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_optional_company_id
from app.core.database import get_db
from app.core.exceptions import AccountNotFoundError, ANRBaseError
from app.core.logging import get_logger
from app.models.account import Account, AccountType
from app.models.journal_entry import EntryState
from sqlalchemy import func, select
from app.models.journal_entry import JournalItem

logger = get_logger(__name__)
router = APIRouter()


def _account_to_dict(account: Account) -> dict:
    return {
        "id": str(account.id),
        "company_id": str(account.company_id),
        "code": account.code,
        "name": account.name,
        "account_type": account.account_type,
        "account_nature": account.account_nature,
        "parent_id": str(account.parent_id) if account.parent_id else None,
        "group_id": str(account.group_id) if account.group_id else None,
        "is_reconcilable": account.is_reconcilable,
        "is_bank_account": account.is_bank_account,
        "currency_code": account.currency_code,
        "is_active": account.is_active,
        "deprecated": account.deprecated,
        "description": account.description,
        "sequence": account.sequence,
        "created_at": account.created_at.isoformat(),
        "updated_at": account.updated_at.isoformat(),
    }


@router.get("/", summary="List chart of accounts")
async def list_accounts(
    company_id: UUID | None = Depends(get_optional_company_id),
    account_type: str | None = Query(None),
    is_active: bool | None = Query(None),
    is_reconcilable: bool | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = select(Account).where(Account.is_deleted == False)
    if company_id is not None:
        q = q.where(Account.company_id == company_id)
    if account_type:
        q = q.where(Account.account_type == account_type)
    if is_active is not None:
        q = q.where(Account.is_active == is_active)
    if is_reconcilable is not None:
        q = q.where(Account.is_reconcilable == is_reconcilable)
    if search:
        q = q.where(
            (Account.code.ilike(f"%{search}%")) | (Account.name.ilike(f"%{search}%"))
        )

    # Count
    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    # Paginate
    q = q.order_by(Account.code).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()

    return {
        "success": True,
        "data": {
            "items": [_account_to_dict(a) for a in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        },
    }


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create account")
async def create_account(
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        # Determine account nature from type
        from app.models.account import ACCOUNT_NATURE_MAP
        acct_type = payload.get("account_type", "asset")
        nature = ACCOUNT_NATURE_MAP.get(acct_type, "debit")

        account = Account(
            company_id=UUID(str(payload["company_id"])),
            code=payload["code"],
            name=payload["name"],
            account_type=acct_type,
            account_nature=nature,
            parent_id=UUID(str(payload["parent_id"])) if payload.get("parent_id") else None,
            group_id=UUID(str(payload["group_id"])) if payload.get("group_id") else None,
            is_reconcilable=payload.get("is_reconcilable", False),
            is_bank_account=payload.get("is_bank_account", False),
            currency_code=payload.get("currency_code"),
            description=payload.get("description"),
            sequence=payload.get("sequence", 10),
        )
        db.add(account)
        await db.flush()
        await db.refresh(account)
        return {"success": True, "data": _account_to_dict(account)}
    except ANRBaseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/{account_id}/", summary="Get account")
async def get_account(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Account).where(Account.id == account_id, Account.is_deleted == False)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"success": True, "data": _account_to_dict(account)}


@router.put("/{account_id}/", summary="Update account")
async def update_account(
    account_id: UUID,
    payload: dict,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Account).where(Account.id == account_id, Account.is_deleted == False)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    updatable = ["name", "description", "is_active", "is_reconcilable", "deprecated", "sequence"]
    for field in updatable:
        if field in payload:
            setattr(account, field, payload[field])

    db.add(account)
    await db.flush()
    await db.refresh(account)
    return {"success": True, "data": _account_to_dict(account)}


@router.delete("/{account_id}/", status_code=status.HTTP_204_NO_CONTENT, summary="Delete account")
async def delete_account(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(Account).where(Account.id == account_id, Account.is_deleted == False)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    from datetime import datetime, timezone
    account.is_deleted = True
    account.deleted_at = datetime.now(timezone.utc)
    db.add(account)


@router.get("/{account_id}/balance/", summary="Get account balance")
async def get_account_balance(
    account_id: UUID,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from datetime import date as date_type
    from sqlalchemy import and_

    result = await db.execute(
        select(Account).where(Account.id == account_id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Query posted journal items
    from app.models.journal_entry import JournalEntry
    q = (
        select(
            func.coalesce(func.sum(JournalItem.debit_amount), 0).label("total_debit"),
            func.coalesce(func.sum(JournalItem.credit_amount), 0).label("total_credit"),
        )
        .join(JournalEntry, JournalItem.entry_id == JournalEntry.id)
        .where(
            JournalItem.account_id == account_id,
            JournalEntry.state == EntryState.POSTED.value,
        )
    )
    if date_from:
        q = q.where(JournalEntry.accounting_date >= date_from)
    if date_to:
        q = q.where(JournalEntry.accounting_date <= date_to)

    row = (await db.execute(q)).one()
    total_debit = Decimal(str(row.total_debit))
    total_credit = Decimal(str(row.total_credit))

    if account.is_debit_normal:
        balance = total_debit - total_credit
    else:
        balance = total_credit - total_debit

    return {
        "success": True,
        "data": {
            "account_id": str(account_id),
            "code": account.code,
            "name": account.name,
            "account_type": account.account_type,
            "debit_total": float(total_debit),
            "credit_total": float(total_credit),
            "balance": float(balance),
        },
    }
