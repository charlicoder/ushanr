"""Add refund fields to anr_payments table.

Revision ID: 0004_add_payment_refund_fields
Revises: 0003_set_db_timezone_kuwait
Create Date: 2026-10-08
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_add_payment_refund_fields"
down_revision: Union[str, None] = "0003_set_db_timezone_kuwait"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "anr_payments" in existing_tables:
        cols = {c["name"] for c in inspector.get_columns("anr_payments")}
        if "refund_number" not in cols:
            op.add_column(
                "anr_payments",
                sa.Column("refund_number", sa.String(100), nullable=True),
            )
        if "is_refund" not in cols:
            op.add_column(
                "anr_payments",
                sa.Column("is_refund", sa.Boolean(), nullable=False, server_default="false"),
            )
        if "cancellation_fee" not in cols:
            op.add_column(
                "anr_payments",
                sa.Column(
                    "cancellation_fee",
                    sa.Numeric(precision=20, scale=3),
                    nullable=False,
                    server_default="0.000",
                ),
            )

        indexes = {i["name"] for i in inspector.get_indexes("anr_payments")}
        if "ix_anr_payments_refund_number" not in indexes:
            op.create_index(
                "ix_anr_payments_refund_number",
                "anr_payments",
                ["refund_number"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "anr_payments" in existing_tables:
        indexes = {i["name"] for i in inspector.get_indexes("anr_payments")}
        if "ix_anr_payments_refund_number" in indexes:
            op.drop_index("ix_anr_payments_refund_number", table_name="anr_payments")

        cols = {c["name"] for c in inspector.get_columns("anr_payments")}
        if "cancellation_fee" in cols:
            op.drop_column("anr_payments", "cancellation_fee")
        if "is_refund" in cols:
            op.drop_column("anr_payments", "is_refund")
        if "refund_number" in cols:
            op.drop_column("anr_payments", "refund_number")
