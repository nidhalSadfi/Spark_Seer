"""World historical dataset and calibration from the FIRMS yearly all-countries archive (spec §16).

    python scripts/build_world.py all       [--world data/world] [--workers 4]
    python scripts/build_world.py mask      # 1. world static-source mask  -> static_mask.json
    python scripts/build_world.py count     # 2. filter + counts           -> counts.parquet, filter_summary.json
    python scripts/build_world.py harmonize # 3. k + harmonized dataset    -> harmonized.parquet, harmonized_validation.json
    python scripts/build_world.py r         # 4. inter-VIIRS factor r      -> inter_viirs_fit.json
    python scripts/build_world.py report    # 5. summary                   -> VALIDATION.md

Input: <world>/raw/{modis_c61,viirs_snpp,viirs_noaa20}/*_all_countries.zip, as written by
`python -m firms_tool archive --global --sensors ... --out <world>/raw` (README, World data).

Same rules as the live processing (§5, §16): every row goes through processing.archive.raw_from_frame
and processing.filters.apply_filters with WORLD_PARAMS (no 34°N cut), the world static-source mask
and no country clip, then processing.grid's keys; see processing/world.py for the chunked path
and tests/test_world_build.py for the oracle tests. The mask is built with build_static_mask's rule
over every downloaded sensor (MODIS, S-NPP, NOAA-20). k is pipeline/calibrate.py's (as
build_data.py), r is processing.inter_viirs's, and both are validated globally, per continent and
per latitude band (scripts/world_regions.py).

Each step records its wall time and the peak resident memory of its process tree in
<world>/logs/steps.json. Steps are restartable: rerun one and it overwrites its own outputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import calibrate  # noqa: E402
from processing.common import KEY_COLS, jsonable  # noqa: E402
from processing.grid import COUNT_COLS, to_ingest_cells  # noqa: E402
from processing.inter_viirs import (FIT_KIND, OUTAGES_FILE, accept_r, fit_inter_viirs, load_outages,  # noqa: E402
                                    outage_months, validate_inter_viirs)
from processing.mask import load_mask  # noqa: E402
from processing.world import (WORLD_PARAMS, MaskTally, archive_files, combine_daily, daily_counts,  # noqa: E402
                              filter_chunk, iter_chunks, mask_from_tally, mask_tally, merge_filter_summaries,
                              monthly_from_daily, natural_key_hashes)
import world_regions  # noqa: E402

CHUNK = 500_000              # CSV rows per chunk, per worker
COMPACT_ROWS = 4_000_000     # merge a worker's per-day tables once they hold this many rows
MASK_COLUMNS = ("latitude", "longitude", "acq_date", "acq_time", "satellite", "instrument", "version", "type")
HIGH_ERROR_PCT = 25.0        # a region's harmonization error above this (or 2x global) is listed as high
MIN_REGION_VIIRS = 1000      # regions with fewer test-year S-NPP detections are reported as insufficient
# Named boxes for the mask summary: (name, lat_min, lat_max, lon_min, lon_max).
MASK_BOXES = [
    ("Persian Gulf (Iraq, Kuwait, Iran, Saudi Arabia, Qatar, UAE)", 23.0, 32.0, 44.0, 57.0),
    ("Niger delta (Nigeria)", 3.5, 7.0, 4.5, 9.0),
    ("West Siberia (Khanty-Mansi, Yamal)", 55.0, 70.0, 60.0, 90.0),
    ("Permian basin (Texas, New Mexico)", 30.5, 34.0, -105.0, -100.5),
    ("Bakken (North Dakota)", 46.5, 49.0, -105.0, -101.5),
    ("Eagle Ford (Texas)", 27.5, 30.0, -100.0, -96.5),
    ("Algeria / Libya Sahara fields", 26.0, 33.0, 2.0, 25.0),
    ("Gulf of Mexico offshore", 18.0, 30.0, -98.0, -84.0),
    ("North Sea", 53.0, 62.0, -3.0, 9.0),
]


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# -- step bookkeeping: wall time and peak RSS of this process and its children ------------------

def _tree_rss_bytes(root: int) -> int:
    parents: dict[int, int] = {}
    rss: dict[int, int] = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/status") as fh:
                ppid = vm = 0
                for line in fh:
                    if line.startswith("PPid:"):
                        ppid = int(line.split()[1])
                    elif line.startswith("VmRSS:"):
                        vm = int(line.split()[1]) * 1024
            parents[int(d)], rss[int(d)] = ppid, vm
        except (OSError, ValueError):
            continue
    total, todo = 0, [root]
    children: dict[int, list[int]] = {}
    for pid, ppid in parents.items():
        children.setdefault(ppid, []).append(pid)
    while todo:
        pid = todo.pop()
        total += rss.get(pid, 0)
        todo.extend(children.get(pid, []))
    return total


class Step:
    """Context manager: measures wall time and samples the process tree's RSS every second."""

    def __init__(self, world: Path, name: str):
        self.world, self.name, self.peak, self._stop = world, name, 0, threading.Event()

    def _sample(self):
        while not self._stop.is_set():
            self.peak = max(self.peak, _tree_rss_bytes(os.getpid()))
            self._stop.wait(1.0)

    def __enter__(self):
        self.t0 = time.time()
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()
        print(f"== {self.name} started {utcnow()}", flush=True)
        return self

    def __exit__(self, exc_type, *_):
        self._stop.set()
        self._thread.join()
        wall = time.time() - self.t0
        log = self.world / "logs" / "steps.json"
        log.parent.mkdir(parents=True, exist_ok=True)
        doc = json.loads(log.read_text()) if log.exists() else {}
        doc[self.name] = {"wall_s": round(wall, 1), "peak_rss_gb": round(self.peak / 1e9, 2), "finished": utcnow(),
                          "ok": exc_type is None}
        log.write_text(json.dumps(doc, indent=2) + "\n")
        print(f"== {self.name} {'done' if exc_type is None else 'FAILED'}: {wall:.0f} s, "
              f"peak RSS {self.peak / 1e9:.2f} GB", flush=True)
        return False


