"""Create feedbacks table (one feedback link / response per completed booking).

Revision ID: 0006_feedback
Revises: 0005_partner_unique_external_id
Create Date: 2026-10-28
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_feedback"
down_revision: Union[str, None] = "0005_partner_unique_external_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "feedbacks" in sa.inspect(bind).get_table_names():
        return

    uuid_t = postgresql.UUID(as_uuid=True)
    op.create_table(
        "feedbacks",
        sa.Column("id", uuid_t, primary_key=True),
        sa.Column("public_token", sa.String(64), nullable=False),
        sa.Column("booking_id", uuid_t, nullable=False),
        sa.Column("booking_number", sa.String(64)),
        sa.Column("company_id", uuid_t),
        sa.Column("customer_id", uuid_t),
        sa.Column("customer_name", sa.String(255)),
        sa.Column("customer_phone", sa.String(50)),
        sa.Column("service_id", uuid_t),
        sa.Column("service_name", sa.String(255)),
        sa.Column("services", postgresql.JSONB),
        sa.Column("therapist_id", uuid_t),
        sa.Column("therapist_name", sa.String(255)),
        sa.Column("secondary_therapist_id", uuid_t),
        sa.Column("secondary_therapist_name", sa.String(255)),
        sa.Column("branch_id", uuid_t),
        sa.Column("branch_name", sa.String(255)),
        sa.Column("appointment_start", sa.DateTime(timezone=True)),
        sa.Column("language", sa.String(5), nullable=False, server_default="ar"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("service_rating", sa.SmallInteger),
        sa.Column("therapist_rating", sa.SmallInteger),
        sa.Column("arrangement_rating", sa.SmallInteger),
        sa.Column("comment", sa.Text),
        sa.Column("created_by", sa.String(255)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "service_rating IS NULL OR service_rating BETWEEN 1 AND 5", name="ck_feedbacks_service_rating"
        ),
        sa.CheckConstraint(
            "therapist_rating IS NULL OR therapist_rating BETWEEN 1 AND 5", name="ck_feedbacks_therapist_rating"
        ),
        sa.CheckConstraint(
            "arrangement_rating IS NULL OR arrangement_rating BETWEEN 1 AND 5",
            name="ck_feedbacks_arrangement_rating",
        ),
        sa.CheckConstraint(
            "status <> 'submitted' OR (service_rating IS NOT NULL AND therapist_rating IS NOT NULL "
            "AND arrangement_rating IS NOT NULL)",
            name="ck_feedbacks_submitted_complete",
        ),
    )
    op.create_index("ix_feedbacks_id", "feedbacks", ["id"])
    op.create_index("ix_feedbacks_public_token", "feedbacks", ["public_token"], unique=True)
    op.create_index("ix_feedbacks_booking_id", "feedbacks", ["booking_id"], unique=True)
    op.create_index("ix_feedbacks_booking_number", "feedbacks", ["booking_number"])
    op.create_index("ix_feedbacks_company_id", "feedbacks", ["company_id"])
    op.create_index("ix_feedbacks_customer_id", "feedbacks", ["customer_id"])
    op.create_index("ix_feedbacks_service_id", "feedbacks", ["service_id"])
    op.create_index("ix_feedbacks_therapist_id", "feedbacks", ["therapist_id"])
    op.create_index("ix_feedbacks_branch_id", "feedbacks", ["branch_id"])
    op.create_index("ix_feedbacks_status", "feedbacks", ["status"])
    op.create_index("ix_feedbacks_status_submitted_at", "feedbacks", ["status", "submitted_at"])


def downgrade() -> None:
    if "feedbacks" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("feedbacks")
