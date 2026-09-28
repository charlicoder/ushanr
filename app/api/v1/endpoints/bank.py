"""
app/api/v1/endpoints/bank.py — Banking endpoints
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.bank import BankAccount, BankStatement, BankStatementLine, BankStatementState

router = APIRouter()


def _bank_account_to_dict(b: BankAccount) -> dict:
    return {
        "id": str(b.id),
        "company_id": str(b.company_id),
        "account_id": str(b.account_id),
        "journal_id": str(b.journal_id) if b.journal_id else None,
        "name": b.name,
        "account_number": b.account_number,
        "bank_name": b.bank_name,
        "bank_code": b.bank_code,
        "currency_code": b.currency_code,
        "account_type": b.account_type,
        "is_active": b.is_active,
        "created_at": b.created_at.isoformat(),
    }


def _statement_to_dict(s: BankStatement, include_lines: bool = False) -> dict:
    d = {
        "id": str(s.id),
        "company_id": str(s.company_id),
        "bank_account_id": str(s.bank_account_id),
        "name": s.name,
        "date_from": s.date_from.isoformat(),
        "date_to": s.date_to.isoformat(),
        "balance_start": float(s.balance_start),
        "balance_end": float(s.balance_end),
        "currency_code": s.currency_code,
        "state": s.state,
        "created_at": s.created_at.isoformat(),
    }
    if include_lines:
        d["lines"] = [_line_to_dict(l) for l in (s.lines or [])]
    return d


def _line_to_dict(l: BankStatementLine) -> dict:
    return {
        "id": str(l.id),
        "statement_id": str(l.statement_id),
        "journal_entry_id": str(l.journal_entry_id) if l.journal_entry_id else None,
        "date": l.date.isoformat(),
        "name": l.name,
        "reference": l.reference,
        "amount": float(l.amount),
        "currency_code": l.currency_code,
        "is_reconciled": l.is_reconciled,
    }


@router.get("/accounts/", summary="List bank accounts")
async def list_bank_accounts(
    company_id: UUID = Query(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = (await db.execute(select(BankAccount).where(BankAccount.company_id == company_id))).scalars().all()
    return {"success": True, "data": {"items": [_bank_account_to_dict(b) for b in rows]}}


@router.post("/accounts/", status_code=status.HTTP_201_CREATED, summary="Create bank account")
async def create_bank_account(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    bank = BankAccount(
        company_id=UUID(str(payload["company_id"])),
        account_id=UUID(str(payload["account_id"])),
        journal_id=UUID(str(payload["journal_id"])) if payload.get("journal_id") else None,
        name=payload["name"],
        account_number=payload.get("account_number"),
        bank_name=payload.get("bank_name"),
        bank_code=payload.get("bank_code"),
        currency_code=payload.get("currency_code", "KWD"),
        account_type=payload.get("account_type", "bank"),
    )
    db.add(bank)
    await db.flush()
    await db.refresh(bank)
    return {"success": True, "data": _bank_account_to_dict(bank)}


@router.get("/accounts/{bank_id}/", summary="Get bank account")
async def get_bank_account(bank_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(BankAccount).where(BankAccount.id == bank_id))
    bank = result.scalar_one_or_none()
    if not bank:
        raise HTTPException(status_code=404, detail="Bank account not found")
    return {"success": True, "data": _bank_account_to_dict(bank)}


@router.get("/statements/", summary="List bank statements")
async def list_statements(
    company_id: UUID = Query(...),
    bank_account_id: UUID | None = Query(None),
    state: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = select(BankStatement).where(BankStatement.company_id == company_id)
    if bank_account_id:
        q = q.where(BankStatement.bank_account_id == bank_account_id)
    if state:
        q = q.where(BankStatement.state == state)
    rows = (await db.execute(q.order_by(BankStatement.date_from.desc()))).scalars().all()
    return {"success": True, "data": {"items": [_statement_to_dict(s) for s in rows]}}


@router.post("/statements/", status_code=status.HTTP_201_CREATED, summary="Create bank statement")
async def create_statement(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    stmt = BankStatement(
        company_id=UUID(str(payload["company_id"])),
        bank_account_id=UUID(str(payload["bank_account_id"])),
        name=payload["name"],
        date_from=date.fromisoformat(payload["date_from"]),
        date_to=date.fromisoformat(payload["date_to"]),
        balance_start=float(payload.get("balance_start", 0)),
        balance_end=float(payload.get("balance_end", 0)),
        currency_code=payload.get("currency_code", "KWD"),
        state=BankStatementState.OPEN.value,
    )
    db.add(stmt)
    await db.flush()
    await db.refresh(stmt)
    return {"success": True, "data": _statement_to_dict(stmt)}


@router.get("/statements/{stmt_id}/", summary="Get bank statement with lines")
async def get_statement(stmt_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(
        select(BankStatement)
        .where(BankStatement.id == stmt_id)
        .options(selectinload(BankStatement.lines))
    )
    stmt = result.scalar_one_or_none()
    if not stmt:
        raise HTTPException(status_code=404, detail="Bank statement not found")
    return {"success": True, "data": _statement_to_dict(stmt, include_lines=True)}


@router.post("/statements/{stmt_id}/lines/", status_code=status.HTTP_201_CREATED, summary="Add statement line")
async def add_statement_line(stmt_id: UUID, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(BankStatement).where(BankStatement.id == stmt_id))
    stmt = result.scalar_one_or_none()
    if not stmt:
        raise HTTPException(status_code=404, detail="Bank statement not found")

    line = BankStatementLine(
        statement_id=stmt_id,
        date=date.fromisoformat(payload["date"]),
        name=payload["name"],
        reference=payload.get("reference"),
        amount=float(payload["amount"]),
        currency_code=payload.get("currency_code", stmt.currency_code),
        raw_data=payload.get("raw_data"),
    )
    db.add(line)
    await db.flush()
    await db.refresh(line)
    return {"success": True, "data": _line_to_dict(line)}


@router.post("/statements/{stmt_id}/reconcile/", summary="Reconcile a statement line")
async def reconcile_line(stmt_id: UUID, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    line_id = UUID(str(payload["line_id"]))
    journal_entry_id = UUID(str(payload["journal_entry_id"])) if payload.get("journal_entry_id") else None

    result = await db.execute(
        select(BankStatementLine).where(
            BankStatementLine.id == line_id,
            BankStatementLine.statement_id == stmt_id,
        )
    )
    line = result.scalar_one_or_none()
    if not line:
        raise HTTPException(status_code=404, detail="Statement line not found")

    line.is_reconciled = True
    line.journal_entry_id = journal_entry_id
    db.add(line)
    await db.flush()
    return {"success": True, "data": _line_to_dict(line)}
