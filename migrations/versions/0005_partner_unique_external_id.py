"""Merge duplicate partners and enforce unique (company_id, external_id).

Duplicates (same company + external_id, not deleted) are merged into the oldest
row: every foreign key pointing at `partners.id` is repointed to the survivor and
the duplicates are soft-deleted. A partial unique index then prevents new ones.

Revision ID: 0005_partner_unique_external_id
Revises: 0004_add_payment_refund_fields
Create Date: 2026-10-27
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_partner_unique_external_id"
down_revision: Union[str, None] = "0004_add_payment_refund_fields"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_partners_company_external_active"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "partners" not in inspector.get_table_names():
        return

    # (table, column) pairs that reference partners.id
    refs: list[tuple[str, str]] = []
    for table in inspector.get_table_names():
        for fk in inspector.get_foreign_keys(table):
            if fk.get("referred_table") == "partners" and fk.get("constrained_columns"):
                refs.append((table, fk["constrained_columns"][0]))

    # Map every duplicate to its survivor (oldest row; id as tie-breaker).
    pairs = bind.execute(
        sa.text(
            """
            SELECT id, keep_id FROM (
                SELECT id,
                       first_value(id) OVER (
                           PARTITION BY company_id, external_id
                           ORDER BY created_at ASC, id ASC
                       ) AS keep_id
                FROM partners
                WHERE is_deleted = false AND external_id IS NOT NULL
            ) t
            WHERE id <> keep_id
            """
        )
    ).fetchall()

    for dup_id, keep_id in pairs:
        for table, col in refs:
            bind.execute(
                sa.text(f'UPDATE "{table}" SET "{col}" = :keep WHERE "{col}" = :dup'),
                {"keep": keep_id, "dup": dup_id},
            )
        bind.execute(
            sa.text(
                "UPDATE partners SET is_deleted = true, deleted_at = now(), is_active = false "
                "WHERE id = :dup"
            ),
            {"dup": dup_id},
        )

    existing = {ix["name"] for ix in inspector.get_indexes("partners")}
    if INDEX_NAME not in existing:
        op.create_index(
            INDEX_NAME,
            "partners",
            ["company_id", "external_id"],
            unique=True,
            postgresql_where=sa.text("is_deleted = false AND external_id IS NOT NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "partners" in inspector.get_table_names():
        existing = {ix["name"] for ix in inspector.get_indexes("partners")}
        if INDEX_NAME in existing:
            op.drop_index(INDEX_NAME, table_name="partners")
