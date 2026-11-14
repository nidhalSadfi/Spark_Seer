#!/usr/bin/env python3
"""Count FIRMS vegetation fires on a 0.5° grid, by month and sensor.

Reads every country-year CSV under the raw directory (MODIS C6.1 and
VIIRS Suomi NPP). Keeps rows with latitude >= the given threshold,
type 0 (presumed vegetation), MODIS confidence >= 30, and VIIRS
confidence nominal or high (n/h). Writes one row per cell, year, month
and sensor:

    python pipeline/ingest.py data/raw data/cells.parquet 34

Cells are half-open 0.5° squares: a point belongs to
[lat0, lat0+0.5) x [lon0, lon0+0.5). ``lat``/``lon`` are the cell centre.
``n`` is the detection count and ``days`` is the number of distinct
``acq_date`` values.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

MODIS = "MODIS"
VIIRS = "VIIRS"
USECOLS = ["latitude", "longitude", "acq_date", "confidence", "type", "version"]
VIIRS_KEEP = {"n", "h", "nominal", "high"}


def sensor_from_path(path: Path) -> str | None:
    """Map a yearly archive path to MODIS or VIIRS S-NPP. Skip other sensors."""
    name = path.as_posix().lower()
    if "noaa" in name or "jpss" in name:
        return None
    if "viirs" in name:
        return VIIRS
    if "modis" in name:
        return MODIS
    return None


def _cell_index(values: pd.Series) -> np.ndarray:
    # Index of the 0.5° cell. floor, so the north and east edges fall in the next cell.
    return np.floor(values.to_numpy(dtype=np.float64) * 2.0 + 1e-9).astype(np.int32)


def prepare(df: pd.DataFrame, sensor: str, min_lat: float) -> tuple[pd.DataFrame, dict]:
    lat = pd.to_numeric(df["latitude"], errors="coerce")
    lon = pd.to_numeric(df["longitude"], errors="coerce")
    kind = pd.to_numeric(df["type"], errors="coerce")
    when = pd.to_datetime(df["acq_date"], errors="coerce")
    usable = lat.notna() & lon.notna() & when.notna()
    north = usable & (lat >= min_lat)
    vegetation = north & (kind == 0)
    if sensor == MODIS:
        confidence = pd.to_numeric(df["confidence"], errors="coerce")
        keep = vegetation & (confidence >= 30)
    else:
        confidence = df["confidence"].astype(str).str.strip().str.lower()
        keep = vegetation & confidence.isin(VIIRS_KEEP)
    versions = df["version"].astype(str).str.strip().value_counts().to_dict() if "version" in df.columns else {}
    kept = pd.DataFrame(
        {
            "latitude": lat[keep].to_numpy(),
            "longitude": lon[keep].to_numpy(),
            "acq_date": when[keep].dt.strftime("%Y-%m-%d").to_numpy(),
            "sensor": sensor,
        }
    )
    stats = {
        "rows": int(len(df)),
        "usable": int(usable.sum()),
        "north": int(north.sum()),
        "vegetation": int(vegetation.sum()),
        "kept": int(keep.sum()),
        "versions": versions,
    }
    return kept, stats


def aggregate(detections: pd.DataFrame) -> pd.DataFrame:
    """One row per 0.5° cell, year, month and sensor."""
    if detections.empty:
        return pd.DataFrame(
            columns=["cell_id", "lat_i", "lon_i", "lat0", "lon0", "lat", "lon", "year", "month", "sensor", "n", "days"]
        )
    frame = detections.copy()
    frame["lat_i"] = _cell_index(frame["latitude"])
    frame["lon_i"] = _cell_index(frame["longitude"])
    frame["year"] = frame["acq_date"].str.slice(0, 4).astype(np.int16)
    frame["month"] = frame["acq_date"].str.slice(5, 7).astype(np.int8)
    grouped = (
        frame.groupby(["sensor", "year", "month", "lat_i", "lon_i"], as_index=False)
        .agg(n=("acq_date", "size"), days=("acq_date", "nunique"))
    )
    grouped["lat0"] = grouped["lat_i"] / 2.0
    grouped["lon0"] = grouped["lon_i"] / 2.0
    grouped["lat"] = grouped["lat0"] + 0.25
    grouped["lon"] = grouped["lon0"] + 0.25
    grouped["cell_id"] = (
        grouped["lat0"].map(lambda v: f"{v:.1f}") + "_" + grouped["lon0"].map(lambda v: f"{v:.1f}")
    )
    grouped["n"] = grouped["n"].astype(np.int32)
    grouped["days"] = grouped["days"].astype(np.int16)
    grouped["year"] = grouped["year"].astype(np.int16)
    grouped["month"] = grouped["month"].astype(np.int8)
    columns = ["cell_id", "lat_i", "lon_i", "lat0", "lon0", "lat", "lon", "year", "month", "sensor", "n", "days"]
    return grouped[columns].sort_values(["sensor", "year", "month", "lat_i", "lon_i"]).reset_index(drop=True)


def csv_paths(raw_dir: Path) -> list[Path]:
    return sorted(p for p in raw_dir.rglob("*.csv") if p.is_file())


def _add_versions(into: dict, extra: dict) -> None:
    for key, count in extra.items():
        into[key] = into.get(key, 0) + int(count)


def run(raw_dir: Path, out_path: Path, min_lat: float) -> pd.DataFrame:
    paths = csv_paths(raw_dir)
    if not paths:
        raise SystemExit(f"no CSV files under {raw_dir}")
    parts: list[pd.DataFrame] = []
    totals = {
        MODIS: {"files": 0, "rows": 0, "usable": 0, "north": 0, "vegetation": 0, "kept": 0, "versions": {}},
        VIIRS: {"files": 0, "rows": 0, "usable": 0, "north": 0, "vegetation": 0, "kept": 0, "versions": {}},
    }
    skipped = []
    for path in paths:
        sensor = sensor_from_path(path)
        if sensor is None:
            skipped.append(path.name)
            continue
        frame = pd.read_csv(path, usecols=lambda c: c in USECOLS)
        missing = [c for c in ("latitude", "longitude", "acq_date", "confidence", "type") if c not in frame.columns]
        if missing:
            raise SystemExit(f"{path} is missing columns: {', '.join(missing)}")
        kept, stats = prepare(frame, sensor, min_lat)
        bucket = totals[sensor]
        bucket["files"] += 1
        for key in ("rows", "usable", "north", "vegetation", "kept"):
            bucket[key] += stats[key]
        _add_versions(bucket["versions"], stats["versions"])
        if not kept.empty:
            parts.append(kept)
        print(f"read  {sensor:5}  {path.name:32}  rows={stats['rows']:7d}  kept={stats['kept']:7d}", file=sys.stderr)

    detections = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["latitude", "longitude", "acq_date", "sensor"])
    cells = aggregate(detections)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cells.to_parquet(out_path, index=False)

    print(f"files {len(paths)}  skipped {len(skipped)}")
    if skipped:
        print("skipped (not MODIS or VIIRS S-NPP): " + ", ".join(skipped))
    for sensor in (MODIS, VIIRS):
        bucket = totals[sensor]
        subset = detections[detections["sensor"] == sensor] if not detections.empty else detections
        if subset.empty:
            years = "none"
            months = ""
        else:
            year_values = sorted({int(d[:4]) for d in subset["acq_date"]})
            years = f"{year_values[0]}-{year_values[-1]}"
            if year_values != list(range(year_values[0], year_values[-1] + 1)):
                missing_years = [y for y in range(year_values[0], year_values[-1] + 1) if y not in year_values]
                years += f"  missing={missing_years}"
            by_month = subset["acq_date"].str.slice(5, 7).value_counts().sort_index()
            months = "  months=" + ",".join(f"{m}:{int(n)}" for m, n in by_month.items())
        version_text = ", ".join(f"{k}:{v}" for k, v in sorted(bucket["versions"].items())) or "-"
        print(
            f"{sensor:5}  detections={bucket['kept']}  years={years}  "
            f"files={bucket['files']}  read={bucket['rows']}  "
            f"lat>={min_lat:g}:{bucket['north']}  type0:{bucket['vegetation']}  "
            f"versions[{version_text}]{months}"
        )
        if sensor == VIIRS and bucket["kept"] < 1000:
            print("WARNING  VIIRS detections are near 0. The confidence or type filter is wrong.", file=sys.stderr)
    print(f"wrote {out_path}  rows={len(cells)}  detections={int(cells['n'].sum()) if len(cells) else 0}")
    return cells


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        raise SystemExit("usage: python pipeline/ingest.py data/raw data/cells.parquet 34")
    raw_dir, out_path, min_lat = args
    run(Path(raw_dir), Path(out_path), float(min_lat))


if __name__ == "__main__":
    main()