def _pool(workers: int):
    return Pool(workers, maxtasksperchild=1)


def _jobs(world: Path) -> list[Path]:
    files = archive_files(world / "raw")
    if not files:
        sys.exit(f"no archive files under {world / 'raw'}")
    return sorted(files, key=lambda p: -p.stat().st_size)  # biggest first: better balance


def _write_json(path: Path, doc) -> None:
    path.write_text(json.dumps(jsonable(doc), indent=2, sort_keys=False) + "\n")


# -- 1. mask --------------------------------------------------------------------------------------

def _mask_job(path: Path):
    tally, hashes, members, products = MaskTally(), [], set(), Counter()
    for member, chunk in iter_chunks(path, CHUNK, MASK_COLUMNS):
        members.add(member)
        tally.add(mask_tally(chunk))
        hashes.append(natural_key_hashes(chunk))
        v = chunk["version"].astype("string").str.strip().str.upper() if "version" in chunk else None
        if v is not None:
            products.update({"NRT" if str(k).endswith("NRT") else f"SP {k}": int(n)
                             for k, n in v.value_counts(dropna=False).items()})
    h = np.concatenate(hashes) if hashes else np.array([], dtype=np.uint64)
    dup = int(len(h) - len(np.unique(h)))
    return path.name, tally, {"rows": int(len(h)), "members": len(members), "duplicate_natural_keys": dup,
                              "versions": dict(sorted(products.items()))}


def _mask_clusters(mask) -> dict:
    sq = pd.DataFrame(list(mask.squares), columns=["iy", "ix", "n"])
    if sq.empty:
        return {"boxes": [], "top_1deg": [], "by_continent": {}}
    lat = (sq.iy + 0.5) * mask.res_deg
    lon = (sq.ix + 0.5) * mask.res_deg
    sq = sq.assign(lat=lat, lon=lon, continent=world_regions.continent(lat, lon))
    boxes = []
    for name, a, b, c, d in MASK_BOXES:
        sel = sq[(sq.lat >= a) & (sq.lat < b) & (sq.lon >= c) & (sq.lon < d)]
        boxes.append({"name": name, "box": [a, b, c, d], "squares": int(len(sel)), "detections": int(sel.n.sum())})
    one = sq.assign(lat1=np.floor(sq.lat).astype(int), lon1=np.floor(sq.lon).astype(int)) \
        .groupby(["lat1", "lon1"]).agg(squares=("n", "size"), detections=("n", "sum")).reset_index() \
        .sort_values("detections", ascending=False)
    one["continent"] = world_regions.continent(one.lat1 + 0.5, one.lon1 + 0.5)
    by_cont = sq.groupby("continent").agg(squares=("n", "size"), detections=("n", "sum"))
    return {"boxes": boxes,
            "top_1deg": [{"lat": int(r.lat1), "lon": int(r.lon1), "continent": r.continent, "squares": int(r.squares),
                          "detections": int(r.detections)} for r in one.head(25).itertuples()],
            "by_continent": {k: {"squares": int(v.squares), "detections": int(v.detections)}
                             for k, v in by_cont.sort_values("detections", ascending=False).iterrows()}}


