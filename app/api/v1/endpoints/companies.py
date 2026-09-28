"""
app/api/v1/endpoints/companies.py — Company CRUD
app/api/v1/endpoints/partners.py  — Partner CRUD
app/api/v1/endpoints/journals.py  — Journal CRUD
"""
from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.models.company import Company

router = APIRouter()


def _company_to_dict(c: Company) -> dict:
    return {
        "id": str(c.id),
        "name": c.name,
        "legal_name": c.legal_name,
        "trade_name": c.trade_name,
        "tax_id": c.tax_id,
        "currency_code": c.currency_code,
        "country_code": c.country_code,
        "address": c.address,
        "phone": c.phone,
        "email": c.email,
        "website": c.website,
        "logo_url": c.logo_url,
        "is_active": c.is_active,
        "fiscal_year_start_month": c.fiscal_year_start_month,
        "decimal_places": c.decimal_places,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
    }


@router.get("/", summary="List companies")
async def list_companies(
    is_active: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict:
    q = select(Company)
    if is_active is not None:
        q = q.where(Company.is_active == is_active)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(Company.name).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {
        "success": True,
        "data": {
            "items": [_company_to_dict(c) for c in rows],
            "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        },
    }


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create company")
async def create_company(payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    company = Company(
        name=payload["name"],
        legal_name=payload.get("legal_name"),
        trade_name=payload.get("trade_name"),
        tax_id=payload.get("tax_id"),
        currency_code=payload.get("currency_code", "KWD"),
        country_code=payload.get("country_code", "KW"),
        address=payload.get("address"),
        phone=payload.get("phone"),
        email=payload.get("email"),
        website=payload.get("website"),
        fiscal_year_start_month=payload.get("fiscal_year_start_month", 1),
        decimal_places=payload.get("decimal_places", 3),
    )
    db.add(company)
    await db.flush()
    await db.refresh(company)
    return {"success": True, "data": _company_to_dict(company)}


@router.get("/{company_id}/", summary="Get company")
async def get_company(company_id: UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return {"success": True, "data": _company_to_dict(company)}


@router.put("/{company_id}/", summary="Update company")
async def update_company(company_id: UUID, payload: dict, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Company).where(Company.id == company_id))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    updatable = ["name", "legal_name", "trade_name", "address", "phone", "email", "website",
                 "currency_code", "is_active", "fiscal_year_start_month", "decimal_places", "logo_url"]
    for f in updatable:
        if f in payload:
            setattr(company, f, payload[f])
    db.add(company)
    await db.flush()
    await db.refresh(company)
    return {"success": True, "data": _company_to_dict(company)}
