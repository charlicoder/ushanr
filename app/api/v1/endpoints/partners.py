"""
app/api/v1/endpoints/partners.py
──────────────────────────────────
Partner (Customer / Vendor) CRUD endpoints.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_optional_company_id
from app.core.database import get_db
from app.core.security import require_permission
from app.models.partner import Partner

router = APIRouter()


def _partner_to_dict(p: Partner) -> dict:
    return {
        "id": str(p.id),
        "company_id": str(p.company_id),
        "name": p.name,
        "display_name": p.display_name,
        "partner_type": p.partner_type,
        "is_customer": p.is_customer,
        "is_vendor": p.is_vendor,
        "is_company": p.is_company,
        "email": p.email,
        "phone": p.phone,
        "mobile": p.mobile,
        "street": p.street,
        "city": p.city,
        "country_code": p.country_code,
        "tax_id": p.tax_id,
        "vat_number": p.vat_number,
        "payment_terms_days": p.payment_terms_days,
        "currency_code": p.currency_code,
        "external_id": p.external_id,
        "is_active": p.is_active,
        "notes": p.notes,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


@router.get("/", summary="List partners")
async def list_partners(
    company_id: UUID | None = Depends(get_optional_company_id),
    partner_type: str | None = Query(None),
    is_customer: bool | None = Query(None),
    is_vendor: bool | None = Query(None),
    is_active: bool | None = Query(None),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("vendors.list")),
) -> dict:
    q = select(Partner).where(Partner.is_deleted == False)
    if company_id is not None:
        q = q.where(Partner.company_id == company_id)
    if partner_type:
        q = q.where(Partner.partner_type == partner_type)
    if is_customer is not None:
        q = q.where(Partner.is_customer == is_customer)
    if is_vendor is not None:
        q = q.where(Partner.is_vendor == is_vendor)
    if is_active is not None:
        q = q.where(Partner.is_active == is_active)
    if search:
        q = q.where(
            (Partner.name.ilike(f"%{search}%")) | (Partner.email.ilike(f"%{search}%"))
        )
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(Partner.name).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {
        "success": True,
        "data": {
            "items": [_partner_to_dict(p) for p in rows],
            "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        },
    }


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create partner")
async def create_partner(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("vendors.create")),
) -> dict:
    partner = Partner(
        company_id=UUID(str(payload["company_id"])),
        name=payload["name"],
        display_name=payload.get("display_name"),
        partner_type=payload.get("partner_type", "customer"),
        is_customer=payload.get("is_customer", True),
        is_vendor=payload.get("is_vendor", False),
        is_company=payload.get("is_company", False),
        email=payload.get("email"),
        phone=payload.get("phone"),
        mobile=payload.get("mobile"),
        street=payload.get("street"),
        city=payload.get("city"),
        state=payload.get("state"),
        zip_code=payload.get("zip_code"),
        country_code=payload.get("country_code"),
        tax_id=payload.get("tax_id"),
        vat_number=payload.get("vat_number"),
        payment_terms_days=payload.get("payment_terms_days", 0),
        currency_code=payload.get("currency_code", "KWD"),
        external_id=payload.get("external_id"),
        notes=payload.get("notes"),
    )
    db.add(partner)
    await db.flush()
    await db.refresh(partner)
    return {"success": True, "data": _partner_to_dict(partner)}


@router.get("/{partner_id}/", summary="Get partner")
async def get_partner(
    partner_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("vendors.view")),
) -> dict:
    result = await db.execute(
        select(Partner).where(Partner.id == partner_id, Partner.is_deleted == False)
    )
    partner = result.scalar_one_or_none()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    return {"success": True, "data": _partner_to_dict(partner)}


@router.put("/{partner_id}/", summary="Update partner")
async def update_partner(
    partner_id: UUID,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("vendors.update")),
) -> dict:
    result = await db.execute(
        select(Partner).where(Partner.id == partner_id, Partner.is_deleted == False)
    )
    partner = result.scalar_one_or_none()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    updatable = [
        "name", "display_name", "email", "phone", "mobile", "street", "city",
        "country_code", "tax_id", "vat_number", "payment_terms_days", "notes",
        "is_active", "is_customer", "is_vendor", "currency_code",
    ]
    for f in updatable:
        if f in payload:
            setattr(partner, f, payload[f])
    db.add(partner)
    await db.flush()
    await db.refresh(partner)
    return {"success": True, "data": _partner_to_dict(partner)}


@router.delete("/{partner_id}/", status_code=status.HTTP_204_NO_CONTENT, summary="Soft-delete partner")
async def delete_partner(
    partner_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_permission("vendors.delete")),
) -> None:
    result = await db.execute(
        select(Partner).where(Partner.id == partner_id, Partner.is_deleted == False)
    )
    partner = result.scalar_one_or_none()
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    partner.is_deleted = True
    partner.deleted_at = datetime.now(timezone.utc)
    db.add(partner)

