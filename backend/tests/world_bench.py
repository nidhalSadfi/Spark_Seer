"""World-scale benchmark (spec §16) on SYNTHETIC data. NOT real FIRMS data.

    python tests/world_bench.py [--detections 3000000] [--cells 20000] [--history-rows 5000000] [--keep]

from backend/, with TEST_DATABASE_URL exported. It writes ONLY to TEST_DATABASE_URL, in a fresh
schema of its own that is dropped at the end (unless --keep), and never calls FIRMS or OpenRouter
(the review is disabled: no OPENROUTER_MODELS).

What it measures, each with wall time and the peak resident memory (RSS) of this process:
  1. bootstrap: `worker.bootstrap.bootstrap` of a synthetic world harmonized parquet
     (--history-rows cell-months over ~3x --cells cells, 2001-2025) with its k table;
  2. process_pending: one firms_poll processing run over --detections NRT detections of
     November 2026 spread over --cells 0.5-degree cells worldwide (all pending: the worst case,
     e.g. the first world poll), with a synthetic static mask; per stage timings;
  3. the API: core.get_data() (the approved cell_month frame) and /calendar for a few bboxes.

Memory is sampled from /proc/self/status every 20 ms; the database server's own memory is not
included. Results are printed as JSON.
"""

from __future__ import annotations

import argparse
import functools
import io
import json
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(BACKEND), str(BACKEND / "tests")]

NOW = datetime(2026, 11, 30, 15, 0, tzinfo=timezone.utc)
MONTH_DAYS = 30
SOURCES = {  # source -> (share of detections, satellite(s), first day, last day of November)
    "MODIS_NRT": (0.15, ("Terra", "Aqua"), 1, 30),
    "VIIRS_SNPP_NRT": (0.03, ("N",), 1, 2),        # S-NPP ends 2026-11-02 (catalogue)
    "VIIRS_NOAA20_NRT": (0.41, ("N20",), 1, 30),
    "VIIRS_NOAA21_NRT": (0.41, ("N21",), 1, 30),
}
DELIVERED = '{"scan":"0.39","track":"0.36","version":"2.0NRT","bright_ti4":"330.2","bright_ti5":"290.1"}'
RAW_COLUMNS = ["source", "satellite", "latitude_raw", "longitude_raw", "acq_date_raw", "acq_time_raw", "content_hash",
               "product", "latitude", "longitude", "acq_date", "acq_time", "instrument", "confidence", "version",
               "frp", "daynight", "type", "delivered", "first_seen_fetch_id", "last_seen_fetch_id",
               "changed_in_job_id", "needs_processing"]


# ----------------------------------------------------------------------------- measuring

class Meter:
    """Wall time and peak RSS per named phase (RSS sampled every 20 ms in a thread)."""

    def __init__(self):
        self.results: dict[str, dict] = {}
        self._active: list[list[float]] = []  # [peak] of every phase in progress (phases nest)
        threading.Thread(target=self._sample, daemon=True).start()

    @staticmethod
    def rss_mb() -> float:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
        return 0.0

    def _sample(self):
        while True:
            rss = self.rss_mb()
            for peak in list(self._active):
                peak[0] = max(peak[0], rss)
            time.sleep(0.02)

    @contextmanager
    def phase(self, name: str):
        start_rss = self.rss_mb()
        peak = [start_rss]
        self._active.append(peak)
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self._active[:] = [p for p in self._active if p is not peak]
            top = max(peak[0], self.rss_mb())
            entry = self.results.setdefault(name, {"seconds": 0.0, "calls": 0, "start_rss_mb": round(start_rss),
                                                   "peak_rss_mb": 0})
            entry["seconds"] = round(entry["seconds"] + time.perf_counter() - t0, 2)
            entry["calls"] += 1
            entry["peak_rss_mb"] = max(entry["peak_rss_mb"], round(top))

    def wrap(self, owner, attr: str, label: str | None = None):
        """Time every call of owner.attr (a module function or a method) as phase `label`."""
        if not hasattr(owner, attr):
            return
        fn = getattr(owner, attr)

        @functools.wraps(fn)
        def timed(*a, **kw):
            with self.phase(label or attr):
                return fn(*a, **kw)
        setattr(owner, attr, timed)


# ----------------------------------------------------------------------------- synthetic data