def step_mask(world: Path, workers: int) -> None:
    jobs = _jobs(world)
    total, per_file = MaskTally(), {}
    with _pool(workers) as pool:
        for i, (name, tally, info) in enumerate(pool.imap_unordered(_mask_job, jobs), 1):
            total.add(tally)
            per_file[name] = {**info, "typed_rows": tally.typed_rows}
            print(f"[mask {i}/{len(jobs)}] {name}  rows={info['rows']:,}  typed={tally.typed_rows:,}  "
                  f"dup_keys={info['duplicate_natural_keys']}", flush=True)
    mask = mask_from_tally(total)
    out = world / "static_mask.json"
    sha = mask.save(out)
    report = {"file": out.name, "file_sha256": sha, **mask.summary(), "input": mask.input,
              "rule": "processing.mask.build_static_mask: SP rows with type in {1, 2, 3}, no other filter, "
                      "0.01 deg squares with >= min_count detections, over every downloaded archive file",
              "files": dict(sorted(per_file.items())), "clusters": _mask_clusters(mask)}
    _write_json(world / "static_mask_report.json", report)
    print(json.dumps({k: report[k] for k in ("version", "squares", "input")}, indent=2, default=str))


# -- 2. count ----------------------------------------------------------------------------------

_MASK = None


def _init_count(mask_path: str) -> None:
    global _MASK
    _MASK = load_mask(mask_path)


def _count_job(args):
    path, daily_dir = args
    parts, sums, rows, in_mask = [], [], 0, Counter()
    for _member, chunk in iter_chunks(path, CHUNK):
        rows += len(chunk)
        kept, summary = filter_chunk(chunk, mask=_MASK, params=WORLD_PARAMS)
        sums.append(summary)
        if len(kept):
            hit = _MASK.contains(kept["latitude"].to_numpy(), kept["longitude"].to_numpy())
            in_mask.update({f"{f} {p}": int(n) for (f, p), n in
                            kept[hit].groupby(["family", "product"]).size().items()})
            parts.append(daily_counts(kept, WORLD_PARAMS))
            if sum(len(p) for p in parts) > COMPACT_ROWS:
                parts = [combine_daily(parts)]
    daily = combine_daily(parts)
    daily.astype({"cy": "int32", "cx": "int32", "year": "int16", "month": "int8", "n": "int32"}) \
        .to_parquet(Path(daily_dir) / f"{path.stem}.parquet", index=False)
    monthly = monthly_from_daily(daily, WORLD_PARAMS.snpp_end)
    return path.name, monthly, merge_filter_summaries(sums), {"rows": rows, "kept_in_mask_squares": dict(in_mask)}


def _compact_counts(counts: pd.DataFrame) -> pd.DataFrame:
    return counts.astype({"cy": "int32", "cx": "int32", "year": "int16", "month": "int8", "n": "int32",
                          "days": "int16", "n_upto": "int32", "days_upto": "int16"})


def load_counts(world: Path) -> pd.DataFrame:
    c = pd.read_parquet(world / "counts.parquet")
    c = c.astype({col: "int64" for col in COUNT_COLS if col != "family"})
    c.attrs["split_date"] = WORLD_PARAMS.snpp_end  # what the count step aggregated with
    return c


