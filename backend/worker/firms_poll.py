"""firms_poll handler: fetch FIRMS NRT windows, keep raw provenance, upsert raw_detections, then
process the pending rows (spec §4, §6).

Rows that are new or modified get needs_processing = true. With no new, modified or pending row the
job ends `no_change` before anything that could import crewai is loaded (I3). Otherwise
worker.process_pending recomputes the affected cell-months with the approved calibration, runs the
review flow and promotes, holds or rejects; only a promotion clears needs_processing.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from firms_tool.area_api import Window, call_cost, windows_for
from firms_tool.sensors import SENSORS, Sensor
from worker.detections import Detection, parse_csv
from worker.jobs import JobContext, JobError, describe
from worker.store import record_sensor_days

log = logging.getLogger("worker.firms_poll")

# NRT source per sensor, in catalogue order: MODIS_NRT, VIIRS_SNPP_NRT, VIIRS_NOAA20_NRT, VIIRS_NOAA21_NRT.
NRT_SOURCES: tuple[tuple[str, Sensor], ...] = tuple((s.api_nrt, s) for s in SENSORS.values())
COUNT_KEYS = ("fetched", "new", "modified", "unchanged", "malformed", "duplicates")


@dataclass(frozen=True)
class SourcePlan:
    source: str
    windows: tuple[Window, ...] = ()
    skipped: str | None = None
    notes: tuple[str, ...] = ()


def plan_source(source: str, sensor: Sensor, today: date, availability: dict[str, tuple[date, date]],
                watermark: date | None, max_backfill_days: int) -> SourcePlan:
    """Windows (<= 5 days) for one NRT source.

    Always today and yesterday (UTC). If the source's watermark (last day covered by a stored fetch)
    is older than yesterday, or the source was never fetched, start earlier: from the watermark,
    but never more than `max_backfill_days` days back (today included) and never before the
    source's NRT availability min_date.
    """
    if sensor.coverage_end and today > sensor.coverage_end:
        reason = f"past catalogue coverage_end {sensor.coverage_end}"
        if source in availability and availability[source][1] > sensor.coverage_end:
            reason += f"; data_availability still reports data up to {availability[source][1]}"
        return SourcePlan(source, skipped=reason)
    if source not in availability:
        return SourcePlan(source, skipped="absent from FIRMS data_availability")
    avail_min, avail_max = availability[source]
    yesterday = today - timedelta(days=1)
    floor = today - timedelta(days=max_backfill_days - 1)
    backfill_from = max(watermark or floor, floor, avail_min)
    start = min(yesterday, backfill_from)
    notes = []
    if watermark is not None and watermark < yesterday and backfill_from > watermark:
        notes.append(f"backfill capped at {backfill_from} (watermark {watermark})")
    if avail_max < yesterday:
        notes.append(f"data_availability max_date {avail_max} is before yesterday; the source may have stalled")
    return SourcePlan(source, tuple(windows_for(source, start, today)), notes=tuple(notes))


def run(ctx: JobContext) -> str:
    settings = ctx.settings
    if not settings.firms_map_key:
        raise JobError("no FIRMS MAP_KEY: set FIRMS_MAP_KEY (or FIRMS_MAP_KEY_1) in the worker's environment")
    client = ctx.client_factory(settings.firms_map_key)
    ctx.redact = client.redact
    bbox = settings.firms_bbox_arg
    today = ctx.now().date()

    with ctx.step("availability") as step:
        availability = client.availability()
        _store_availability(ctx, availability)
        step.summary["sources"] = {s: [lo, hi] for s, (lo, hi) in sorted(availability.items())}
        if not availability:
            step.summary["note"] = "data_availability returned no rows"

    with ctx.step("planning") as step:
        watermarks = load_watermarks(ctx.engine, [s for s, _ in NRT_SOURCES])
        plans = [plan_source(s, sensor, today, availability, watermarks.get(s), settings.firms_max_backfill_days)
                 for s, sensor in NRT_SOURCES]
        step.summary.update(
            today=today, bbox=bbox,
            estimated_transactions=sum(call_cost(w, bbox) for p in plans for w in p.windows),
            sources={p.source: _describe_plan(p, watermarks.get(p.source), availability) for p in plans})

    ctx.counts.update(fetched=0, new=0, modified=0, unchanged=0)
    per_source: dict[str, Counter] = {}
    errors: dict[str, str] = {}
    for plan in plans:
        if plan.skipped:
            continue
        counts = per_source.setdefault(plan.source, Counter({k: 0 for k in COUNT_KEYS}))
        try:
            with ctx.step(f"fetch:{plan.source}") as step:
                step.summary["windows"] = []
                for w in plan.windows:
                    body = client.fetch_window(w, bbox)
                    fetch_id, got = store_response(ctx, w, bbox, body, availability.get(w.source))
                    counts.update(got)
                    for k in ("fetched", "new", "modified", "unchanged"):
                        ctx.counts[k] += got[k]
                    step.summary["windows"].append({"fetch_id": fetch_id, "start": w.start, "days": w.days, **got})
                step.summary["counts"] = dict(counts)
        except Exception as e:
            errors[plan.source] = ctx.redact(describe(e))
            log.error("fetch of %s failed: %s", plan.source, errors[plan.source],
                      extra={"job_id": ctx.job_id, "kind": ctx.kind, "source": plan.source})

    with ctx.step("storage") as step:
        pending = count_pending(ctx.engine)
        ctx.counts["pending"] = pending
        step.summary.update(
            totals={k: sum(c[k] for c in per_source.values()) for k in COUNT_KEYS},
            per_source={s: dict(c) for s, c in per_source.items()}, pending=pending,
            skipped={p.source: p.skipped for p in plans if p.skipped}, failed=errors)

    if errors:
        raise JobError("FIRMS fetch failed for " + "; ".join(f"{s}: {e}" for s, e in errors.items()))
    if ctx.counts["new"] == 0 and ctx.counts["modified"] == 0 and pending == 0:
        return "no_change"  # I3: decided before worker.process_pending (and so crewai) is imported
    from worker.process_pending import process_pending

    return process_pending(ctx, availability)


def _describe_plan(plan: SourcePlan, watermark: date | None, availability: dict[str, tuple[date, date]]) -> dict:
    if plan.skipped:
        return {"skipped": plan.skipped}
    return {"watermark": watermark, "availability": availability[plan.source], "notes": list(plan.notes),
            "windows": [{"start": w.start, "days": w.days} for w in plan.windows]}


def _store_availability(ctx: JobContext, availability: dict[str, tuple[date, date]]) -> None:
    if not availability:
        return
    now = ctx.now()
    with ctx.engine.begin() as c:
        c.execute(text(
            "INSERT INTO firms_availability (job_id, source, min_date, max_date, recorded_at) "
            "VALUES (:job_id, :source, :min_date, :max_date, :now)"),
            [{"job_id": ctx.job_id, "source": s, "min_date": lo, "max_date": hi, "now": now}
             for s, (lo, hi) in sorted(availability.items())])


def load_watermarks(engine: Engine, sources: list[str]) -> dict[str, date]:
    """Last day covered by a stored fetch, per source (a fetch row is committed with its rows)."""
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT source, max(window_start + (window_days - 1)) AS covered_to FROM firms_fetches "
            "WHERE source = ANY(CAST(:sources AS text[])) GROUP BY source"), {"sources": sources}).all()
    return {r.source: r.covered_to for r in rows}


def count_pending(engine: Engine) -> int:
    with engine.connect() as c:
        return c.scalar(text("SELECT count(*) FROM raw_detections WHERE needs_processing"))


def raw_path(raw_dir: Path, source: str, fetched_at: datetime, fetch_id: int) -> Path:
    """<raw dir>/<source>/<UTC fetch date>/<fetch_id>.csv.gz"""
    return Path(raw_dir) / source / fetched_at.date().isoformat() / f"{fetch_id}.csv.gz"


def _write_gz(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as fh:
        fh.write(body)
    os.replace(tmp, path)


def store_response(ctx: JobContext, w: Window, bbox: str, body: str,
                   availability: tuple[date, date] | None) -> tuple[int, dict[str, int]]:
    """Record one response atomically: firms_fetches row, raw .csv.gz, classified upsert of its rows."""
    parsed = parse_csv(body, w.source)
    fetched_at = ctx.now()
    lo, hi = availability or (None, None)
    with ctx.engine.begin() as c:
        fetch_id = c.scalar(text("""
            INSERT INTO firms_fetches (job_id, source, bbox, window_start, window_days, fetched_at, sha256,
                                       row_count, raw_path, availability_min_date, availability_max_date)
            VALUES (:job_id, :source, :bbox, :start, :days, :fetched_at, :sha256, :rows, '', :lo, :hi)
            RETURNING id"""),
            {"job_id": ctx.job_id, "source": w.source, "bbox": bbox, "start": w.start, "days": w.days,
             "fetched_at": fetched_at, "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
             "rows": parsed.rows, "lo": lo, "hi": hi})
        path = raw_path(ctx.settings.firms_raw_dir, w.source, fetched_at, fetch_id)
        _write_gz(path, body)
        c.execute(text("UPDATE firms_fetches SET raw_path = :path WHERE id = :id"), {"path": str(path), "id": fetch_id})
        record_sensor_days(c, w.source, w.start, w.days, hi, fetch_id, fetched_at)
        classified = upsert_detections(c, parsed.detections, fetch_id, ctx.job_id)
    return fetch_id, {"fetched": parsed.rows, **classified, "malformed": parsed.malformed,
                      "duplicates": parsed.duplicates}


_KEY_MATCH = """r.source = i.source AND r.satellite = i.satellite AND r.latitude_raw = i.latitude_raw
    AND r.longitude_raw = i.longitude_raw AND r.acq_date_raw = i.acq_date_raw AND r.acq_time_raw = i.acq_time_raw"""
_KEY_COLUMNS = ("source", "satellite", "latitude_raw", "longitude_raw", "acq_date_raw", "acq_time_raw")
_DATA_COLUMNS = ("content_hash", "product", "latitude", "longitude", "acq_date", "acq_time", "instrument",
                 "confidence", "version", "frp", "daynight", "type", "delivered")


def _cols(columns: tuple[str, ...]) -> str:
    return ", ".join(f'"{col}"' for col in columns)  # quoted: "type" is an SQL keyword


def upsert_detections(c: Connection, detections: list[Detection], fetch_id: int, job_id: int) -> dict[str, int]:
    """Classify rows as new / modified / unchanged against raw_detections and apply them, set-based.

    The rows go into a temp table; classification and each kind of write is one SQL statement. New
    and modified rows get needs_processing = true; unchanged rows keep theirs and only record that
    this fetch saw them (last_seen_fetch_id). `detections` must be unique by natural key.
    """
    counts = {"new": 0, "modified": 0, "unchanged": 0}
    if not detections:
        return counts
    c.execute(text("""
        CREATE TEMP TABLE incoming_detections (
            source text NOT NULL, satellite text NOT NULL, latitude_raw text NOT NULL,
            longitude_raw text NOT NULL, acq_date_raw text NOT NULL, acq_time_raw text NOT NULL,
            content_hash text NOT NULL, product text NOT NULL, latitude double precision NOT NULL,
            longitude double precision NOT NULL, acq_date date NOT NULL, acq_time time NOT NULL,
            instrument text, confidence text, version text, frp double precision, daynight text,
            "type" smallint, delivered jsonb NOT NULL, cls text NOT NULL DEFAULT 'new'
        ) ON COMMIT DROP"""))
    columns = _KEY_COLUMNS + _DATA_COLUMNS
    values = ", ".join("CAST(:delivered AS jsonb)" if col == "delivered" else f":{col}" for col in columns)
    c.execute(text(f"INSERT INTO incoming_detections ({_cols(columns)}) VALUES ({values})"),
              [d.params() for d in detections])
    c.execute(text(f"""
        UPDATE incoming_detections AS i
        SET cls = CASE WHEN r.content_hash = i.content_hash THEN 'unchanged' ELSE 'modified' END
        FROM raw_detections AS r WHERE {_KEY_MATCH}"""))
    ids = {"fetch_id": fetch_id, "job_id": job_id}
    c.execute(text(f"""
        INSERT INTO raw_detections ({_cols(columns)}, first_seen_fetch_id, last_seen_fetch_id,
                                    changed_in_job_id, needs_processing)
        SELECT {_cols(columns)}, :fetch_id, :fetch_id, :job_id, true
        FROM incoming_detections WHERE cls = 'new'"""), ids)
    c.execute(text(f"""
        UPDATE raw_detections AS r
        SET {', '.join(f'"{col}" = i."{col}"' for col in _DATA_COLUMNS)},
            last_seen_fetch_id = :fetch_id, changed_in_job_id = :job_id, needs_processing = true
        FROM incoming_detections AS i WHERE i.cls = 'modified' AND {_KEY_MATCH}"""), ids)
    c.execute(text(f"""
        UPDATE raw_detections AS r SET last_seen_fetch_id = :fetch_id
        FROM incoming_detections AS i WHERE i.cls = 'unchanged' AND {_KEY_MATCH}"""), ids)
    counts.update(dict(c.execute(text("SELECT cls, count(*) FROM incoming_detections GROUP BY cls")).all()))
    return counts