def world_cells(rng, n: int) -> np.ndarray:
    """n distinct (cy, cx) 0.5-degree cells between 40S and 70N, all longitudes."""
    cy = rng.integers(-80, 140, size=n * 2)
    cx = rng.integers(-360, 360, size=n * 2)
    cells = np.unique(np.stack([cy, cx], axis=1), axis=0)
    rng.shuffle(cells)
    return cells[:n]


def detections_frame(rng, cells: np.ndarray, n: int, fetch_ids: dict[str, int], job_id: int) -> pd.DataFrame:
    """n NRT detections of November 2026 over `cells` (lognormal cell activity), unique by natural key."""
    weights = rng.lognormal(0.0, 1.5, size=len(cells))
    idx = rng.choice(len(cells), size=n, p=weights / weights.sum())
    names = list(SOURCES)
    src = rng.choice(len(names), size=n, p=[SOURCES[s][0] for s in names])
    lat = np.round(cells[idx, 0] * 0.5 + rng.uniform(0, 0.5, n), 5)
    lon = np.round(cells[idx, 1] * 0.5 + rng.uniform(0, 0.5, n), 5)
    day = np.empty(n, dtype=np.int64)
    sat = np.empty(n, dtype=object)
    for i, s in enumerate(names):
        sel = src == i
        _, sats, d0, d1 = SOURCES[s]
        day[sel] = rng.integers(d0, d1 + 1, size=sel.sum())
        sat[sel] = np.array(sats, dtype=object)[rng.integers(0, len(sats), size=sel.sum())]
    hh, mm = rng.integers(0, 24, n), rng.integers(0, 60, n)
    source = np.array(names, dtype=object)[src]
    is_modis = src == 0
    conf = np.where(is_modis, rng.integers(0, 101, n).astype(str),
                    np.array(["l", "n", "h"])[rng.choice(3, size=n, p=[0.1, 0.7, 0.2])])
    df = pd.DataFrame({
        "source": source, "satellite": sat,
        "latitude_raw": pd.Series(lat).map("{:.5f}".format), "longitude_raw": pd.Series(lon).map("{:.5f}".format),
        "day": day, "hh": hh, "mm": mm,
    })
    df["acq_date_raw"] = "2026-11-" + pd.Series(day).map("{:02d}".format)
    df["acq_time_raw"] = pd.Series(hh * 100 + mm).map("{:04d}".format)
    df = df.drop_duplicates(["source", "satellite", "latitude_raw", "longitude_raw", "acq_date_raw", "acq_time_raw"])
    keep = df.index.to_numpy()
    m = len(df)
    df["content_hash"] = [os.urandom(32).hex() for _ in range(m)]
    df["product"] = "NRT"
    df["latitude"], df["longitude"] = lat[keep], lon[keep]
    df["acq_date"] = df["acq_date_raw"]
    df["acq_time"] = pd.Series(hh[keep], index=df.index).map("{:02d}".format) + ":" + \
        pd.Series(mm[keep], index=df.index).map("{:02d}".format)
    df["instrument"] = np.where(is_modis[keep], "MODIS", "VIIRS")
    df["confidence"] = conf[keep]
    df["version"] = np.where(is_modis[keep], "6.1NRT", "2.0NRT")
    df["frp"] = np.round(rng.gamma(1.5, 6.0, m), 2)
    df["daynight"] = np.where(rng.random(m) < 0.6, "D", "N")
    df["type"] = ""  # NRT rows have no type (NULL)
    df["delivered"] = DELIVERED
    df["first_seen_fetch_id"] = df["source"].map(fetch_ids)
    df["last_seen_fetch_id"] = df["first_seen_fetch_id"]
    df["changed_in_job_id"] = job_id
    df["needs_processing"] = "t"
    return df[RAW_COLUMNS].reset_index(drop=True)


def copy_frame(engine, table: str, df: pd.DataFrame, chunk: int = 500_000) -> None:
    """COPY a frame into `table` (CSV, empty string = NULL)."""
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cols = ", ".join(f'"{c}"' for c in df.columns)
        with cur.copy(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, NULL '')") as cp:
            for i in range(0, len(df), chunk):
                buf = io.StringIO()
                df.iloc[i:i + chunk].to_csv(buf, header=False, index=False)
                cp.write(buf.getvalue())
        raw.commit()
    finally:
        raw.close()