def step_count(world: Path, workers: int) -> None:
    mask_path = world / "static_mask.json"
    if not mask_path.exists():
        sys.exit("run the mask step first")
    daily_dir = world / "work" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    jobs = _jobs(world)
    monthly, sums, per_file = {}, [], {}
    with Pool(workers, initializer=_init_count, initargs=(str(mask_path),), maxtasksperchild=1) as pool:
        for i, (name, m, s, info) in enumerate(pool.imap_unordered(_count_job, [(p, daily_dir) for p in jobs]), 1):
            monthly[name] = _compact_counts(m)
            sums.append(s)
            per_file[name] = {**info, "kept": s.kept_rows, "kept_by_family": s.kept_by_family}
            print(f"[count {i}/{len(jobs)}] {name}  rows={info['rows']:,}  kept={s.kept_rows:,}  "
                  f"cell-months={len(m):,}", flush=True)
    counts = pd.concat(list(monthly.values()), ignore_index=True)
    # A (cell-month, family) in two archive files (a stray date in another year's file) must be
    # recounted from the per-day tables: `days` is never summed.
    dup = counts.duplicated(KEY_COLS + ["family"], keep=False)
    recounted = 0
    if dup.any():
        bad = counts.loc[dup, KEY_COLS + ["family"]].drop_duplicates().astype(
            {c: "int64" for c in KEY_COLS})
        daily = []
        for f in sorted(daily_dir.glob("*.parquet")):
            d = pd.read_parquet(f).astype({c: "int64" for c in KEY_COLS + ["n"]})
            d = d.merge(bad, on=KEY_COLS + ["family"])
            if len(d):
                daily.append(d)
        fixed = monthly_from_daily(combine_daily(daily), WORLD_PARAMS.snpp_end)
        counts = pd.concat([counts[~dup], _compact_counts(fixed)], ignore_index=True)
        recounted = int(len(bad))
    counts = counts.sort_values(KEY_COLS + ["family"]).reset_index(drop=True)
    counts.attrs.clear()  # split_date (a date) is not parquet metadata; load_counts restores it
    counts.to_parquet(world / "counts.parquet", index=False)
    total = merge_filter_summaries(sums)
    in_mask = Counter()
    for info in per_file.values():
        in_mask.update(info["kept_in_mask_squares"])
    by_family = {fam: {"detections": int(sub.n.sum()), "cell_months": int(len(sub)),
                       "cells": int(len(sub[["cy", "cx"]].drop_duplicates()))}
                 for fam, sub in counts.groupby("family")}
    doc = {"params": WORLD_PARAMS, "mask": load_mask(mask_path).summary(), "filter": total.to_dict(),
           "kept_in_mask_squares": dict(sorted(in_mask.items())),
           "kept_in_mask_note": "kept SP type-0 detections that fall in a mask square: the live NRT path drops "
                                "these (NRT rows have no type) until monthly_reconcile restores the SP rows",
           "recounted_cross_file_keys": recounted,
           "by_family": by_family,
           "files": dict(sorted(per_file.items()))}
    _write_json(world / "filter_summary.json", doc)
    print(json.dumps({"filter": {k: doc["filter"][k] for k in ("input_rows", "kept_rows", "kept_by_family")},
                      "by_family": doc["by_family"], "recounted": recounted}, indent=2))


# -- 3. harmonize ---------------------------------------------------------------------------------

def _mae_pct(pred: pd.Series, obs: pd.Series) -> float | None:
    if not len(obs):
        return None
    return round(100 * float((pred - obs).abs().mean() / max(obs.mean(), 1)), 1)


def regional_k_validation(cells: pd.DataFrame, regions: pd.DataFrame) -> dict:
    """calibrate.validate's measures (k fitted once, world-wide, on calibrate.TRAIN; test years
    calibrate.TEST) for every region in `regions` (cy, cx, continent, lat_band) and for the globe.
    The "global" entry equals calibrate.validate(cells). Also per region: the same error at
    cell-month level (no spatial summing), and the bias of the summed prediction."""
    k_cell, k_month = calibrate.fit(cells, calibrate.TRAIN)
    tr = cells[cells.year.isin(calibrate.TRAIN)]
    k_global = float(tr[tr.sensor == "VIIRS"].n.sum() / max(tr[tr.sensor == "MODIS"].n.sum(), 1))
    test = cells[cells.year.isin(calibrate.TEST)]
    m = calibrate.monthly(test, "MODIS").merge(k_cell, on=["cy", "cx", "month"], how="left")
    m["k"] = m.k.fillna(m.month.map(k_month)).fillna(k_global)
    m["pred"] = m.MODIS * m.k
    obs = calibrate.monthly(test, "VIIRS")
    cm = m[["cy", "cx", "year", "month", "MODIS", "pred"]].merge(obs, on=["cy", "cx", "year", "month"],
                                                                 how="outer").fillna(0)
    cm = cm.merge(regions, on=["cy", "cx"], how="left")

    def measures(sub: pd.DataFrame) -> dict:
        t = sub.groupby(["year", "month"]).agg(pred=("pred", "sum"), raw=("MODIS", "sum"), VIIRS=("VIIRS", "sum"))
        t["global"] = t.raw * k_global
        obs_total = float(t.VIIRS.sum())
        return {"error_pct_uncorrected": _mae_pct(t.raw, t.VIIRS),
                "error_pct_global_ratio": _mae_pct(t["global"], t.VIIRS),
                "error_pct_cell_month_ratio": _mae_pct(t.pred, t.VIIRS),
                "error_pct_cell_level": _mae_pct(sub.pred, sub.VIIRS),
                "bias_pct": round(100 * (float(t.pred.sum()) - obs_total) / obs_total, 1) if obs_total else None,
                "test_months": int(len(t)), "test_viirs": int(obs_total), "test_modis": int(t.raw.sum()),
                "cells": int(len(sub[["cy", "cx"]].drop_duplicates())),
                "region_k": round(float(t.VIIRS.sum() / max(t.raw.sum(), 1)), 3)}

    out = {"global": measures(cm), "continent": {}, "lat_band": {}}
    for col in ("continent", "lat_band"):
        for name, sub in cm.groupby(col):
            out[col][name] = measures(sub)
    out["k_global"] = round(k_global, 2)
    return out


