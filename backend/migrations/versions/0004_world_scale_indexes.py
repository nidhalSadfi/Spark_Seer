"""World scale (spec §16): indexes for GET /fires/live on millions of raw_detections.

- raw_detections_nrt_window (acq_date, acq_time, latitude, longitude) WHERE product = 'NRT': the
  window's days are a range on its first column, the bbox and the exact window are checked inside
  the index (an index-only scan for the count and the grid without a source filter), and the
  newest NRT detection is its last entry.
- raw_detections_nrt_source_date (source, acq_date) WHERE product = 'NRT': the newest date per
  source (the response's `sources`) and a source-filtered newest detection.

Indexes only; no data changes. On an existing large table, building them takes a while and locks
raw_detections against writes meanwhile (run it between polls).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("raw_detections_nrt_window", "raw_detections", ["acq_date", "acq_time", "latitude", "longitude"],
                    postgresql_where=sa.text("product = 'NRT'"))
    op.create_index("raw_detections_nrt_source_date", "raw_detections", ["source", "acq_date"],
                    postgresql_where=sa.text("product = 'NRT'"))


def downgrade() -> None:
    op.drop_index("raw_detections_nrt_source_date", table_name="raw_detections")
    op.drop_index("raw_detections_nrt_window", table_name="raw_detections")