def history_parquet(rng, path: Path, active_cells: np.ndarray, rows: int) -> pd.DataFrame:
    """A synthetic world harmonized.parquet (pipeline/calibrate.py's columns) of about `rows`
    cell-months over the active cells plus twice as many others, 2001-2025."""
    extra = world_cells(rng, len(active_cells) * 3)
    cells = np.unique(np.concatenate([active_cells, extra]), axis=0)
    years = np.arange(2001, 2026)
    total = len(cells) * len(years) * 12
    pick = np.sort(rng.choice(total, size=min(rows, total), replace=False))
    ci, rest = np.divmod(pick, len(years) * 12)
    yi, mi = np.divmod(rest, 12)
    df = pd.DataFrame({"cy": cells[ci, 0].astype("int32"), "cx": cells[ci, 1].astype("int32"),
                       "year": years[yi].astype("int16"), "month": (mi + 1).astype("int8")})
    k_cell = pd.DataFrame({"cy": cells[:, 0], "cx": cells[:, 1]})
    kt = k_cell.loc[k_cell.index.repeat(12)].reset_index(drop=True)
    kt["month"] = np.tile(np.arange(1, 13), len(cells)).astype("int8")
    kt["k"] = np.round(rng.lognormal(1.2, 0.4, len(kt)), 4)
    kt["support"] = rng.integers(0, 500, len(kt)).astype(float)
    k_month = {m: round(float(3.0 + 0.1 * m), 4) for m in range(1, 13)}
    df = df.merge(kt.astype({"cy": "int32", "cx": "int32"}), on=["cy", "cx", "month"], how="left")
    df["k_month"] = df["month"].map(k_month).astype(float)
    pre = df["year"] < 2012
    df["MODIS"] = rng.poisson(8, len(df)).astype(float)
    df["VIIRS"] = np.where(pre, 0.0, rng.poisson(25, len(df)).astype(float))
    df["harmonized"] = np.where(pre, df["MODIS"] * df["k"], df["VIIRS"])
    df["source"] = np.where(pre, "MODIS*k", "VIIRS")
    df = df[["cy", "cx", "year", "month", "MODIS", "VIIRS", "k", "k_month", "support", "harmonized", "source"]]
    df.to_parquet(path, index=False)
    return df