def _flag_high(entries: dict, global_err: float) -> list[dict]:
    high = []
    for kind in ("continent", "lat_band"):
        for name, e in entries[kind].items():
            err = e["error_pct_cell_month_ratio"]
            if e["test_viirs"] < MIN_REGION_VIIRS:
                e["status"] = "insufficient_data"
            elif err is not None and (err > HIGH_ERROR_PCT or err > 2 * global_err):
                e["status"] = "high_error"
                high.append({"kind": kind, "region": name, "error_pct": err, "bias_pct": e["bias_pct"]})
            else:
                e["status"] = "ok"
    return high


def step_harmonize(world: Path) -> None:
    counts = load_counts(world)
    cells = to_ingest_cells(counts)
    report = calibrate.validate(cells)  # the existing global measure (calibrate.py, build_data.py)
    regions = world_regions.cell_regions(cells)
    regional = regional_k_validation(cells, regions)
    for key in ("error_pct_uncorrected", "error_pct_global_ratio", "error_pct_cell_month_ratio", "test_months"):
        if regional["global"][key] != report[key]:
            raise SystemExit(f"regional validation's global {key} {regional['global'][key]} != calibrate.validate's "
                             f"{report[key]}")
    high = _flag_high(regional, report["error_pct_cell_month_ratio"])

    h = calibrate.harmonize(cells)
    days = counts[counts.family.isin(["MODIS", "SNPP"])].pivot_table(
        index=KEY_COLS, columns="family", values="days", aggfunc="sum", fill_value=0).reset_index()
    days = days.rename(columns={"MODIS": "MODIS_days", "SNPP": "VIIRS_days"})
    h = h.merge(days, on=KEY_COLS, how="left")
    h[["MODIS_days", "VIIRS_days"]] = h[["MODIS_days", "VIIRS_days"]].fillna(0)
    h = h.astype({"cy": "int32", "cx": "int32", "year": "int16", "month": "int8", "MODIS": "float64",
                  "VIIRS": "float64", "k": "float64", "k_month": "float64", "support": "float64",
                  "harmonized": "float64", "MODIS_days": "int16", "VIIRS_days": "int16"})
    cols = ["cy", "cx", "year", "month", "MODIS", "VIIRS", "k", "k_month", "support", "harmonized", "source",
            "MODIS_days", "VIIRS_days"]
    h = h[cols].sort_values(KEY_COLS).reset_index(drop=True)
    h.to_parquet(world / "harmonized.parquet", index=False)

    f_sum = json.loads((world / "filter_summary.json").read_text())
    doc = {**report,
           "scope": "world (FIRMS yearly all-countries SP archive)",
           "filter_rules": f_sum["filter"]["rules"], "mask_version": f_sum["mask"]["version"],
           "k_note": "k published in harmonized.parquet is calibrate.harmonize's refit on 2012-2021, which includes "
                     "the test years (spec §15); the errors here score the TRAIN-only fit",
           "measure": "error % = 100 * mean |pred - obs| / max(mean obs, 1) over the test year-months of the summed "
                      "region (calibrate.validate); error_pct_cell_level is the same over cell-months (no summing)",
           "regions": {"rules": world_regions.__doc__.strip(), **regional},
           "high_error_regions": high,
           "high_error_rule": f"error_pct_cell_month_ratio > {HIGH_ERROR_PCT} or > 2 x global "
                              f"({2 * report['error_pct_cell_month_ratio']}); regions with < {MIN_REGION_VIIRS} test "
                              "S-NPP detections are 'insufficient_data'",
           "dataset": {"rows": int(len(h)), "cells": int(len(h[["cy", "cx"]].drop_duplicates())),
                       "years": [int(h.year.min()), int(h.year.max())],
                       "modis_detections": int(h.MODIS.sum()), "viirs_detections": int(h.VIIRS.sum()),
                       "by_source": {k: int(v) for k, v in h.source.value_counts().items()}}}
    _write_json(world / "harmonized_validation.json", doc)
    print(json.dumps({k: doc[k] for k in ("error_pct_uncorrected", "error_pct_global_ratio",
                                          "error_pct_cell_month_ratio", "k_global", "dataset")}, indent=2))


