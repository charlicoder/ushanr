"""
app/api/v1/endpoints/budgets.py — Budget CRUD + variance
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.budget import Budget, BudgetLine, BudgetState
from app.models.journal_entry import EntryState, JournalEntry, JournalItem

router = APIRouter()


def _budget_to_dict(b: Budget, include_lines: bool = False) -> dict:
    d = {
        "id": str(b.id),
        "company_id": str(b.company_id),
        "name": b.name,
        "fiscal_year_id": str(b.fiscal_year_id) if b.fiscal_year_id else None,
        "date_from": b.date_from.isoformat(),
        "date_to": b.date_to.isoformat(),
        "state": b.state,
        "description": b.description,
        "created_at": b.created_at.isoformat(),
    }
    if include_lines:
        d["lines"] = [_line_to_dict(l) for l in (b.lines or [])]
    return d


def _line_to_dict(l: BudgetLine) -> dict:
    return {
        "id": str(l.id),
        "budget_id": str(l.budget_id),
        "account_id": str(l.account_id),
        "analytic_account_id": str(l.analytic_account_id) if l.analytic_account_id else None,
        "date_from": l.date_from.isoformat(),
        "date_to": l.date_to.isoformat(),
        "planned_amount": float(l.planned_amount),
        "currency_code": l.currency_code,
    }


@router.get("/", summary="List budgets")
async def list_budgets(
    company_id: UUID = Query(...),
    state: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = select(Budget).where(Budget.company_id == company_id)
    if state:
        q = q.where(Budget.state == state)
    rows = (await db.execute(q.order_by(Budget.date_from.desc()))).scalars().all()
    return {"success": True, "data": {"items": [_budget_to_dict(b) for b in rows]}}


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create budget")
async def create_budget(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    budget = Budget(
        company_id=UUID(str(payload["company_id"])),
        name=payload["name"],
        fiscal_year_id=UUID(str(payload["fiscal_year_id"])) if payload.get("fiscal_year_id") else None,
        date_from=date.fromisoformat(payload["date_from"]),
        date_to=date.fromisoformat(payload["date_to"]),
        state=BudgetState.DRAFT.value,
        description=payload.get("description"),
    )
    db.add(budget)
    await db.flush()

    for raw_line in payload.get("lines", []):
        line = BudgetLine(
            budget_id=budget.id,
            account_id=UUID(str(raw_line["account_id"])),
            analytic_account_id=UUID(str(raw_line["analytic_account_id"])) if raw_line.get("analytic_account_id") else None,
            date_from=date.fromisoformat(raw_line["date_from"]),
            date_to=date.fromisoformat(raw_line["date_to"]),
            planned_amount=float(raw_line["planned_amount"]),
            currency_code=raw_line.get("currency_code", "KWD"),
        )
        db.add(line)

    await db.flush()
    result = await db.execute(select(Budget).where(Budget.id == budget.id).options(selectinload(Budget.lines)))
    budget = result.scalar_one()
    return {"success": True, "data": _budget_to_dict(budget, include_lines=True)}


@router.get("/{budget_id}/", summary="Get budget with lines")
async def get_budget(budget_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(
        select(Budget).where(Budget.id == budget_id).options(selectinload(Budget.lines))
    )
    budget = result.scalar_one_or_none()
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")
    return {"success": True, "data": _budget_to_dict(budget, include_lines=True)}


@router.put("/{budget_id}/", summary="Update budget")
async def update_budget(budget_id: UUID, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Budget).where(Budget.id == budget_id).options(selectinload(Budget.lines)))
    budget = result.scalar_one_or_none()
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")
    if budget.state != BudgetState.DRAFT.value:
        raise HTTPException(status_code=409, detail="Only draft budgets can be updated")
    for f in ["name", "description"]:
        if f in payload:
            setattr(budget, f, payload[f])
    db.add(budget)
    await db.flush()
    return {"success": True, "data": _budget_to_dict(budget, include_lines=True)}


@router.post("/{budget_id}/confirm/", summary="Confirm budget")
async def confirm_budget(budget_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Budget).where(Budget.id == budget_id))
    budget = result.scalar_one_or_none()
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")
    if budget.state != BudgetState.DRAFT.value:
        raise HTTPException(status_code=409, detail=f"Budget is already {budget.state}")
    budget.state = BudgetState.CONFIRMED.value
    db.add(budget)
    await db.flush()
    return {"success": True, "data": _budget_to_dict(budget)}


@router.get("/{budget_id}/variance/", summary="Budget vs actual variance")
async def budget_variance(budget_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    """Compare planned budget amounts vs actual posted journal items."""
    result = await db.execute(
        select(Budget).where(Budget.id == budget_id).options(selectinload(Budget.lines))
    )
    budget = result.scalar_one_or_none()
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")

    variance_lines = []
    for line in budget.lines:
        # Sum actual journal items for this account in the period
        actual_result = await db.execute(
            select(
                func.coalesce(func.sum(JournalItem.debit_amount), 0).label("debit"),
                func.coalesce(func.sum(JournalItem.credit_amount), 0).label("credit"),
            )
            .join(JournalEntry, JournalItem.entry_id == JournalEntry.id)
            .where(
                JournalItem.account_id == line.account_id,
                JournalItem.company_id == budget.company_id,
                JournalEntry.state == EntryState.POSTED.value,
                JournalEntry.accounting_date >= line.date_from,
                JournalEntry.accounting_date <= line.date_to,
            )
        )
        row = actual_result.one()
        actual = float(Decimal(str(row.debit)) - Decimal(str(row.credit)))
        planned = float(line.planned_amount)
        variance = actual - planned

        variance_lines.append({
            "account_id": str(line.account_id),
            "date_from": line.date_from.isoformat(),
            "date_to": line.date_to.isoformat(),
            "planned_amount": planned,
            "actual_amount": actual,
            "variance": variance,
            "variance_pct": round((variance / planned * 100) if planned != 0 else 0, 2),
        })

    total_planned = sum(l["planned_amount"] for l in variance_lines)
    total_actual = sum(l["actual_amount"] for l in variance_lines)
    return {
        "success": True,
        "data": {
            "budget_id": str(budget_id),
            "budget_name": budget.name,
            "lines": variance_lines,
            "total_planned": total_planned,
            "total_actual": total_actual,
            "total_variance": total_actual - total_planned,
        },
    }
