"""firms_poll steps 4-7 (spec §6): pending rows -> affected cell-months -> all their detections ->
the CrewAI processing run (worker.review.run_processing) -> staging -> promote, hold or reject.

Called only when there is something to process (new, modified or pending rows), so the I3 check
stays in firms_poll and runs before anything here. Importing this module does not import crewai
(worker.review imports it only inside a run with OPENROUTER_MODELS set).

All numbers come from processing/ (Python). This module reads the database, maps rows to the
DataFrame run_processing expects, and persists what the run returns: one job_steps row per flow
step, every LLM attempt, the decision, the staging rows and, for an approval, the promotion.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, timedelta
from typing import Mapping

import numpy as np
import pandas as pd
import pyarrow as pa
from sqlalchemy import text
from sqlalchemy.engine import Engine

import pgcopy
from processing.boundary import load_boundary
from processing.common import KEY_COLS, family_of, jsonable, month_bounds
from processing.grid import cell_index, with_keys
from processing.mask import load_mask
from processing.params import DEFAULT_PARAMS, ProcessingParams
from worker import store
from worker.jobs import JobContext, JobError, to_json
from worker.review import ProcessingFailed, run_processing

log = logging.getLogger("worker.process_pending")

# Columns of the DataFrame handed to run_processing (the shape of tests/processing_synth.frame).
DETECTION_COLUMNS = ["source", "product", "satellite", "latitude", "longitude", "acq_date", "acq_time", "confidence",
                     "type", "instrument"]
RECENT_JOBS = 10        # successful runs averaged for the "drop share far above its recent level" flag
_PAD_DEG = 0.01         # SQL pre-selection margin; Python decides cell membership exactly
STATUS_FOR = {"approve": "succeeded", "hold": "held", "reject": "rejected"}


def params_for(settings) -> ProcessingParams:
    """The default science with the deployment's drift thresholds and latitude cut (processing/params.py;
    PROCESSING_MIN_LAT=none for the world, spec §16)."""
    return replace(DEFAULT_PARAMS, drift_warn_pct=settings.drift_warn_pct, drift_min_count=settings.drift_min_count,
                   min_lat=settings.processing_min_lat)


# ----------------------------------------------------------------------------- inputs

PENDING_TYPES = {"id": pa.int64(), "content_hash": pa.string(), "latitude": pa.float64(), "longitude": pa.float64(),
                 "acq_date": pa.date32()}
DETECTION_TYPES = {"source": pa.string(), "product": pa.string(), "satellite": pa.string(), "latitude": pa.float64(),
                   "longitude": pa.float64(), "acq_date": pa.date32(), "acq_time": pa.string(),
                   "confidence": pa.string(), "type": pa.int16(), "instrument": pa.string()}


def load_pending(engine: Engine) -> pd.DataFrame:
    """id, content_hash, latitude, longitude, acq_date of every pending detection, by id.

    Read with COPY (spec §16: millions of rows on a world poll); the hashes stay in Arrow memory
    and acq_date is datetime64. Promotion clears exactly these (id, content_hash) pairs.
    """
    with engine.connect() as c:
        return pgcopy.read_frame(c, "SELECT id, content_hash, latitude, longitude, acq_date FROM raw_detections "
                                    "WHERE needs_processing ORDER BY id", None, PENDING_TYPES,
                                 arrow_strings=("content_hash",))


def affected_keys(pending: pd.DataFrame, params: ProcessingParams = DEFAULT_PARAMS) -> pd.DataFrame:
    """Distinct (cy, cx, year, month) of the pending rows (spec §6 step 4), with processing.grid's
    cell rule, so the keys are exactly the cell-months the run aggregates."""
    if pending.empty:
        return pd.DataFrame(columns=KEY_COLS, dtype="int64")
    keyed = with_keys(pending[["latitude", "longitude", "acq_date"]], params)
    return keyed[KEY_COLS].drop_duplicates().sort_values(KEY_COLS).reset_index(drop=True).astype("int64")


# Per affected month: the month's date range, the affected cells' bounding box (padded) and the
# affected cells themselves, as cell codes cy * CELL_CODE + cx (SQL's floor(deg / cell_deg) on
# float8 is the same IEEE operation as processing.grid.cell_index; the codes are whole numbers far
# below 2^53, exact in float8). The codes are a constant array (psycopg merges COPY parameters
# client-side), which PostgreSQL >= 14 probes with a hash table: one lookup per row, never a
# join plan that could rescan the table per cell. All of it only pre-selects: the exact
# membership is decided again in Python with processing.grid's rule (_inside_keys).
CELL_CODE = 1 << 20
_DETECTIONS_SQL = """
    SELECT r.source, r.product, r.satellite, r.latitude, r.longitude, r.acq_date, r.acq_time_raw AS acq_time,
           r.confidence, r."type", r.instrument
    FROM raw_detections AS r
    WHERE r.acq_date BETWEEN %(first)s AND %(last)s AND r.latitude >= %(lat0)s AND r.latitude < %(lat1)s
      AND r.longitude >= %(lon0)s AND r.longitude < %(lon1)s
      AND floor(r.latitude / CAST(%(deg)s AS double precision)) * %(code)s
          + floor(r.longitude / CAST(%(deg)s AS double precision)) = ANY(CAST(%(cells)s AS double precision[]))"""


def load_detections(engine: Engine, keys: pd.DataFrame, params: ProcessingParams = DEFAULT_PARAMS) -> pd.DataFrame:
    """ALL stored detections (any source, product or flag) of the affected cell-months (§6 step 5).

    Per month, SQL pre-selects the date range, the cells' bounding box (padded) and the affected
    cells, and streams the rows with COPY (spec §16: a world month holds millions); the exact
    cell membership is then decided in Python with the same rule as processing.grid. Columns are
    DETECTION_COLUMNS; acq_date is datetime64, `type` nullable Int64.
    """
    frames = []
    with engine.connect() as c:
        for (year, month), group in keys.groupby(["year", "month"]):
            first, last = month_bounds(int(year), int(month))
            cells = group[["cy", "cx"]].drop_duplicates()
            frame = pgcopy.read_frame(c, _DETECTIONS_SQL, {
                "first": first, "last": last, "deg": params.cell_deg, "code": float(CELL_CODE),
                "cells": (cells.cy.astype("int64") * CELL_CODE + cells.cx.astype("int64")).astype(float).tolist(),
                "lat0": group.cy.min() * params.cell_deg - _PAD_DEG, "lat1": (group.cy.max() + 1) * params.cell_deg + _PAD_DEG,
                "lon0": group.cx.min() * params.cell_deg - _PAD_DEG, "lon1": (group.cx.max() + 1) * params.cell_deg + _PAD_DEG,
            }, DETECTION_TYPES, nullable_int=("type",))
            if len(frame):
                frames.append(frame[_inside_keys(frame, group, params)])
    if not frames:
        return _detection_frame(pgcopy.empty_frame(DETECTION_TYPES, nullable_int=("type",)))
    return _detection_frame(pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0])


def _inside_keys(df: pd.DataFrame, keys: pd.DataFrame, params: ProcessingParams) -> np.ndarray:
    """Rows whose (cy, cx, year, month) by processing.grid's rule (as with_keys computes them) is in keys."""
    day = pd.to_datetime(df["acq_date"]).dt.normalize()
    got = pd.DataFrame({"cy": cell_index(df["latitude"], params.cell_deg),
                        "cx": cell_index(df["longitude"], params.cell_deg),
                        "year": day.dt.year.astype("int64").to_numpy(), "month": day.dt.month.astype("int64").to_numpy()})
    want = keys[KEY_COLS].astype("int64").drop_duplicates()
    return got.merge(want, on=KEY_COLS, how="left", indicator=True)["_merge"].eq("both").to_numpy()