# -- 4. r ----------------------------------------------------------------------------------------

def _file_inventory(world: Path) -> dict:
    files = archive_files(world / "raw")
    names, h = [], hashlib.sha256()
    for p in files:
        digest = hashlib.sha256()
        with open(p, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 24), b""):
                digest.update(block)
        rel = p.relative_to(world / "raw").as_posix()
        names.append({"file": rel, "bytes": p.stat().st_size, "sha256": digest.hexdigest()})
        h.update(f"{rel}\t{digest.hexdigest()}\n".encode())
    return {"files": len(files), "file_list": names, "files_sha256": h.hexdigest()}


def _dark_days(world: Path, outages: dict) -> dict:
    """World daily S-NPP / NOAA-20 totals: days far below their month's median (likely outages),
    compared with the committed outage list (not changed here)."""
    rows = []
    for f in sorted((world / "work" / "daily").glob("viirs*.parquet")):
        d = pd.read_parquet(f, columns=["family", "acq_day", "n"])
        rows.append(d.groupby(["family", "acq_day"]).n.sum().reset_index())
    if not rows:
        return {}
    d = pd.concat(rows).groupby(["family", "acq_day"]).n.sum().reset_index()
    out = {}
    listed = {}
    for e in outages["outages"]:
        if "start" in e:
            days = pd.date_range(e["start"], e["end"])
            listed.setdefault(e["family"], set()).update(days)
    for fam in ("SNPP", "NOAA20"):
        sub = d[d.family == fam].set_index("acq_day").n.sort_index()
        if sub.empty:
            continue
        full = sub.reindex(pd.date_range(sub.index.min(), sub.index.max()), fill_value=0)
        med = full.groupby([full.index.year, full.index.month]).transform("median")
        dark = full[full < 0.2 * med]
        out[fam] = {"days_checked": int(len(full)), "dark_days_lt_20pct_of_month_median": [d.date().isoformat()
                                                                                          for d in dark.index],
                    "listed_days_found_dark": int(sum(1 for x in dark.index if x in listed.get(fam, set()))),
                    "listed_days": int(len(listed.get(fam, set())))}
    return out


