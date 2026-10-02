"""
app/services/asset_service.py
─────────────────────────────
Fixed Asset and Depreciation Management Service.
Manages asset capitalization, linear depreciation schedules, and automated GL posting.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AccountingError, NotFoundError
from app.core.logging import get_logger
from app.models.asset import Asset, AssetDepreciationLine, AssetStatus
from app.models.journal_entry import EntryState, JournalEntry, JournalItem
from app.models.journal import Journal

logger = get_logger(__name__)
PRECISION = Decimal("0.001")


def _add_months(sourcedate: date, months: int) -> date:
    """Add months to a date, preserving day of month as closely as possible."""
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    import calendar
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


class AssetService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_asset(
        self,
        company_id: uuid.UUID,
        name: str,
        purchase_date: date,
        asset_value: Decimal,
        salvage_value: Decimal = Decimal("0.0"),
        fixed_asset_account_id: uuid.UUID | None = None,
        depreciation_account_id: uuid.UUID | None = None,
        expense_account_id: uuid.UUID | None = None,
        depreciation_model: str | None = "36 Month Linear",
        method_number: int = 36,
        method_period: int = 1,
        asset_group: str | None = None,
        partner_id: uuid.UUID | None = None,
        status: AssetStatus = AssetStatus.RUNNING,
    ) -> Asset:
        """Create a new fixed asset and immediately generate its depreciation schedule."""
        asset = Asset(
            id=uuid.uuid4(),
            company_id=company_id,
            name=name,
            purchase_date=purchase_date,
            asset_value=float(asset_value),
            book_value=float(asset_value),
            salvage_value=float(salvage_value),
            fixed_asset_account_id=fixed_asset_account_id,
            depreciation_account_id=depreciation_account_id,
            expense_account_id=expense_account_id,
            depreciation_model=depreciation_model,
            method_number=method_number,
            method_period=method_period,
            asset_group=asset_group,
            partner_id=partner_id,
            status=status.value,
        )
        self.db.add(asset)
        await self.db.flush()

        # Generate schedule lines
        await self.generate_schedule(asset.id, company_id)
        await self.db.refresh(asset)
        return asset

    async def get_asset(self, asset_id: uuid.UUID, company_id: uuid.UUID) -> Asset:
        query = (
            select(Asset)
            .where(Asset.id == asset_id, Asset.company_id == company_id)
            .options(selectinload(Asset.depreciation_lines))
        )
        result = await self.db.execute(query)
        asset = result.scalar_one_or_none()
        if not asset:
            raise NotFoundError(f"Asset with id {asset_id} not found.")
        return asset

    async def list_assets(
        self,
        company_id: uuid.UUID,
        status: str | None = None,
    ) -> Sequence[Asset]:
        query = select(Asset).where(Asset.company_id == company_id)
        if status:
            query = query.where(Asset.status == status)
        query = query.order_by(Asset.purchase_date.desc())
        result = await self.db.execute(query)
        return result.scalars().all()

    async def generate_schedule(self, asset_id: uuid.UUID, company_id: uuid.UUID) -> list[AssetDepreciationLine]:
        """Generate or regenerate linear depreciation schedule lines for an asset."""
        asset = await self.get_asset(asset_id, company_id)
        if not asset:
            raise NotFoundError(f"Asset {asset_id} not found.")

        # Remove existing unposted lines
        for line in list(asset.depreciation_lines):
            if not line.is_posted:
                await self.db.delete(line)
        await self.db.flush()

        gross = Decimal(str(asset.asset_value))
        salvage = Decimal(str(asset.salvage_value))
        depreciable_base = gross - salvage
        periods = asset.method_number or 36

        if periods <= 0:
            return []

        monthly_amount = (depreciable_base / Decimal(periods)).quantize(PRECISION, rounding=ROUND_HALF_UP)
        accumulated = Decimal("0.0")
        lines: list[AssetDepreciationLine] = []

        for p in range(1, periods + 1):
            line_date = _add_months(asset.purchase_date, p * (asset.method_period or 1))
            if p == periods:
                # Adjust last period for rounding differences
                period_amt = depreciable_base - accumulated
            else:
                period_amt = monthly_amount

            accumulated += period_amt
            rem_val = gross - accumulated

            dep_line = AssetDepreciationLine(
                id=uuid.uuid4(),
                company_id=company_id,
                asset_id=asset.id,
                depreciation_date=line_date,
                amount=float(period_amt),
                depreciated_value=float(accumulated),
                remaining_value=float(rem_val),
                is_posted=False,
            )
            self.db.add(dep_line)
            lines.append(dep_line)

        await self.db.flush()
        return lines

    async def post_depreciation_line(
        self,
        line_id: uuid.UUID,
        company_id: uuid.UUID,
    ) -> AssetDepreciationLine:
        """
        Post a depreciation line into the General Ledger:
          Dr: Depreciation Expense
          Cr: Accumulated Depreciation
        """
        query = (
            select(AssetDepreciationLine)
            .where(AssetDepreciationLine.id == line_id, AssetDepreciationLine.company_id == company_id)
        )
        res = await self.db.execute(query)
        line = res.scalar_one_or_none()
        if not line:
            raise NotFoundError(f"Depreciation line {line_id} not found.")

        if line.is_posted:
            raise AccountingError("This depreciation line is already posted.")

        asset = await self.get_asset(line.asset_id, company_id)

        if not asset.depreciation_account_id or not asset.expense_account_id:
            raise AccountingError("Asset is missing depreciation_account_id or expense_account_id.")

        # Find or use miscellaneous journal
        j_query = select(Journal).where(Journal.company_id == company_id, Journal.journal_type == "general").limit(1)
        j_res = await self.db.execute(j_query)
        journal = j_res.scalar_one_or_none()
        journal_id = journal.id if journal else None

        amt = Decimal(str(line.amount))
        entry_number = f"DEP/{line.depreciation_date.strftime('%Y/%m')}/{str(uuid.uuid4())[:8]}"

        entry = JournalEntry(
            id=uuid.uuid4(),
            company_id=company_id,
            journal_id=journal_id,
            entry_number=entry_number,
            accounting_date=line.depreciation_date,
            state=EntryState.POSTED.value,
            reference=f"Depreciation - {asset.name}",
            notes=f"Auto-generated depreciation for {asset.name} on {line.depreciation_date}",
        )
        self.db.add(entry)

        # Debit Expense
        dr_item = JournalItem(
            id=uuid.uuid4(),
            company_id=company_id,
            entry_id=entry.id,
            account_id=asset.expense_account_id,
            debit_amount=float(amt),
            credit_amount=0.0,
            partner_id=asset.partner_id,
            name=f"Depreciation expense - {asset.name}",
        )
        # Credit Accumulated Depreciation
        cr_item = JournalItem(
            id=uuid.uuid4(),
            company_id=company_id,
            entry_id=entry.id,
            account_id=asset.depreciation_account_id,
            debit_amount=0.0,
            credit_amount=float(amt),
            partner_id=asset.partner_id,
            name=f"Accumulated depreciation - {asset.name}",
        )
        self.db.add_all([dr_item, cr_item])

        line.is_posted = True
        line.journal_entry_id = entry.id

        # Update asset book value
        asset.book_value = float(Decimal(str(asset.book_value)) - amt)

        await self.db.flush()
        return line
