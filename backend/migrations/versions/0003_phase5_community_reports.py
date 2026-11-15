"""Phase 5: community_reports, the fire alerts people file on the reports page.

Spec: docs/processing/ARCHITECTURE.md §10 (amendment 2026-11-15). The API's /reports endpoints are
the only writers; they never touch processing or approved tables (I1). Ids are server-generated
UUIDs. `reviewed_at` is set exactly when the status is confirmed or false.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OBSERVED = ("smoke", "flames", "both", "unsure")
STATUSES = ("pending", "confirmed", "false")
NOTE_MAX = 280


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "community_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("lat", sa.Double, nullable=False),
        sa.Column("lon", sa.Double, nullable=False),
        sa.Column("observed", sa.Text, nullable=False),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("lat BETWEEN -90 AND 90 AND lon BETWEEN -180 AND 180",
                           name="community_reports_position_check"),
        sa.CheckConstraint(_in("observed", OBSERVED), name="community_reports_observed_check"),
        sa.CheckConstraint(_in("status", STATUSES), name="community_reports_status_check"),
        sa.CheckConstraint(f"char_length(note) <= {NOTE_MAX}", name="community_reports_note_check"),
        sa.CheckConstraint("(status = 'pending') = (reviewed_at IS NULL)", name="community_reports_reviewed_check"),
    )
    op.create_index("community_reports_created_at", "community_reports", [sa.text("created_at DESC"), "id"])


def downgrade() -> None:
    op.drop_table("community_reports")
