"""World-scale run of harmonization-modis-viirs/pipeline/ingest.py: same filters, same output file.

    python scripts/ingest_world.py RAW OUT [MIN_LAT] [--workers N]
    python scripts/ingest_world.py data/world/raw data/world/cells.parquet --workers 3

Their ingest keeps every kept detection in memory before counting. That is fine for one region, but
the world (about 185 million detections) needs far more RAM than a laptop has. This script runs their
own prepare() (the filters) on one CSV chunk at a time, reduces it to detections per cell and day at
once, and finishes one sensor-year before the next. Days are counted per sensor-year, so `days` stays
exact even when a cell spans two country files. tests/test_ingest_world.py checks that the output is
identical to theirs. Their file is imported, not changed.
"""
import argparse
import importlib.util
import re
import sys
import time
import zipfile
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "p1_ingest", ROOT / "harmonization-modis-viirs" / "pipeline" / "ingest.py")
ingest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest)

CHUNK = 1_000_000  # CSV rows in memory at once, per worker
REQUIRED = ["latitude", "longitude", "acq_date", "confidence", "type"]
KEYS = ["sensor", "year", "month", "lat_i", "lon_i"]
COLUMNS = ["cell_id", "lat_i", "lon_i", "lat0", "lon0", "lat", "lon", "year", "month", "sensor", "n", "days"]
YEAR = re.compile(r"(?<!\d)((?:19|20)\d\d)(?!\d)")


def find_tables(raw_dir: Path) -> dict:
    """{(sensor, year): [(file, zip member or None), ...]}, from the file names.

    Only MODIS and VIIRS S-NPP tables, like their ingest (it skips the other sensors' files).
    """
    groups = defaultdict(list)
    for path in sorted(p for p in raw_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".csv", ".zip"}):
        if path.suffix.lower() == ".csv":
            members = [None]
        else:
            with zipfile.ZipFile(path) as z:
                members = sorted(n for n in z.namelist() if n.lower().endswith(".csv"))
        for member in members:
            label = path if member is None else Path(f"{path.as_posix()}/{member}")
            sensor = ingest.sensor_from_path(label)
            if sensor is None:
                continue
            year = YEAR.search(label.name) or YEAR.search(path.name)
            groups[(sensor, year.group(1) if year else "?")].append((path, member))
    if not groups:
        sys.exit(f"no MODIS or VIIRS S-NPP CSV files under {raw_dir}")
    return groups


def read_chunks(path: Path, member: str | None):
    """Their _read_csv (same columns), in chunks; zip members are streamed, not loaded whole."""
    if member is None:
        yield from pd.read_csv(path, usecols=lambda c: c in ingest.USECOLS, chunksize=CHUNK)
        return
    with zipfile.ZipFile(path) as z, z.open(member) as f:
        yield from pd.read_csv(f, usecols=lambda c: c in ingest.USECOLS, chunksize=CHUNK)


def count_group(job):
    """One sensor-year: monthly n and days per cell, plus row counts for the report."""
    (sensor, year), tables, min_lat = job
    daily, stats = [], {"files": len(tables), "rows": 0, "kept": 0}
    for path, member in tables:
        for frame in read_chunks(path, member):
            missing = [c for c in REQUIRED if c not in frame.columns]
            if missing:
                raise SystemExit(f"{path}/{member or ''} is missing columns: {', '.join(missing)}")
            kept, s = ingest.prepare(frame, sensor, min_lat)  # their filters
            stats["rows"] += s["rows"]
            stats["kept"] += s["kept"]
            if kept.empty:
                continue
            cells = pd.DataFrame({"lat_i": ingest._cell_index(kept["latitude"]),
                                  "lon_i": ingest._cell_index(kept["longitude"]),
                                  "acq_date": kept["acq_date"].to_numpy()})
            daily.append(cells.groupby(["lat_i", "lon_i", "acq_date"]).size().rename("n").reset_index())
    if not daily:
        return pd.DataFrame(columns=KEYS + ["n", "days"]), (sensor, year), stats
    d = pd.concat(daily, ignore_index=True)
    d["year"] = d["acq_date"].str.slice(0, 4).astype(np.int16)
    d["month"] = d["acq_date"].str.slice(5, 7).astype(np.int8)
    monthly = d.groupby(["year", "month", "lat_i", "lon_i"], as_index=False).agg(
        n=("n", "sum"), days=("acq_date", "nunique"))
    monthly.insert(0, "sensor", sensor)
    return monthly, (sensor, year), stats


def assemble(parts: list[pd.DataFrame]) -> pd.DataFrame:
    """Their aggregate()'s columns, types and order."""
    parts = [p for p in parts if len(p)]
    if not parts:
        return pd.DataFrame(columns=COLUMNS)
    cells = pd.concat(parts, ignore_index=True)
    if cells.duplicated(KEYS).any():  # would make `days` wrong: never sum it across groups
        sys.exit("a sensor-month appears in two file groups: name files <sensor>_<year>_...")
    cells = cells.astype({"lat_i": np.int32, "lon_i": np.int32, "year": np.int16, "month": np.int8,
                          "n": np.int32, "days": np.int16})
    cells["lat0"] = cells["lat_i"] / 2.0
    cells["lon0"] = cells["lon_i"] / 2.0
    cells["lat"] = cells["lat0"] + 0.25
    cells["lon"] = cells["lon0"] + 0.25
    ids = {k: ingest._cell_id(*k) for k in cells[["lat_i", "lon_i"]].drop_duplicates().itertuples(index=False, name=None)}
    cells["cell_id"] = [ids[k] for k in zip(cells["lat_i"].to_numpy(), cells["lon_i"].to_numpy())]
    return cells[COLUMNS].sort_values(["sensor", "year", "month", "lat_i", "lon_i"]).reset_index(drop=True)


def run(raw_dir: Path, out: Path, min_lat: float | None, workers: int = 1) -> pd.DataFrame:
    groups = find_tables(raw_dir)
    jobs = [(key, tables, min_lat) for key, tables in sorted(groups.items())]
    print(f"{sum(len(t) for t in groups.values())} tables in {len(jobs)} sensor-years, {workers} worker(s)",
          file=sys.stderr)
    parts, totals, t0 = [], defaultdict(lambda: {"rows": 0, "kept": 0, "files": 0}), time.time()
    with Pool(workers) as pool:
        for i, (monthly, (sensor, year), stats) in enumerate(pool.imap_unordered(count_group, jobs), 1):
            parts.append(monthly)
            for k in stats:
                totals[sensor][k] += stats[k]
            print(f"[{i}/{len(jobs)}] {sensor:5} {year}  rows={stats['rows']:>10,}  kept={stats['kept']:>9,}"
                  f"  cell-months={len(monthly):>8,}  {time.time() - t0:5.0f}s", file=sys.stderr, flush=True)
    cells = assemble(parts)
    out.parent.mkdir(parents=True, exist_ok=True)
    cells.to_parquet(out, index=False)
    for sensor in (ingest.MODIS, ingest.VIIRS):
        sub = cells[cells["sensor"] == sensor]
        years = f"{sub.year.min()}-{sub.year.max()}" if len(sub) else "none"
        print(f"{sensor:5}  detections={int(sub['n'].sum())}  years={years}  files={totals[sensor]['files']}"
              f"  read={totals[sensor]['rows']}")
    print(f"wrote {out}  rows={len(cells)}  detections={int(cells['n'].sum()) if len(cells) else 0}")
    return cells


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raw", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("min_lat", nargs="?", default=None, help="like their ingest: omit or 'none' for the world")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    run(a.raw, a.out, ingest.parse_min_lat(a.min_lat), a.workers)