def _detection_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df[DETECTION_COLUMNS].reset_index(drop=True)
    out["latitude"] = out["latitude"].astype(float)
    out["longitude"] = out["longitude"].astype(float)
    out["type"] = out["type"].astype("Int64")
    return out


def load_coverage(engine: Engine, bbox: str, keys: pd.DataFrame) -> list[tuple[str, date, int]]:
    """(family, window_start, window_days) of every stored fetch of this bbox that overlaps the
    affected months (spec §5 completeness: fetch coverage, NRT or SP)."""
    if keys.empty:
        return []
    first = min(month_bounds(int(y), int(m))[0] for y, m in zip(keys.year, keys.month))
    last = max(month_bounds(int(y), int(m))[1] for y, m in zip(keys.year, keys.month))
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT source, window_start, window_days FROM firms_fetches
            WHERE bbox = :bbox AND window_start <= :last AND window_start + (window_days - 1) >= :first"""),
            {"bbox": bbox, "first": first, "last": last}).all()
    return [(family_of(r.source), r.window_start, int(r.window_days)) for r in rows if family_of(r.source)]


def family_availability(availability: Mapping[str, tuple[date, date]]) -> dict[str, date]:
    """Family -> latest availability max_date over its sources (NRT and SP), from this poll."""
    out: dict[str, date] = {}
    for source, (_, hi) in availability.items():
        fam = family_of(source)
        if fam is not None and (fam not in out or hi > out[fam]):
            out[fam] = hi
    return dict(sorted(out.items()))


def recent_drop_shares(engine: Engine, job_id: int) -> dict[str, float] | None:
    """Mean clip and mask drop shares of the last RECENT_JOBS successful runs, or None."""
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT s.summary FROM job_steps AS s JOIN processing_jobs AS j ON j.id = s.job_id
            WHERE s.name = 'filter' AND s.status = 'succeeded' AND j.status = 'succeeded' AND j.id <> :id
            ORDER BY j.id DESC LIMIT :n"""), {"id": job_id, "n": RECENT_JOBS}).all()
    out = {}
    for reason in ("outside_country", "static_mask"):
        values = [r.summary["drop_share"][reason] for r in rows
                  if isinstance(r.summary.get("drop_share"), dict) and r.summary["drop_share"].get(reason) is not None]
        if values:
            out[reason] = sum(values) / len(values)
    return out or None