def synthetic_mask(rng, dets: pd.DataFrame, path: Path, squares: int = 50_000):
    """A mask of `squares` 0.01-degree squares, about 1 in 25 placed on detections."""
    from processing.mask import StaticMask, square_index

    on = dets.sample(n=min(squares // 25, len(dets)), random_state=1)
    iy = np.concatenate([square_index(on.latitude, 0.01), rng.integers(-4000, 7000, squares)])
    ix = np.concatenate([square_index(on.longitude, 0.01), rng.integers(-18000, 18000, squares)])
    sq = sorted({(int(a), int(b)) for a, b in zip(iy, ix)})[:squares]
    mask = StaticMask(version="bench-mask", res_deg=0.01, min_count=3, types=(1, 2, 3),
                      squares=tuple((a, b, 3) for a, b in sq), input={"synthetic": True})
    mask.save(path)
    return mask


# ----------------------------------------------------------------------------- the run

def make_schema(url: str):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from db import make_engine

    schema = f"bench_{uuid.uuid4().hex[:12]}"
    admin = make_engine(url)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = make_engine(url, connect_args={"options": f"-c search_path={schema}", "application_name": schema})
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    return admin, engine, schema


def drop_schema(admin, schema: str) -> None:
    from sqlalchemy import text

    with admin.begin() as c:
        c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                       "WHERE application_name = :app AND pid <> pg_backend_pid()"), {"app": schema})
        c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def seed_jobs_and_fetches(engine) -> tuple[int, dict[str, int]]:
    """A running firms_poll job (owned by 'bench') and November fetch windows per source (bbox world)."""
    from sqlalchemy import text

    with engine.begin() as c:
        job_id = c.scalar(text("""
            INSERT INTO processing_jobs (kind, status, trigger, lease_owner, lease_expires_at, created_at, started_at)
            VALUES ('firms_poll', 'running', 'manual', 'bench', :lease, :now, :now) RETURNING id"""),
            {"now": NOW, "lease": NOW + timedelta(days=1)})
        fetch_ids = {}
        for source, (_, _, d0, d1) in SOURCES.items():
            start = date(2026, 11, d0)
            while start <= date(2026, 11, d1):
                days = min(5, (date(2026, 11, d1) - start).days + 1)
                fid = c.scalar(text("""
                    INSERT INTO firms_fetches (job_id, source, bbox, window_start, window_days, fetched_at, sha256,
                                               row_count, raw_path)
                    VALUES (:job, :source, 'world', :start, :days, :now, 'synthetic', 0, '') RETURNING id"""),
                    {"job": job_id, "source": source, "start": start, "days": days, "now": NOW})
                fetch_ids.setdefault(source, fid)
                start += timedelta(days=days)
    return job_id, fetch_ids


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detections", type=int, default=3_000_000)
    ap.add_argument("--cells", type=int, default=20_000)
    ap.add_argument("--history-rows", type=int, default=5_000_000)
    ap.add_argument("--skip", nargs="*", default=[], choices=["bootstrap", "process", "api"])
    ap.add_argument("--keep", action="store_true", help="keep the schema (prints its name)")
    ap.add_argument("--workdir", type=Path, default=None)
    args = ap.parse_args(argv)
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        print("TEST_DATABASE_URL is not set", file=sys.stderr)
        return 2
    # The API under test must use the benchmark schema, never the real DATABASE_URL.
    os.environ.pop("DATABASE_URL", None)
    import tempfile

    from sqlalchemy import text

    import worker.bootstrap
    import worker.process_pending as pp
    from processing import pipeline
    from processing.inter_viirs import FIT_KIND
    from settings import Settings
    from worker import store
    from worker.jobs import JobContext

    meter = Meter()
    rng = np.random.default_rng(16)
    work = args.workdir or Path(tempfile.mkdtemp(prefix="world_bench_"))
    work.mkdir(parents=True, exist_ok=True)
    admin, engine, schema = make_schema(url)
    out: dict = {"detections_requested": args.detections, "cells": args.cells, "schema": schema}
    try:
        cells = world_cells(rng, args.cells)
        job_id, fetch_ids = seed_jobs_and_fetches(engine)
        with meter.phase("generate_detections"):
            dets = detections_frame(rng, cells, args.detections, fetch_ids, job_id)
        out["detections"] = len(dets)
        mask_path = work / "mask.json"
        synthetic_mask(rng, dets, mask_path)

        parquet = work / "harmonized.parquet"
        with meter.phase("generate_history"):
            hist = history_parquet(rng, parquet, cells, args.history_rows)
        out["history_rows"] = len(hist)
        del hist
        (work / "harmonized_validation.json").write_text(json.dumps({"error_pct_cell_month_ratio": 11.5}))
        fit = work / "inter_viirs_fit.json"
        fit.write_text(json.dumps({"kind": FIT_KIND, "approved_r": 0.983, "r_global": 0.983, "status": "synthetic"}))
        if "bootstrap" not in args.skip:
            for attr in ("parquet_rows", "copy_cell_month"):
                meter.wrap(worker.bootstrap, attr)
            for attr in ("insert_calibration", "insert_cell_month", "copy_cell_month"):
                meter.wrap(store, attr)
            with meter.phase("bootstrap_total"):
                res = worker.bootstrap.bootstrap(engine, parquet=parquet, validation=work / "harmonized_validation.json",
                                                 inter_viirs=fit, mask=mask_path, now=NOW - timedelta(days=20))
            out["bootstrap"] = res.message

        with meter.phase("copy_detections_into_db"):
            copy_frame(engine, "raw_detections", dets)
        del dets
        with engine.connect() as c:
            out["raw_detections_mb"] = c.scalar(text("SELECT pg_total_relation_size('raw_detections') / 1048576"))

        if "process" not in args.skip:
            for attr in ("load_pending", "affected_keys", "load_detections", "load_coverage", "recent_drop_shares",
                         "load_rules", "run_processing", "load_pending_keys", "decision_record"):
                meter.wrap(pp, attr)
            for attr in ("load_approved_calibration", "cell_rows", "write_staging", "promote", "prune_staging",
                         "insert_steps"):
                meter.wrap(store, attr)
            for stage in pipeline.STAGE_ORDER:
                meter.wrap(pipeline.ProcessingRun, f"_{stage}", f"stage_{stage}")
            meter.wrap(pipeline.ProcessingRun, "result", "stage_result")
            settings = Settings(database_url=url, firms_map_key="x", firms_bbox="world", static_mask_file=mask_path) \
                if _accepts_world() else Settings(database_url=url, firms_map_key="x", static_mask_file=mask_path)
            if not _accepts_world():  # before FIRMS_BBOX=world existed: the fetch rows say "world"
                with engine.begin() as c:
                    c.execute(text("UPDATE firms_fetches SET bbox = :b"), {"b": settings.firms_bbox_arg})
            settings = replace(settings, **({"processing_min_lat": None} if hasattr(settings, "processing_min_lat")
                                            else {}))
            avail = {"MODIS_NRT": (date(2000, 11, 1), date(2026, 11, 30)),
                     "VIIRS_SNPP_NRT": (date(2012, 1, 20), date(2026, 11, 2)),
                     "VIIRS_NOAA20_NRT": (date(2018, 4, 1), date(2026, 11, 30)),
                     "VIIRS_NOAA21_NRT": (date(2024, 1, 17), date(2026, 11, 30))}
            ctx = JobContext(engine=engine, job_id=job_id, kind="firms_poll", settings=settings, clock=lambda: NOW,
                             client_factory=lambda key: None, worker_id="bench")
            with meter.phase("process_pending_total"):
                status = pp.process_pending(ctx, avail)
            out["process_pending"] = {"status": status, "counts": ctx.counts}
            with engine.connect() as c:
                out["process_pending"]["prompt_chars"] = c.scalar(text(
                    "SELECT summary->'payload_chars' FROM job_steps WHERE job_id = :j AND name = 'review'"),
                    {"j": job_id})
                out["process_pending"]["pending_after"] = c.scalar(text(
                    "SELECT count(*) FROM raw_detections WHERE needs_processing"))
                out["process_pending"]["filter"] = c.scalar(text(
                    "SELECT summary->'dropped_total' FROM job_steps WHERE job_id = :j AND name = 'filter'"),
                    {"j": job_id})

        if "api" not in args.skip:
            from api import core, database

            os.environ["DATABASE_URL"] = _with_search_path(url, schema)
            os.environ["API_VERSION_CHECK_S"] = "3600"
            database.reset()
            core.reset_cache()
            with meter.phase("api_get_data_first_load"):
                frame = core.get_data()
            out["api_frame"] = {"rows": len(frame), "memory_mb": round(frame.memory_usage(deep=True).sum() / 2**20)}
            del frame
            boxes = {"bizerte": (8.3, 36.0, 9.8, 37.2), "amazon_arc": (-66.0, -14.0, -44.0, -2.0),
                     "africa": (-18.0, -35.0, 52.0, 18.0), "world": (-180.0, -90.0, 180.0, 90.0)}
            for name, box in boxes.items():
                with meter.phase(f"calendar_{name}"):
                    for _ in range(5):
                        core.calendar_for_bbox(box, 2001, 2026)
            out["calendar_note"] = "5 calls each, warm cache"
            database.reset()
    finally:
        engine.dispose()
        if args.keep:
            print(f"kept schema {schema}", file=sys.stderr)
        else:
            drop_schema(admin, schema)
        admin.dispose()
    out["phases"] = meter.results
    out["process_peak_rss_mb"] = round(_maxrss_mb())
    phases = out.pop("phases")
    print(json.dumps(out, indent=1, default=str))
    for name, r in phases.items():
        print(f"{name:32s} {r['seconds']:9.2f} s  x{r['calls']:<3d} rss {r['start_rss_mb']:6d} -> peak {r['peak_rss_mb']:6d} MB")
    (work / "result.json").write_text(json.dumps({**out, "phases": phases}, indent=1, default=str))
    return 0


def _accepts_world() -> bool:
    from settings import Settings

    try:
        Settings(firms_bbox="world")
        return True
    except Exception:
        return False


def _with_search_path(url: str, schema: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}options=-csearch_path%3D{schema}"


def _maxrss_mb() -> float:
    import resource

    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


if __name__ == "__main__":
    sys.exit(main())
