"""Workforce history & analytics tables (therapist roster, completed sessions, timeline, summary).

Revision ID: 0007_workforce_history
Revises: 0006_feedback
Create Date: 2026-11-06
"""
from __future__ import annotations

from typing import Union

from alembic import op

revision: str = "0007_workforce_history"
down_revision: Union[str, None] = "0006_feedback"
branch_labels = None
depends_on = None

_TABLES = (
    "wf_schedule_change_log",
    "wf_therapist_daily_roster",
    "wf_completed_session",
    "wf_therapist_activity_segment",
    "wf_therapist_daily_summary",
    "wf_processed_event",
)


def _tables():
    # Imported lazily so the migration module stays importable without app settings.
    from app.core.database import Base
    import app.models.workforce  # noqa: F401

    return [Base.metadata.tables[name] for name in _TABLES]


def upgrade() -> None:
    # checkfirst: the API's startup ``create_all`` may already have created them.
    for table in _tables():
        table.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for table in reversed(_tables()):
        table.drop(bind=op.get_bind(), checkfirst=True)