def step_r(world: Path) -> None:
    counts = load_counts(world)
    vi = counts[counts.family.isin(["SNPP", "NOAA20"])]
    outages = load_outages(OUTAGES_FILE)
    p = WORLD_PARAMS
    fit = fit_inter_viirs(vi, outages, p.inter_viirs_train_years, p.inter_viirs_test_years, p.inter_viirs_pseudo)
    validation = validate_inter_viirs(vi, outages, fit, p.inter_viirs_test_years)
    acceptance = accept_r(fit, validation)
    regions = world_regions.cell_regions(vi)
    vr = vi.merge(regions, on=["cy", "cx"], how="left")
    regional = {"continent": {}, "lat_band": {}}
    for col in ("continent", "lat_band"):
        for name, sub in vr.groupby(col):
            entry = {}
            try:
                entry = validate_inter_viirs(sub, outages, fit, p.inter_viirs_test_years)
                entry.pop("excluded_month_list", None)
            except ValueError as e:
                entry = {"error": str(e)}
            try:
                own = fit_inter_viirs(sub, outages, p.inter_viirs_train_years, p.inter_viirs_test_years,
                                      p.inter_viirs_pseudo)
                entry["regional_r_global"] = round(own.r_global, 4)
                entry["regional_fit_error_pct"] = own.report["error_pct_global_ratio"]
            except ValueError as e:
                entry["regional_r_global"] = None
                entry["regional_fit_note"] = str(e)
            e1, eg = entry.get("error_pct_uncorrected"), entry.get("error_pct_global_ratio")
            entry["world_r_beats_r1"] = None if e1 is None or eg is None else bool(eg < e1)
            regional[col][name] = entry
    f_sum = json.loads((world / "filter_summary.json").read_text())
    doc = {"kind": FIT_KIND, "status": "proposed", **fit.to_dict(),
           "outages": {"file": str(OUTAGES_FILE.relative_to(ROOT)),
                       "sha256": hashlib.sha256(OUTAGES_FILE.read_bytes()).hexdigest(), "source": outages["source"]},
           "input": {"role": "fit pool: the world (FIRMS yearly all-countries SP archive)", **_file_inventory(world)},
           "filter": f_sum["filter"], "mask": f_sum["mask"],
           "validation": {"scope": "world, held-out test years (out of time; the fit pool is the world itself)",
                          **validation, "regions": regional},
           "acceptance": acceptance, "approved_r": acceptance["approved_r"],
           "outage_check": _dark_days(world, outages),
           "note": "World fit (spec §16): fitted on the world's S-NPP / NOAA-20 overlap over the train years, "
                   "validated on the world's test years; the §5 acceptance rule is applied to that validation."}
    _write_json(world / "inter_viirs_fit.json", doc)
    print(json.dumps({"r_global": fit.r_global, "report": {k: fit.report[k] for k in (
        "train_months", "test_months", "error_pct_uncorrected", "error_pct_global_ratio", "error_pct_month_ratio")},
        "acceptance": acceptance}, indent=2))


# -- 5. report ------------------------------------------------------------------------------------

def _fmt(v, nd=1):
    return "-" if v is None else f"{v:,.{nd}f}" if isinstance(v, float) else f"{v:,}"


