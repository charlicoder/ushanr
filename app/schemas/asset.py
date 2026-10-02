"""
app/schemas/asset.py
────────────────────
Pydantic v2 schemas for Fixed Asset and Depreciation endpoints.
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AssetStatusEnum(str, enum.Enum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    CLOSE = "close"
    CANCELLED = "cancelled"


class AssetDepreciationLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    asset_id: UUID
    depreciation_date: date
    amount: Decimal
    remaining_value: Decimal
    depreciated_value: Decimal
    is_posted: bool
    journal_entry_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class AssetCreateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company_id: UUID
    name: str = Field(..., max_length=255, description="Asset name or description")
    purchase_date: date = Field(..., description="Purchase / capitalization date")
    asset_value: Decimal = Field(..., gt=0, description="Gross acquisition cost")
    salvage_value: Decimal = Field(default=Decimal("0.0"), ge=0, description="Estimated salvage / scrap value")
    fixed_asset_account_id: UUID | None = None
    depreciation_account_id: UUID | None = None
    expense_account_id: UUID | None = None
    depreciation_model: str | None = Field(default="36 Month Linear", description="Description of the model")
    method_number: int = Field(default=36, gt=0, description="Total depreciation periods (e.g. 36 months)")
    method_period: int = Field(default=1, gt=0, description="Period interval in months")
    asset_group: str | None = None
    partner_id: UUID | None = None
    status: AssetStatusEnum = AssetStatusEnum.RUNNING


class AssetUpdateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str | None = None
    purchase_date: date | None = None
    salvage_value: Decimal | None = None
    fixed_asset_account_id: UUID | None = None
    depreciation_account_id: UUID | None = None
    expense_account_id: UUID | None = None
    depreciation_model: str | None = None
    method_number: int | None = None
    method_period: int | None = None
    asset_group: str | None = None
    partner_id: UUID | None = None
    status: AssetStatusEnum | None = None


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    name: str
    purchase_date: date
    asset_value: Decimal
    book_value: Decimal
    salvage_value: Decimal
    fixed_asset_account_id: UUID | None
    depreciation_account_id: UUID | None
    expense_account_id: UUID | None
    depreciation_model: str | None
    method_number: int
    method_period: int
    asset_group: str | None
    status: str
    partner_id: UUID | None
    created_at: datetime
    updated_at: datetime


class AssetDetailResponse(AssetResponse):
    depreciation_lines: list[AssetDepreciationLineResponse] = Field(default_factory=list)