def load_rules(settings, cv: store.CalibrationVersion):
    """Static mask and country boundary from settings. The mask must be the calibration's (I6)."""
    mask = load_mask(settings.static_mask_file) if settings.static_mask_file else None
    if mask is None and cv.mask_version is not None:
        raise JobError(f"calibration version {cv.id} uses static mask {cv.mask_version}; "
                       "set STATIC_MASK_FILE to that mask file")
    if mask is not None and (mask.version != cv.mask_version or (cv.mask_sha256 and mask.content_sha256 != cv.mask_sha256)):
        raise JobError(f"STATIC_MASK_FILE holds mask {mask.version}, but calibration version {cv.id} uses "
                       f"{cv.mask_version or 'no mask'}; the 3-hour job never changes the calibration (I6)")
    boundary = load_boundary(settings.country_boundary_file) if settings.country_boundary_file else None
    return mask, boundary


# ----------------------------------------------------------------------------- the run

def decision_record(outcome) -> dict:
    """What processing_jobs.decision holds for the API: decision, gates, soft flags, the verdict."""
    summaries = {s["name"]: s.get("summary") or {} for s in outcome.steps}
    review, decided = summaries.get("review", {}), summaries.get("decide", {})
    return jsonable({
        "decision": outcome.decision, "basis": decided.get("basis"), "ai_review": outcome.ai_review,
        "gates_passed": outcome.result.passed, "gates": [g.to_dict() for g in outcome.result.gates],
        "soft_flags": list(outcome.result.soft_flags),
        "verdict": outcome.verdict.model_dump(mode="json") if outcome.verdict is not None else None,
        "review": {"model": review.get("model"), "attempts": len(outcome.attempts), "reason": review.get("reason")},
    })


def _update_job(engine: Engine, job_id: int, **fields) -> None:
    sets = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k == "decision" else f"{k} = :{k}" for k in fields)
    with engine.begin() as c:
        c.execute(text(f"UPDATE processing_jobs SET {sets} WHERE id = :id"),
                  {**{k: to_json(v) if k == "decision" else v for k, v in fields.items()}, "id": job_id})


