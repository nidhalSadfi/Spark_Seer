"""Phase 4 persistence (spec §8): calibration versions, staging, atomic promotion, LLM attempts.

Explicit SQL, like the rest of the worker. Every number written here was computed by
`processing/` (Python); this module only moves rows.

Promotion (`promote`) is one transaction: copy the approved rows being replaced into
cell_month_history, upsert the staging rows into cell_month, insert the new dataset_versions row
and supersede the previous one, mark the job `succeeded`, and clear `needs_processing` for the
detections the run processed (§6). Any exception rolls all of it back (I2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Mapping, Sequence

import pandas as pd
import pyarrow as pa
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

import pgcopy
from processing.calibration import Calibration
from processing.common import REF_MODIS, jsonable

# Value columns of cell_month / cell_month_staging / cell_month_history, after the key.
KEY = ("cy", "cx", "year", "month")
VALUES = ("modis", "viirs_ref", "harmonized", "k", "support", "days", "modis_days", "viirs_days", "reference",
          "n_snpp", "n_noaa20", "n_noaa21", "is_partial", "quality_flags", "calibration_version_id")
CELL_COLUMNS = KEY + VALUES


class StoreError(RuntimeError):
    """The database is not in the state this operation needs (e.g. no approved calibration)."""


def _cols(columns: Iterable[str], prefix: str = "") -> str:
    return ", ".join(f"{prefix}{c}" for c in columns)


# ----------------------------------------------------------------------------- calibration

@dataclass(frozen=True)
class CalibrationVersion:
    id: int
    calibration: Calibration
    mask_version: str | None
    mask_sha256: str | None


def insert_calibration(c: Connection, cal: Calibration, *, status: str, label: str, k_table_ref: str,
                       mask_version: str | None, mask_sha256: str | None, validation_report: Mapping,
                       now: datetime) -> int:
    """Store a calibration and its k table; returns its id. `cal.version` is not stored (the id is the version)."""
    cal_id = c.scalar(text("""
        INSERT INTO calibration_versions (status, label, k_table_ref, k_month, fingerprint, snpp_end, r, r_global,
                                          mask_version, mask_sha256, validation_report, created_at, approved_at)
        VALUES (:status, :label, :ref, CAST(:k_month AS jsonb), '', :snpp_end, CAST(:r AS jsonb), :r_global,
                :mask_version, :mask_sha256, CAST(:report AS jsonb), :now, :approved_at)
        RETURNING id"""),
        {"status": status, "label": label, "ref": k_table_ref,
         "k_month": json.dumps({str(m): v for m, v in sorted(cal.k_month.items())}),
         "snpp_end": cal.snpp_end, "r": json.dumps({str(m): v for m, v in sorted(cal.r.items())}) if cal.r else None,
         "r_global": cal.r_global, "mask_version": mask_version, "mask_sha256": mask_sha256,
         "report": json.dumps(jsonable(dict(validation_report)), sort_keys=True), "now": now,
         "approved_at": now if status == "approved" else None})
    kt = cal.k_table
    if len(kt):  # COPY: a world k table has hundreds of thousands of rows (spec §16)
        pgcopy.write_table(c, "calibration_k", pd.DataFrame({
            "calibration_version_id": cal_id, "cy": kt.cy.astype("int64"), "cx": kt.cx.astype("int64"),
            "month": kt.month.astype("int64"), "k": kt.k.astype("float64"), "support": kt.support.astype("float64")}))
    # The fingerprint of the calibration as it reads back (version = id), so load_calibration can verify it.
    stored = _calibration_from_rows(c, cal_id)
    c.execute(text("UPDATE calibration_versions SET fingerprint = :fp WHERE id = :id"),
              {"fp": stored.fingerprint, "id": cal_id})
    return cal_id


def _calibration_from_rows(c: Connection, cal_id: int) -> Calibration:
    row = c.execute(text("SELECT * FROM calibration_versions WHERE id = :id"), {"id": cal_id}).one()
    k = pgcopy.read_frame(c, "SELECT cy, cx, month, k, support FROM calibration_k "
                             "WHERE calibration_version_id = %(id)s ORDER BY cy, cx, month", {"id": int(cal_id)},
                          {"cy": pa.int64(), "cx": pa.int64(), "month": pa.int64(), "k": pa.float64(),
                           "support": pa.float64()})
    r = {int(m): float(v) for m, v in row.r.items()} if row.r is not None else None
    return Calibration(version=str(cal_id), k_table=k, k_month={int(m): float(v) for m, v in row.k_month.items()},
                       snpp_end=row.snpp_end, r=r, r_global=row.r_global, source=row.k_table_ref)


def load_approved_calibration(c: Connection) -> CalibrationVersion:
    """The approved calibration version (I6: firms_poll uses it as is, never refits)."""
    row = c.execute(text("SELECT id, fingerprint, mask_version, mask_sha256 FROM calibration_versions "
                         "WHERE status = 'approved'")).one_or_none()
    if row is None:
        raise StoreError("no approved calibration version: run `python -m worker.bootstrap` from backend/ first")
    cal = _calibration_from_rows(c, row.id)
    if cal.fingerprint != row.fingerprint:
        raise StoreError(f"calibration version {row.id} does not match its stored fingerprint "
                         "(its k table or factors were edited)")
    return CalibrationVersion(row.id, cal, row.mask_version, row.mask_sha256)


def approved_dataset_version(c: Connection, *, lock: bool = False) -> int | None:
    sql = "SELECT id FROM dataset_versions WHERE status = 'approved'" + (" FOR UPDATE" if lock else "")
    return c.scalar(text(sql))


# ----------------------------------------------------------------------------- staging

def cell_rows(staged: pd.DataFrame, calibration_version_id: int) -> list[dict]:
    """Staged rows (processing.harmonize.STAGED_COLUMNS) as cell_month column dicts.

    `days` is the day count behind the harmonized value: MODIS days before 2012 (reference
    "MODIS*k"), the VIIRS reference days from 2012. Nulls stay null (I7).
    """
    out = []
    for r in jsonable(staged.to_dict(orient="records")):
        row = {c: r.get(c) for c in CELL_COLUMNS if c not in ("days", "calibration_version_id")}
        row["days"] = r.get("modis_days") if r.get("reference") == REF_MODIS else r.get("viirs_days")
        row["quality_flags"] = list(r.get("quality_flags") or [])
        row["calibration_version_id"] = calibration_version_id
        out.append(row)
    return out


def _value_params() -> str:
    return ", ".join("CAST(:quality_flags AS text[])" if c == "quality_flags" else f":{c}" for c in CELL_COLUMNS)


def write_staging(c: Connection, job_id: int, rows: Sequence[dict], now: datetime) -> int:
    if rows:
        c.execute(text(f"INSERT INTO cell_month_staging (job_id, {_cols(CELL_COLUMNS)}, created_at) "
                       f"VALUES (:job_id, {_value_params()}, :now)"),
                  [{**r, "job_id": job_id, "now": now} for r in rows])
    return len(rows)


def insert_cell_month(c: Connection, rows: Sequence[dict], dataset_version_id: int) -> int:
    """Plain insert into the approved table (bootstrap only; runs go through promote)."""
    if rows:
        c.execute(text(f"INSERT INTO cell_month ({_cols(CELL_COLUMNS)}, dataset_version_id) "
                       f"VALUES ({_value_params()}, :dataset_version_id)"),
                  [{**r, "dataset_version_id": dataset_version_id} for r in rows])
    return len(rows)


def copy_cell_month(c: Connection, frame: pd.DataFrame, dataset_version_id: int) -> int:
    """COPY a frame of cell_month rows (CELL_COLUMNS; quality_flags as text[] literals such as "{}")
    into the approved table: the bootstrap of a world dataset (spec §16), millions of rows."""
    out = frame[list(CELL_COLUMNS)].copy()
    out["dataset_version_id"] = int(dataset_version_id)
    return pgcopy.write_table(c, "cell_month", out)


def prune_staging(c: Connection, older_than: datetime) -> int:
    """Retention (§8): drop staging rows of jobs that finished before `older_than`."""
    return c.execute(text("""
        DELETE FROM cell_month_staging AS s USING processing_jobs AS j
        WHERE s.job_id = j.id AND j.finished_at IS NOT NULL AND j.finished_at < :t"""), {"t": older_than}).rowcount


# ----------------------------------------------------------------------------- promotion

@dataclass(frozen=True)
class Promotion:
    dataset_version_id: int
    parent_id: int
    inserted: int
    updated: int
    history_rows: int
    cleared_pending: int

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def promote(engine: Engine, *, job_id: int, worker_id: str, calibration_version_id: int,
            processed: Sequence[tuple[int, str]] | pd.DataFrame, summary: Mapping, now: datetime) -> Promotion:
    """Publish job `job_id`'s staging rows in ONE transaction (spec §8); any exception rolls back all.

    `processed`: (raw_detections.id, content_hash) of the pending rows the run was computed from
    (pairs, or a frame with columns id and content_hash); those still carrying that hash get
    needs_processing = false. A row modified again since keeps its flag and is processed by the
    next run.
    """
    with engine.begin() as c:
        return _promote(c, job_id=job_id, worker_id=worker_id, calibration_version_id=calibration_version_id,
                        processed=processed, summary=summary, now=now)


def _promote(c: Connection, *, job_id, worker_id, calibration_version_id, processed, summary, now) -> Promotion:
    owned = c.scalar(text("SELECT id FROM processing_jobs WHERE id = :id AND status = 'running' "
                          "AND lease_owner = :owner FOR UPDATE"), {"id": job_id, "owner": worker_id})
    if owned is None:
        raise StoreError(f"job {job_id} is no longer running under {worker_id}: not promoted")
    parent = approved_dataset_version(c, lock=True)
    if parent is None:
        raise StoreError("no approved dataset version: run `python -m worker.bootstrap` from backend/ first")
    version = _new_version(c, parent, job_id, calibration_version_id, summary, now)
    history = _copy_history(c, job_id, version, now)
    inserted, updated = _upsert(c, job_id, version)
    _mark_succeeded(c, job_id, version, now)
    cleared = _clear_pending(c, processed)
    promotion = Promotion(version, parent, inserted, updated, history, cleared)
    c.execute(text("UPDATE dataset_versions SET summary = summary || CAST(:extra AS jsonb) WHERE id = :id"),
              {"id": version, "extra": json.dumps(promotion.to_dict(), sort_keys=True)})
    return promotion


def _new_version(c: Connection, parent: int, job_id: int, calibration_version_id: int, summary: Mapping,
                 now: datetime) -> int:
    c.execute(text("UPDATE dataset_versions SET status = 'superseded' WHERE id = :id"), {"id": parent})
    return c.scalar(text("""
        INSERT INTO dataset_versions (parent_id, job_id, calibration_version_id, status, created_at, approved_at,
                                      summary)
        VALUES (:parent, :job_id, :cal, 'approved', :now, :now, CAST(:summary AS jsonb)) RETURNING id"""),
        {"parent": parent, "job_id": job_id, "cal": calibration_version_id, "now": now,
         "summary": json.dumps(jsonable(dict(summary)), sort_keys=True)})


def _copy_history(c: Connection, job_id: int, version: int, now: datetime) -> int:
    return c.execute(text(f"""
        INSERT INTO cell_month_history ({_cols(CELL_COLUMNS)}, dataset_version_id, replaced_by_version, replaced_at)
        SELECT {_cols(CELL_COLUMNS, 'a.')}, a.dataset_version_id, :version, :now
        FROM cell_month AS a
        WHERE EXISTS (SELECT 1 FROM cell_month_staging AS s WHERE s.job_id = :job_id AND s.cy = a.cy
                      AND s.cx = a.cx AND s.year = a.year AND s.month = a.month)"""),
        {"job_id": job_id, "version": version, "now": now}).rowcount


def _upsert(c: Connection, job_id: int, version: int) -> tuple[int, int]:
    rows = c.execute(text(f"""
        INSERT INTO cell_month ({_cols(CELL_COLUMNS)}, dataset_version_id)
        SELECT {_cols(CELL_COLUMNS)}, :version FROM cell_month_staging WHERE job_id = :job_id
        ON CONFLICT (cy, cx, year, month) DO UPDATE SET
            {', '.join(f'{v} = EXCLUDED.{v}' for v in VALUES)}, dataset_version_id = EXCLUDED.dataset_version_id
        RETURNING (xmax = 0) AS inserted"""), {"job_id": job_id, "version": version}).all()
    inserted = sum(1 for r in rows if r.inserted)
    return inserted, len(rows) - inserted


def _mark_succeeded(c: Connection, job_id: int, version: int, now: datetime) -> None:
    c.execute(text("UPDATE processing_jobs SET status = 'succeeded', dataset_version_id = :version, "
                   "heartbeat_at = :now WHERE id = :id"), {"id": job_id, "version": version, "now": now})


def _clear_pending(c: Connection, processed: Sequence[tuple[int, str]] | pd.DataFrame) -> int:
    """needs_processing = false for the (id, content_hash) pairs still unchanged. The pairs go in
    with COPY (a world run clears millions, spec §16), into a temp table dropped at commit."""
    frame = processed[["id", "content_hash"]] if isinstance(processed, pd.DataFrame) else \
        pd.DataFrame(list(processed), columns=["id", "content_hash"])
    if frame.empty:
        return 0
    c.execute(text("CREATE TEMP TABLE processed_detections (id bigint NOT NULL, content_hash text NOT NULL) "
                   "ON COMMIT DROP"))
    pgcopy.write_table(c, "processed_detections", frame.astype({"id": "int64"}))
    c.execute(text("ANALYZE processed_detections"))
    return c.execute(text("""
        UPDATE raw_detections AS r SET needs_processing = false
        FROM processed_detections AS p
        WHERE r.id = p.id AND r.content_hash = p.content_hash AND r.needs_processing""")).rowcount


# ----------------------------------------------------------------------------- job records

def insert_steps(c: Connection, job_id: int, steps: Sequence[Mapping]) -> None:
    """One job_steps row per flow step record of worker.review ({name, status, started_at, finished_at, summary})."""
    status = {"ok": "succeeded", "failed": "failed", "running": "failed"}
    if steps:
        c.execute(text("INSERT INTO job_steps (job_id, name, status, started_at, finished_at, summary) "
                       "VALUES (:job_id, :name, :status, :started_at, :finished_at, CAST(:summary AS jsonb))"),
                  [{"job_id": job_id, "name": s["name"], "status": status.get(s["status"], s["status"]),
                    "started_at": s["started_at"], "finished_at": s.get("finished_at"),
                    "summary": json.dumps(jsonable(s.get("summary") or {}), sort_keys=True)} for s in steps])


def insert_attempts(c: Connection, job_id: int, step: str, attempts: Sequence[Mapping]) -> int:
    """Every LLM attempt (worker.llm.AttemptRecord.to_dict()) of one step, numbered from 1."""
    if attempts:
        c.execute(text("""
            INSERT INTO llm_attempts (job_id, step, attempt_no, model, started_at, latency_ms, outcome, error_class,
                                      error, prompt_tokens, completion_tokens, total_tokens)
            VALUES (:job_id, :step, :n, :model, :started_at, :latency_ms, :outcome, :error_class, :error,
                    :prompt_tokens, :completion_tokens, :total_tokens)"""),
            [{**{k: a.get(k) for k in ("model", "started_at", "latency_ms", "outcome", "error_class", "error",
                                         "prompt_tokens", "completion_tokens", "total_tokens")},
              "job_id": job_id, "step": step, "n": n} for n, a in enumerate(attempts, 1)])
    return len(attempts)


def record_sensor_days(c: Connection, source: str, start: date, days: int, max_date: date | None,
                       fetch_id: int, now: datetime) -> None:
    """sensor_day_coverage for one stored fetch window: a day is `available` when FIRMS reported
    data up to it (availability max_date), else `unknown` (not yet published, or a gap)."""
    rows = []
    for i in range(days):
        day = date.fromordinal(start.toordinal() + i)
        rows.append({"source": source, "day": day, "fetch_id": fetch_id, "now": now,
                     "status": "available" if max_date is not None and day <= max_date else "unknown"})
    c.execute(text("""
        INSERT INTO sensor_day_coverage (source, day, status, fetch_id, updated_at)
        VALUES (:source, :day, :status, :fetch_id, :now)
        ON CONFLICT (source, day) DO UPDATE SET status = EXCLUDED.status, fetch_id = EXCLUDED.fetch_id,
            updated_at = EXCLUDED.updated_at"""), rows)