def step_report(world: Path) -> None:
    hv = json.loads((world / "harmonized_validation.json").read_text())
    rv = json.loads((world / "inter_viirs_fit.json").read_text())
    mk = json.loads((world / "static_mask_report.json").read_text())
    fs = json.loads((world / "filter_summary.json").read_text())
    steps = json.loads((world / "logs" / "steps.json").read_text()) if (world / "logs" / "steps.json").exists() else {}
    reg = hv["regions"]
    L = ["# World validation (EmberGuard, spec §16)", "",
         f"Generated {utcnow()} by `scripts/build_world.py` from the FIRMS yearly all-countries SP archive "
         f"({rv['input']['files']} files). Filters: processing.filters.apply_filters with min_lat "
         f"{fs['filter']['rules']['min_lat']}, static-source mask `{fs['mask']['version']}` "
         f"({fs['mask']['squares']:,} squares), no country clip.", "",
         "## Dataset", "",
         f"- Archive rows read: {fs['filter']['input_rows']:,}; kept: {fs['filter']['kept_rows']:,} "
         f"({', '.join(f'{k} {v:,}' for k, v in fs['filter']['kept_by_family'].items())}).",
         f"- harmonized.parquet: {hv['dataset']['rows']:,} cell-months, {hv['dataset']['cells']:,} cells, years "
         f"{hv['dataset']['years'][0]}-{hv['dataset']['years'][1]}.", "",
         "## Harmonization (MODIS x k -> S-NPP)", "",
         f"Train {hv['train_years'][0]}-{hv['train_years'][1]}, test {hv['test_years'][0]}-{hv['test_years'][1]}, "
         f"k_global {hv['k_global']}. Error % = 100 x mean |pred - obs| / mean obs over the test months of the "
         "summed region (the calibrate.validate measure). `cell-level` is the same over single cell-months. "
         "Bias = summed prediction vs observed.", "",
         "| Region | uncorrected | global k | cell-month k (used) | cell-level | bias % | test S-NPP | cells | status |",
         "|---|---:|---:|---:|---:|---:|---:|---:|---|"]

    def row(name, e):
        return (f"| {name} | {_fmt(e['error_pct_uncorrected'])} | {_fmt(e['error_pct_global_ratio'])} | "
                f"**{_fmt(e['error_pct_cell_month_ratio'])}** | {_fmt(e['error_pct_cell_level'])} | "
                f"{_fmt(e['bias_pct'])} | {e['test_viirs']:,} | {e['cells']:,} | {e.get('status', '')} |")
    L.append(row("**Global**", reg["global"]))
    for kind, title in (("continent", "Continent"), ("lat_band", "Latitude band")):
        for name, e in sorted(reg[kind].items(), key=lambda kv: -kv[1]["test_viirs"]):
            L.append(row(f"{title}: {name}", e))
    L += ["", f"High-error rule: {hv['high_error_rule']}.", ""]
    if hv["high_error_regions"]:
        L.append("High-error regions: " + "; ".join(f"{h['region']} ({h['error_pct']} %, bias {h['bias_pct']} %)"
                                                    for h in hv["high_error_regions"]) + ".")
    else:
        L.append("High-error regions: none by that rule.")
    acc = rv["acceptance"]
    L += ["", "## Inter-VIIRS factor r (S-NPP / NOAA-20)", "",
          f"Fit on the world, train {rv['report']['train_years'][0]}-{rv['report']['train_years'][1]} "
          f"({rv['report']['train_months']} months, {rv['report']['excluded_months']} outage months excluded), "
          f"test {rv['report']['test_years'][0]}-{rv['report']['test_years'][1]} ({rv['validation']['test_months']} "
          f"months). r_global = {rv['r_global']:.4f} (Australia reference 1.02).", "",
          f"Acceptance (§5): error with r = 1: {acc['error_pct_r1']} %, with r_global: {acc['error_pct_r_global']} %, "
          f"per-month r (not applied): {acc['error_pct_month_ratio']} %. **Approved r = {acc['approved_r']:.4f}** "
          f"({acc['approved']}).", "",
          "| Region | error r = 1 | error world r | world r beats r = 1 | regional r (own fit) | test S-NPP |",
          "|---|---:|---:|---|---:|---:|",
          f"| **Global** | {_fmt(rv['validation']['error_pct_uncorrected'])} | "
          f"{_fmt(rv['validation']['error_pct_global_ratio'])} | {acc['approved'] == 'r_global'} | "
          f"{rv['r_global']:.4f} | {rv['validation']['test_snpp']:,} |"]
    for kind, title in (("continent", "Continent"), ("lat_band", "Latitude band")):
        for name, e in sorted(rv["validation"]["regions"][kind].items(), key=lambda kv: -kv[1].get("test_snpp", 0)):
            rr = e.get("regional_r_global")
            L.append(f"| {title}: {name} | {_fmt(e.get('error_pct_uncorrected'))} | "
                     f"{_fmt(e.get('error_pct_global_ratio'))} | {e.get('world_r_beats_r1')} | "
                     f"{'-' if rr is None else f'{rr:.4f}'} | {e.get('test_snpp', 0):,} |")
    L += ["", "## Static-source mask", "",
          f"`{mk['version']}`: {mk['squares']:,} squares of 0.01 deg (>= {mk['params']['min_count']} SP detections "
          f"of type 1/2/3), {mk['input']['detections_in_mask']:,} of {mk['input']['typed_rows']:,} typed detections "
          f"(by type {mk['input']['by_type']}).", "",
          "| Area | squares | typed detections |", "|---|---:|---:|"]
    for b in mk["clusters"]["boxes"]:
        L.append(f"| {b['name']} | {b['squares']:,} | {b['detections']:,} |")
    L += ["", "Largest 1-deg clusters: " + "; ".join(
        f"{c['lat']}..{c['lat'] + 1}N {c['lon']}..{c['lon'] + 1}E ({c['continent']}, {c['detections']:,})"
        for c in mk["clusters"]["top_1deg"][:10]) + ".", ""]
    if steps:
        L += ["## Run cost", "", "| Step | wall | peak RSS (process tree) |", "|---|---:|---:|"]
        for name, s in steps.items():
            L.append(f"| {name} | {s['wall_s'] / 60:.1f} min | {s['peak_rss_gb']} GB |")
        L.append("")
    (world / "VALIDATION.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["mask", "count", "harmonize", "r", "report", "all"])
    ap.add_argument("--world", type=Path, default=ROOT / "data" / "world")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    world = a.world.resolve()
    steps = ["mask", "count", "harmonize", "r", "report"] if a.step == "all" else [a.step]
    for s in steps:
        with Step(world, s):
            if s == "mask":
                step_mask(world, a.workers)
            elif s == "count":
                step_count(world, a.workers)
            elif s == "harmonize":
                step_harmonize(world)
            elif s == "r":
                step_r(world)
            else:
                step_report(world)
    return 0


if __name__ == "__main__":
    sys.exit(main())
