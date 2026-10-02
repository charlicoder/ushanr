"""
tests/unit/test_asset_model.py
──────────────────────────────
Unit tests for Asset and AssetDepreciationLine models and logic.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.asset import Asset, AssetDepreciationLine, AssetStatus


class TestAssetModel:
    @pytest.mark.unit
    def test_asset_creation_and_properties(self):
        asset = Asset(
            name="Curtains",
            purchase_date=date(2026, 9, 1),
            asset_value=500.0,
            book_value=500.0,
            status=AssetStatus.RUNNING.value,
            method_number=36,
            method_period=1,
            salvage_value=5.0,
        )
        assert asset.name == "Curtains"
        assert asset.status == AssetStatus.RUNNING.value
        assert asset.salvage_value == 5.0
        assert asset.method_number == 36
        assert asset.method_period == 1

    @pytest.mark.unit
    def test_asset_linear_depreciation_math(self):
        # 360 KWD over 36 months, 0 salvage
        cost = Decimal("360.000")
        salvage = Decimal("0.000")
        periods = 36
        monthly = (cost - salvage) / periods
        assert monthly == Decimal("10.000")

    @pytest.mark.unit
    def test_asset_status_enum_values(self):
        assert AssetStatus.RUNNING.value == "running"
        assert AssetStatus.CLOSE.value == "close"
        assert AssetStatus.DRAFT.value == "draft"
        assert AssetStatus.CANCELLED.value == "cancelled"
