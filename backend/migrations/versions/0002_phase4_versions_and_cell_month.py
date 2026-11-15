"""Phase 4 tables: calibration and dataset versions, approved / staging / history cell-months,
llm_attempts, sensor_day_coverage; job columns for the decision and processing counts.

Spec: docs/processing/ARCHITECTURE.md §8 (tables, promotion), §7 (llm_attempts) and §5 (row
columns). `community_reports` is not created here: another phase owns it and chains after this.

Cell-month columns are a superset of §8: `days` is the number of distinct days behind the
harmonized value (MODIS days before 2012, the VIIRS reference days from 2012); `modis_days`,
`viirs_days`, `reference` and the per-family raw counts (`n_snpp`, `n_noaa20`, `n_noaa21`) come
from processing.harmonize.STAGED_COLUMNS. NULL means unknown, never zero (I7).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CALIBRATION_STATUSES = ("proposed", "approved", "superseded", "rejected")
DATASET_STATUSES = ("approved", "superseded")
AI_REVIEW = ("completed", "unavailable", "disabled")
LLM_OUTCOMES = ("ok", "timeout", "rate_limited", "quota_exhausted", "http_error", "invalid_output", "error")
COVERAGE_STATUSES = ("available", "missing", "unknown")
REFERENCES = ("MODIS*k", "SNPP", "NOAA20", "mixed")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _ts(name: str, nullable: bool = True, **kw) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, **kw)


def _id() -> sa.Column:
    return sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True)


def _cell_columns() -> list[sa.Column]:
    """Key and value columns shared by cell_month, cell_month_staging and cell_month_history."""
    return [
        sa.Column("cy", sa.Integer, nullable=False),
        sa.Column("cx", sa.Integer, nullable=False),
        sa.Column("year", sa.SmallInteger, nullable=False),
        sa.Column("month", sa.SmallInteger, nullable=False),
        sa.Column("modis", sa.Integer),            # filtered MODIS detections; NULL = no MODIS day fetched
        sa.Column("viirs_ref", sa.Double),         # VIIRS reference in S-NPP terms; NULL before 2012 or unknown
        sa.Column("harmonized", sa.Double),        # NULL with a flag when its input is unknown (I7)
        sa.Column("k", sa.Double, nullable=False),
        sa.Column("support", sa.Double, nullable=False),
        sa.Column("days", sa.Integer),             # distinct days behind the harmonized value
        sa.Column("modis_days", sa.Integer),
        sa.Column("viirs_days", sa.Integer),
        sa.Column("reference", sa.Text),           # MODIS*k | SNPP | NOAA20 | mixed
        sa.Column("n_snpp", sa.Integer),
        sa.Column("n_noaa20", sa.Integer),
        sa.Column("n_noaa21", sa.Integer),
        sa.Column("is_partial", sa.Boolean, nullable=False),
        sa.Column("quality_flags", ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")),
        sa.Column("calibration_version_id", sa.BigInteger, sa.ForeignKey("calibration_versions.id"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "calibration_versions",
        _id(),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("label", sa.Text, nullable=False),
        # k per (cy, cx, calendar month) lives in calibration_k; this names where it came from.
        sa.Column("k_table_ref", sa.Text, nullable=False),
        sa.Column("k_month", JSONB, nullable=False),             # {"1": k, ...} regional k per calendar month
        sa.Column("fingerprint", sa.Text, nullable=False),       # processing.calibration.Calibration.fingerprint
        sa.Column("snpp_end", sa.Date),                          # last day of S-NPP coverage
        sa.Column("r", JSONB),                                   # {"1": r, ...} S-NPP / NOAA-20 factor applied
        sa.Column("r_global", sa.Double),
        sa.Column("mask_version", sa.Text),                      # static-source mask version (NULL = no mask)
        sa.Column("mask_sha256", sa.Text),                       # its content_sha256
        sa.Column("validation_report", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("created_at", nullable=False),
        _ts("approved_at"),
        sa.CheckConstraint(_in("status", CALIBRATION_STATUSES), name="calibration_versions_status_check"),
        sa.CheckConstraint("(r IS NULL) = (r_global IS NULL)", name="calibration_versions_r_check"),
    )
    op.create_index("calibration_versions_one_approved", "calibration_versions", ["status"], unique=True,
                    postgresql_where=sa.text("status = 'approved'"))

    op.create_table(
        "calibration_k",
        sa.Column("calibration_version_id", sa.BigInteger,
                  sa.ForeignKey("calibration_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cy", sa.Integer, nullable=False),
        sa.Column("cx", sa.Integer, nullable=False),
        sa.Column("month", sa.SmallInteger, nullable=False),
        sa.Column("k", sa.Double, nullable=False),
        sa.Column("support", sa.Double, nullable=False),
        sa.PrimaryKeyConstraint("calibration_version_id", "cy", "cx", "month", name="calibration_k_pkey"),
    )

    op.create_table(
        "dataset_versions",
        _id(),
        sa.Column("parent_id", sa.BigInteger, sa.ForeignKey("dataset_versions.id")),
        sa.Column("job_id", sa.BigInteger, sa.ForeignKey("processing_jobs.id")),  # NULL for the bootstrap
        sa.Column("calibration_version_id", sa.BigInteger, sa.ForeignKey("calibration_versions.id"), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        _ts("created_at", nullable=False),
        _ts("approved_at"),
        sa.Column("summary", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint(_in("status", DATASET_STATUSES), name="dataset_versions_status_check"),
    )
    op.create_index("dataset_versions_one_approved", "dataset_versions", ["status"], unique=True,
                    postgresql_where=sa.text("status = 'approved'"))

    op.create_table(
        "cell_month",
        *_cell_columns(),
        sa.Column("dataset_version_id", sa.BigInteger, sa.ForeignKey("dataset_versions.id"), nullable=False),
        sa.PrimaryKeyConstraint("cy", "cx", "year", "month", name="cell_month_pkey"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="cell_month_month_check"),
        sa.CheckConstraint("modis >= 0 AND viirs_ref >= 0 AND harmonized >= 0 AND days >= 0 AND modis_days >= 0 "
                           "AND viirs_days >= 0 AND n_snpp >= 0 AND n_noaa20 >= 0 AND n_noaa21 >= 0",
                           name="cell_month_non_negative"),
        sa.CheckConstraint(f"reference IS NULL OR {_in('reference', REFERENCES)}", name="cell_month_reference_check"),
    )
    op.create_index("cell_month_dataset_version", "cell_month", ["dataset_version_id"])

    # Staging has no range checks and no unique key: a run whose gates failed is still stored as it
    # was computed (rejected runs are kept for inspection, §8); promotion requires the gates.
    op.create_table(
        "cell_month_staging",
        _id(),
        sa.Column("job_id", sa.BigInteger, sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False),
        *_cell_columns(),
        _ts("created_at", nullable=False),
    )
    op.create_index("cell_month_staging_job_key", "cell_month_staging", ["job_id", "cy", "cx", "year", "month"])

    op.create_table(
        "cell_month_history",
        _id(),
        *_cell_columns(),
        sa.Column("dataset_version_id", sa.BigInteger, sa.ForeignKey("dataset_versions.id"), nullable=False),
        sa.Column("replaced_by_version", sa.BigInteger, sa.ForeignKey("dataset_versions.id"), nullable=False),
        _ts("replaced_at", nullable=False),
    )
    op.create_index("cell_month_history_replaced_by", "cell_month_history", ["replaced_by_version"])
    op.create_index("cell_month_history_key", "cell_month_history", ["cy", "cx", "year", "month"])

    op.create_table(
        "llm_attempts",
        _id(),
        sa.Column("job_id", sa.BigInteger, sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step", sa.Text, nullable=False),
        sa.Column("attempt_no", sa.SmallInteger, nullable=False),   # 1 = first model tried
        sa.Column("model", sa.Text, nullable=False),
        _ts("started_at", nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("error_class", sa.Text),
        sa.Column("error", sa.Text),                                 # short and redacted (worker.llm)
        sa.Column("prompt_tokens", sa.Integer),
        sa.Column("completion_tokens", sa.Integer),
        sa.Column("total_tokens", sa.Integer),
        sa.CheckConstraint(_in("outcome", LLM_OUTCOMES), name="llm_attempts_outcome_check"),
        sa.UniqueConstraint("job_id", "step", "attempt_no", name="llm_attempts_job_step_attempt"),
    )

    op.create_table(
        "sensor_day_coverage",
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("fetch_id", sa.BigInteger, sa.ForeignKey("firms_fetches.id", ondelete="SET NULL")),
        _ts("updated_at", nullable=False),
        sa.PrimaryKeyConstraint("source", "day", name="sensor_day_coverage_pkey"),
        sa.CheckConstraint(_in("status", COVERAGE_STATUSES), name="sensor_day_coverage_status_check"),
    )

    # processing_jobs: dataset_version_id, ai_review and affected_count exist since 0001.
    op.create_foreign_key("processing_jobs_dataset_version_id_fkey", "processing_jobs", "dataset_versions",
                          ["dataset_version_id"], ["id"])
    op.add_column("processing_jobs", sa.Column("calibration_version_id", sa.BigInteger,
                                               sa.ForeignKey("calibration_versions.id",
                                                             name="processing_jobs_calibration_version_id_fkey")))
    op.add_column("processing_jobs", sa.Column("detections_count", sa.Integer))  # loaded for the affected keys
    op.add_column("processing_jobs", sa.Column("staged_count", sa.Integer))      # rows written to staging
    # The decision record for the API (§10 /processing/jobs/{id}): decision, basis, ai_review,
    # gates_passed, the gate results, soft flags and the Validation reviewer's verdict
    # ({decision, reasons, flagged_keys}) or null.
    op.add_column("processing_jobs", sa.Column("decision", JSONB))
    op.create_check_constraint("processing_jobs_ai_review_check", "processing_jobs",
                               f"ai_review IS NULL OR {_in('ai_review', AI_REVIEW)}")


def downgrade() -> None:
    op.drop_constraint("processing_jobs_ai_review_check", "processing_jobs", type_="check")
    for column in ("decision", "staged_count", "detections_count", "calibration_version_id"):
        op.drop_column("processing_jobs", column)
    op.drop_constraint("processing_jobs_dataset_version_id_fkey", "processing_jobs", type_="foreignkey")
    for table in ("sensor_day_coverage", "llm_attempts", "cell_month_history", "cell_month_staging", "cell_month",
                  "dataset_versions", "calibration_k", "calibration_versions"):
        op.drop_table(table)
