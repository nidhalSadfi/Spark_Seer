"""Phase 1 tables: schedules, processing_jobs, job_steps, firms_fetches, firms_availability, raw_detections.

Spec: docs/processing/ARCHITECTURE.md §8 (table ownership) and §9 (scheduler). The remaining
tables (llm_attempts, calibration/dataset versions, cell_month*) belong to Phase 4. All timestamps
are timestamptz and are written in UTC.

Revision ID: 0001
Revises:
Create Date: 2026-11-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JOB_STATUSES = ("queued", "running", "succeeded", "no_change", "held", "rejected", "failed", "abandoned")
STEP_STATUSES = ("running", "succeeded", "skipped", "failed")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _ts(name: str, nullable: bool = True, **kw) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, **kw)


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True)


def upgrade() -> None:
    op.create_table(
        "processing_jobs",
        _id(),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("lease_owner", sa.Text),
        _ts("lease_expires_at"),
        _ts("heartbeat_at"),
        _ts("created_at", nullable=False),
        _ts("started_at"),
        _ts("finished_at"),
        # Row counts of firms_poll (§6); affected_count (cell-months) is filled from Phase 2.
        sa.Column("fetched_count", sa.Integer),
        sa.Column("new_count", sa.Integer),
        sa.Column("modified_count", sa.Integer),
        sa.Column("unchanged_count", sa.Integer),
        sa.Column("pending_count", sa.Integer),
        sa.Column("affected_count", sa.Integer),
        # Filled from Phase 4 (dataset_versions does not exist yet; its foreign key comes with it).
        sa.Column("dataset_version_id", sa.BigInteger),
        sa.Column("ai_review", sa.Text),
        sa.Column("error", sa.Text),
        sa.CheckConstraint(_in("status", JOB_STATUSES), name="processing_jobs_status_check"),
        sa.CheckConstraint(_in("trigger", ("schedule", "manual")), name="processing_jobs_trigger_check"),
    )
    # Invariant I4: at most one active job per kind.
    op.create_index("processing_jobs_one_active_per_kind", "processing_jobs", ["kind"], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.create_index("processing_jobs_kind_created_at", "processing_jobs", ["kind", "created_at"])

    op.create_table(
        "schedules",
        sa.Column("kind", sa.Text, primary_key=True),
        sa.Column("cadence", sa.Text, nullable=False),
        sa.Column("interval_seconds", sa.Integer),   # cadence = 'interval'
        sa.Column("day_of_month", sa.SmallInteger),  # cadence = 'monthly' (runs at 03:00 UTC)
        _ts("next_run_at", nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        _ts("last_claimed_at"),
        sa.Column("last_job_id", sa.BigInteger, sa.ForeignKey("processing_jobs.id", ondelete="SET NULL")),
        _ts("updated_at", nullable=False),
        sa.CheckConstraint(
            "(cadence = 'interval' AND interval_seconds > 0)"
            " OR (cadence = 'monthly' AND day_of_month BETWEEN 1 AND 28)",
            name="schedules_cadence_check"),
    )

    op.create_table(
        "job_steps",
        _id(),
        sa.Column("job_id", sa.BigInteger, sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        _ts("started_at", nullable=False),
        _ts("finished_at"),
        sa.Column("summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint(_in("status", STEP_STATUSES), name="job_steps_status_check"),
    )
    op.create_index("job_steps_job_id", "job_steps", ["job_id", "id"])

    op.create_table(
        "firms_fetches",
        _id(),
        sa.Column("job_id", sa.BigInteger, sa.ForeignKey("processing_jobs.id"), nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("bbox", sa.Text, nullable=False),
        sa.Column("window_start", sa.Date, nullable=False),
        sa.Column("window_days", sa.SmallInteger, nullable=False),
        _ts("fetched_at", nullable=False),
        sa.Column("sha256", sa.Text, nullable=False),  # of the response body (UTF-8), hex
        sa.Column("row_count", sa.Integer, nullable=False),
        sa.Column("raw_path", sa.Text, nullable=False),
        # Availability snapshot of this source at fetch time (same poll's data_availability call).
        sa.Column("availability_min_date", sa.Date),
        sa.Column("availability_max_date", sa.Date),
        sa.CheckConstraint("window_days BETWEEN 1 AND 5", name="firms_fetches_window_days_check"),
    )
    op.create_index("firms_fetches_source_window", "firms_fetches", ["source", "window_start"])
    op.create_index("firms_fetches_job_id", "firms_fetches", ["job_id"])

    op.create_table(
        "firms_availability",
        _id(),
        sa.Column("job_id", sa.BigInteger, sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("min_date", sa.Date, nullable=False),
        sa.Column("max_date", sa.Date, nullable=False),
        _ts("recorded_at", nullable=False),
        sa.UniqueConstraint("job_id", "source", name="firms_availability_job_source"),
    )

    op.create_table(
        "raw_detections",
        _id(),
        # Natural key (§6): raw strings as delivered, trimmed.
        sa.Column("source", sa.Text, nullable=False),         # FIRMS API source id, e.g. VIIRS_NOAA20_NRT
        sa.Column("satellite", sa.Text, nullable=False),
        sa.Column("latitude_raw", sa.Text, nullable=False),
        sa.Column("longitude_raw", sa.Text, nullable=False),
        sa.Column("acq_date_raw", sa.Text, nullable=False),
        sa.Column("acq_time_raw", sa.Text, nullable=False),
        # sha256 over all delivered columns (trimmed names and values) in sorted column-name order.
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("product", sa.Text, nullable=False),
        # Typed copies for querying.
        sa.Column("latitude", sa.Double, nullable=False),
        sa.Column("longitude", sa.Double, nullable=False),
        sa.Column("acq_date", sa.Date, nullable=False),
        sa.Column("acq_time", sa.Time, nullable=False),        # UTC
        sa.Column("instrument", sa.Text),
        sa.Column("confidence", sa.Text),                      # MODIS 0-100, VIIRS l/n/h: kept as text
        sa.Column("version", sa.Text),
        sa.Column("frp", sa.Double),
        sa.Column("daynight", sa.Text),
        sa.Column("type", sa.SmallInteger),                    # SP only; NRT rows have no type
        sa.Column("delivered", JSONB, nullable=False),         # the full delivered row (trimmed strings)
        sa.Column("first_seen_fetch_id", sa.BigInteger, sa.ForeignKey("firms_fetches.id"), nullable=False),
        sa.Column("last_seen_fetch_id", sa.BigInteger, sa.ForeignKey("firms_fetches.id"), nullable=False),
        sa.Column("changed_in_job_id", sa.BigInteger, sa.ForeignKey("processing_jobs.id"), nullable=False),
        # Set when a row is inserted or modified; cleared only by a successful promotion (Phase 4).
        sa.Column("needs_processing", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.CheckConstraint(_in("product", ("NRT", "SP")), name="raw_detections_product_check"),
        sa.UniqueConstraint("source", "satellite", "latitude_raw", "longitude_raw", "acq_date_raw",
                            "acq_time_raw", name="raw_detections_natural_key"),
    )
    op.create_index("raw_detections_pending", "raw_detections", ["id"],
                    postgresql_where=sa.text("needs_processing"))
    op.create_index("raw_detections_source_acq_date", "raw_detections", ["source", "acq_date"])


def downgrade() -> None:
    for table in ("raw_detections", "firms_availability", "firms_fetches", "job_steps", "schedules",
                  "processing_jobs"):
        op.drop_table(table)
