"""Add invoice_number to journal_entries and journal_items.

Revision ID: 0002_journal_invoice_number
Revises: 0001_initial_schema
Create Date: 2026-10-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_journal_invoice_number"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("journal_entries", sa.Column("invoice_number", sa.String(100), nullable=True))
    op.add_column("journal_items", sa.Column("invoice_number", sa.String(100), nullable=True))
    op.create_index("ix_journal_entries_invoice_number", "journal_entries", ["invoice_number"])
    op.create_index("ix_journal_items_invoice_number", "journal_items", ["invoice_number"])

    # Backfill from the invoice that each entry was posted for
    op.execute(
        """
        UPDATE journal_entries je
        SET invoice_number = inv.name
        FROM invoices inv
        WHERE je.source_document_type = 'invoice'
          AND je.source_document_id = inv.id
          AND inv.name IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE journal_items ji
        SET invoice_number = je.invoice_number
        FROM journal_entries je
        WHERE ji.entry_id = je.id AND je.invoice_number IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_journal_items_invoice_number", table_name="journal_items")
    op.drop_index("ix_journal_entries_invoice_number", table_name="journal_entries")
    op.drop_column("journal_items", "invoice_number")
    op.drop_column("journal_entries", "invoice_number")
