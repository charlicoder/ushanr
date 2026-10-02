"""
app/api/v1/endpoints/assets.py
──────────────────────────────
Fixed Assets and Depreciation REST endpoints.

GET    /api/v1/assets/                     — list assets
POST   /api/v1/assets/                     — create asset with depreciation schedule
GET    /api/v1/assets/{id}/                — get asset detail including schedule
POST   /api/v1/assets/{id}/schedule/       — regenerate schedule
POST   /api/v1/assets/lines/{id}/post/     — post depreciation line to GL
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_optional_company_id
from app.core.database import get_db
from app.core.exceptions import ANRBaseError, NotFoundError
from app.core.logging import get_logger
from app.models.asset import Asset, AssetDepreciationLine
from app.schemas.asset import AssetCreateRequest
from app.services.asset_service import AssetService

logger = get_logger(__name__)
router = APIRouter()


def _asset_to_dict(asset: Asset) -> dict:
    return {
        "id": str(asset.id),
        "company_id": str(asset.company_id),
        "name": asset.name,
        "purchase_date": asset.purchase_date.isoformat(),
        "asset_value": float(asset.asset_value),
        "book_value": float(asset.book_value),
        "salvage_value": float(asset.salvage_value),
        "fixed_asset_account_id": str(asset.fixed_asset_account_id) if asset.fixed_asset_account_id else None,
        "depreciation_account_id": str(asset.depreciation_account_id) if asset.depreciation_account_id else None,
        "expense_account_id": str(asset.expense_account_id) if asset.expense_account_id else None,
        "depreciation_model": asset.depreciation_model,
        "method_number": asset.method_number,
        "method_period": asset.method_period,
        "asset_group": asset.asset_group,
        "status": asset.status,
        "partner_id": str(asset.partner_id) if asset.partner_id else None,
        "created_at": asset.created_at.isoformat(),
        "updated_at": asset.updated_at.isoformat(),
    }


def _line_to_dict(line: AssetDepreciationLine) -> dict:
    return {
        "id": str(line.id),
        "company_id": str(line.company_id),
        "asset_id": str(line.asset_id),
        "depreciation_date": line.depreciation_date.isoformat(),
        "amount": float(line.amount),
        "remaining_value": float(line.remaining_value),
        "depreciated_value": float(line.depreciated_value),
        "is_posted": line.is_posted,
        "journal_entry_id": str(line.journal_entry_id) if line.journal_entry_id else None,
        "created_at": line.created_at.isoformat(),
        "updated_at": line.updated_at.isoformat(),
    }


@router.get("/", summary="List fixed assets")
async def list_assets(
    company_id: UUID | None = Depends(get_optional_company_id),
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if company_id is None:
        raise HTTPException(status_code=400, detail="Company ID required")
    service = AssetService(db)
    assets = await service.list_assets(company_id=company_id, status=status)
    return {
        "success": True,
        "data": [_asset_to_dict(a) for a in assets],
        "count": len(assets),
    }


@router.post("/", status_code=status.HTTP_201_CREATED, summary="Create a fixed asset")
async def create_asset(
    payload: AssetCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    service = AssetService(db)
    try:
        asset = await service.create_asset(
            company_id=payload.company_id,
            name=payload.name,
            purchase_date=payload.purchase_date,
            asset_value=payload.asset_value,
            salvage_value=payload.salvage_value,
            fixed_asset_account_id=payload.fixed_asset_account_id,
            depreciation_account_id=payload.depreciation_account_id,
            expense_account_id=payload.expense_account_id,
            depreciation_model=payload.depreciation_model,
            method_number=payload.method_number,
            method_period=payload.method_period,
            asset_group=payload.asset_group,
            partner_id=payload.partner_id,
            status=payload.status,
        )
        await db.commit()
        return {
            "success": True,
            "data": _asset_to_dict(asset),
        }
    except ANRBaseError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{asset_id}/", summary="Get fixed asset details and depreciation schedule")
async def get_asset(
    asset_id: UUID,
    company_id: UUID | None = Depends(get_optional_company_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if company_id is None:
        raise HTTPException(status_code=400, detail="Company ID required")
    service = AssetService(db)
    try:
        asset = await service.get_asset(asset_id=asset_id, company_id=company_id)
        asset_dict = _asset_to_dict(asset)
        asset_dict["depreciation_lines"] = [_line_to_dict(l) for l in asset.depreciation_lines]
        return {
            "success": True,
            "data": asset_dict,
        }
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{asset_id}/schedule/", summary="Regenerate depreciation schedule")
async def regenerate_schedule(
    asset_id: UUID,
    company_id: UUID | None = Depends(get_optional_company_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if company_id is None:
        raise HTTPException(status_code=400, detail="Company ID required")
    service = AssetService(db)
    try:
        lines = await service.generate_schedule(asset_id=asset_id, company_id=company_id)
        await db.commit()
        return {
            "success": True,
            "data": [_line_to_dict(l) for l in lines],
            "count": len(lines),
        }
    except NotFoundError as e:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/lines/{line_id}/post/", summary="Post a depreciation line into General Ledger")
async def post_depreciation_line(
    line_id: UUID,
    company_id: UUID | None = Depends(get_optional_company_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if company_id is None:
        raise HTTPException(status_code=400, detail="Company ID required")
    service = AssetService(db)
    try:
        line = await service.post_depreciation_line(line_id=line_id, company_id=company_id)
        await db.commit()
        return {
            "success": True,
            "data": _line_to_dict(line),
        }
    except NotFoundError as e:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(e))
    except ANRBaseError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