def process_pending(ctx: JobContext, availability: Mapping[str, tuple[date, date]]) -> str:
    """Process every pending detection's cell-months; returns succeeded / held / rejected."""
    settings, engine = ctx.settings, ctx.engine
    params = params_for(settings)
    now = ctx.now()

    with ctx.step("prepare") as step:
        try:
            with engine.connect() as c:
                cv = store.load_approved_calibration(c)
                parent = store.approved_dataset_version(c)
        except store.StoreError as e:
            raise JobError(str(e)) from None
        if parent is None:
            raise JobError("no approved dataset version: run `python -m worker.bootstrap` from backend/ first")
        mask, boundary = load_rules(settings, cv)
        pending = load_pending(engine)
        keys = affected_keys(pending, params)
        detections = load_detections(engine, keys, params)
        coverage = load_coverage(engine, settings.firms_bbox_arg, keys)
        fam_avail = family_availability(availability)
        recent = recent_drop_shares(engine, ctx.job_id)
        with engine.begin() as c:
            pruned = store.prune_staging(c, now - timedelta(days=settings.staging_retention_days))
        ctx.counts.update(affected=len(keys), detections=len(detections))
        step.summary.update(
            calibration_version_id=cv.id, dataset_version_id=parent, pending=len(pending), affected=len(keys),
            affected_months=step_months(keys),
            detections=len(detections), coverage_windows=len(coverage), availability=fam_avail,
            recent_drop_shares=recent, mask=mask.version if mask else None,
            boundary=boundary.source if boundary else None, staging_rows_pruned=pruned,
            review="disabled" if not settings.openrouter_models else f"{len(settings.openrouter_models)} model(s)")
    _update_job(engine, ctx.job_id, affected_count=len(keys), detections_count=len(detections),
                calibration_version_id=cv.id)

    try:
        outcome = run_processing(detections, keys, cv.calibration, mask=mask, boundary=boundary, coverage=coverage,
                                 availability=fam_avail, now=now, params=params, recent_drop_shares=recent,
                                 settings=settings, llm_factory=ctx.llm_factory, quota_check=ctx.quota_check,
                                 utcnow=ctx.now)
    except ProcessingFailed as e:
        with engine.begin() as c:
            store.insert_steps(c, ctx.job_id, e.steps)
        cause = e.__cause__
        raise JobError(f"processing step {e.step!r} failed: {type(cause).__name__}: {cause}") from e

    rows = store.cell_rows(outcome.result.staged, cv.id)
    with engine.begin() as c:
        store.insert_steps(c, ctx.job_id, outcome.steps)
        store.insert_attempts(c, ctx.job_id, "review", outcome.attempts)
    decision = decision_record(outcome)
    _update_job(engine, ctx.job_id, ai_review=outcome.ai_review, decision=decision)

    with ctx.step("stage") as step:
        with engine.begin() as c:
            staged = store.write_staging(c, ctx.job_id, rows, ctx.now())
        ctx.counts["staged"] = staged
        step.summary.update(rows=staged, partial=sum(bool(r["is_partial"]) for r in rows),
                            null_harmonized=sum(r["harmonized"] is None for r in rows))
    _update_job(engine, ctx.job_id, staged_count=staged)

    status = STATUS_FOR[outcome.decision]
    with ctx.step("publish") as step:
        step.summary.update(decision=outcome.decision, ai_review=outcome.ai_review, basis=decision["basis"])
        if outcome.decision != "approve":
            # I2: approved tables untouched; staging kept for review; the rows stay pending (§6).
            step.status = "skipped"
            step.summary.update(note=f"run {status}: approved data unchanged, staging rows kept, "
                                     f"{len(pending)} detection(s) stay pending")
        else:
            summary = {"job_id": ctx.job_id, "decision": outcome.decision, "ai_review": outcome.ai_review,
                       "keys": len(rows), "calibration_version_id": cv.id,
                       "soft_flags": sorted({f["flag"] for f in outcome.result.soft_flags}),
                       "months": step_months(keys)}
            promotion = store.promote(engine, job_id=ctx.job_id, worker_id=ctx.worker_id,
                                      calibration_version_id=cv.id,
                                      processed=pending[["id", "content_hash"]],
                                      summary=summary, now=ctx.now())
            step.summary.update(promotion.to_dict())
    log.info("firms_poll job %s processed: %s", ctx.job_id, status,
             extra={"job_id": ctx.job_id, "kind": ctx.kind, "decision": outcome.decision,
                    "ai_review": outcome.ai_review, "gates_passed": outcome.result.passed})
    return status


def step_months(keys: pd.DataFrame) -> list[str]:
    return sorted({f"{int(y):04d}-{int(m):02d}" for y, m in zip(keys.year, keys.month)})
