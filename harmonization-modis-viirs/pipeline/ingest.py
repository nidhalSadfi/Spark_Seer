#!/usr/bin/env python3
"""Count FIRMS vegetation fires on a world 0.5° grid, by month and sensor.

P1 keeps every latitude. Reads every country-year CSV under the raw
directory, and every CSV inside a yearly ``.zip`` (the all-countries
archive). MODIS C6.1 and VIIRS Suomi NPP only. Keeps type 0 (presumed
vegetation), MODIS confidence >= 30, and VIIRS confidence nominal or
high (n/h).

    python pipeline/ingest.py data/raw data/cells.parquet
    python pipeline/ingest.py data/raw data/cells.parquet 34

Omitting the last argument, or passing ``none``, keeps every latitude.
That is the world grid. ``34`` keeps latitude >= 34, the North Africa
slice (Tunisia and Algeria). Cells are half-open 0.5° squares: a point
belongs to [lat0, lat0+0.5) x [lon0, lon0+0.5). ``cell_id`` is the
southwest corner, one decimal, with the sign kept on each side
(``36.0_9.0``, ``-33.5_-71.5``). ``lat``/``lon`` are the cell centre.
``n`` is the detection count and ``days`` is the number of distinct
``acq_date`` values.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

MODIS = "MODIS"
VIIRS = "VIIRS"
USECOLS = ["latitude", "longitude", "acq_date", "confidence", "type", "version"]
VIIRS_KEEP = {"n", "h", "nominal", "high"}
# Tokens that mean "no latitude floor". A number, including 0, is still a floor.
NO_LATITUDE_FLOOR = {"none", "all", "global", "world"}
USAGE = """usage: python pipeline/ingest.py RAW OUT [MIN_LAT]

  python pipeline/ingest.py data/raw data/cells.parquet
  python pipeline/ingest.py data/raw data/cells.parquet 34

Omitting MIN_LAT keeps every latitude. That is the P1 world grid.
none means the same thing. MIN_LAT 34 keeps latitude >= 34
(North Africa slice: Tunisia and Algeria).
"""


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


def parse_min_lat(value: str | None) -> float | None:
    """Return a latitude floor, or None when every latitude should be kept."""
    if value is None:
        return None
    if value.strip().lower() in NO_LATITUDE_FLOOR:
        return None
    try:
        return float(value)
    except ValueError:
        raise SystemExit(f"latitude floor must be a number or none, got {value!r}\n{USAGE}") from None


def _cell_index(values: pd.Series) -> np.ndarray:
    # Index of the 0.5° cell. floor, so the north and east edges fall in the next cell.
    return np.floor(values.to_numpy(dtype=np.float64) * 2.0 + 1e-9).astype(np.int32)


def prepare(df: pd.DataFrame, sensor: str, min_lat: float | None) -> tuple[pd.DataFrame, dict]:
    lat = pd.to_numeric(df["latitude"], errors="coerce")
    lon = pd.to_numeric(df["longitude"], errors="coerce")
    kind = pd.to_numeric(df["type"], errors="coerce")
    when = pd.to_datetime(df["acq_date"], errors="coerce")
    usable = lat.notna() & lon.notna() & when.notna()
    # "north" counts rows that pass the latitude rule. With no floor, that is every usable row.
    north = usable if min_lat is None else usable & (lat >= min_lat)
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


def _corner_token(index: int) -> str:
    """One-decimal southwest corner from a 0.5° cell index.

    ``index`` is ``floor(degrees * 2)``. The corner is ``index / 2``. A
    negative index keeps the minus sign, so south and west cells stay
    distinct (``-0.5``, ``-33.5``) and the id does not flip to ``-0.0``.
    """
    index = int(index)
    sign = "-" if index < 0 else ""
    whole, rem = divmod(abs(index), 2)
    return f"{sign}{whole}.{5 if rem else 0}"


def _cell_id(lat_i: int, lon_i: int) -> str:
    """Stable worldwide id: southwest corner ``lat_lon``, signs included."""
    return f"{_corner_token(lat_i)}_{_corner_token(lon_i)}"


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
    grouped["cell_id"] = [
        _cell_id(lat_i, lon_i)
        for lat_i, lon_i in zip(grouped["lat_i"].to_numpy(), grouped["lon_i"].to_numpy())
    ]
    grouped["n"] = grouped["n"].astype(np.int32)
    grouped["days"] = grouped["days"].astype(np.int16)
    grouped["year"] = grouped["year"].astype(np.int16)
    grouped["month"] = grouped["month"].astype(np.int8)
    columns = ["cell_id", "lat_i", "lon_i", "lat0", "lon0", "lat", "lon", "year", "month", "sensor", "n", "days"]
    return grouped[columns].sort_values(["sensor", "year", "month", "lat_i", "lon_i"]).reset_index(drop=True)


def _read_csv(source) -> pd.DataFrame:
    return pd.read_csv(source, usecols=lambda c: c in USECOLS)


def iter_tables(raw_dir: Path):
    """Yield ``(label, frame)`` for each CSV on disk or inside a zip.

    ``frame`` is None when the path is not MODIS or VIIRS S-NPP, so those
    files are counted and skipped without being parsed.
    """
    paths = sorted(p for p in raw_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".csv", ".zip"})
    if not paths:
        raise SystemExit(f"no CSV files under {raw_dir}")
    saw = False
    for path in paths:
        if path.suffix.lower() == ".csv":
            saw = True
            yield path, None if sensor_from_path(path) is None else _read_csv(path)
            continue
        try:
            archive = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise SystemExit(f"{path} is not a zip file ({exc})") from None
        with archive:
            members = [name for name in archive.namelist() if name.lower().endswith(".csv") and not name.endswith("/")]
            for name in sorted(members):
                saw = True
                label = Path(f"{path.as_posix()}/{name}")
                if sensor_from_path(label) is None:
                    yield label, None
                    continue
                yield label, _read_csv(io.BytesIO(archive.read(name)))
    if not saw:
        raise SystemExit(f"no CSV files under {raw_dir}")


def _add_versions(into: dict, extra: dict) -> None:
    for key, count in extra.items():
        into[key] = into.get(key, 0) + int(count)


def run(raw_dir: Path, out_path: Path, min_lat: float | None) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    totals = {
        MODIS: {"files": 0, "rows": 0, "usable": 0, "north": 0, "vegetation": 0, "kept": 0, "versions": {}},
        VIIRS: {"files": 0, "rows": 0, "usable": 0, "north": 0, "vegetation": 0, "kept": 0, "versions": {}},
    }
    skipped = []
    considered = 0
    for label, frame in iter_tables(raw_dir):
        considered += 1
        sensor = sensor_from_path(label)
        if frame is None or sensor is None:
            skipped.append(label.name)
            continue
        missing = [c for c in ("latitude", "longitude", "acq_date", "confidence", "type") if c not in frame.columns]
        if missing:
            raise SystemExit(f"{label} is missing columns: {', '.join(missing)}")
        kept, stats = prepare(frame, sensor, min_lat)
        bucket = totals[sensor]
        bucket["files"] += 1
        for key in ("rows", "usable", "north", "vegetation", "kept"):
            bucket[key] += stats[key]
        _add_versions(bucket["versions"], stats["versions"])
        if not kept.empty:
            parts.append(kept)
        print(f"read  {sensor:5}  {label.name:32}  rows={stats['rows']:7d}  kept={stats['kept']:7d}", file=sys.stderr)

    detections = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["latitude", "longitude", "acq_date", "sensor"])
    cells = aggregate(detections)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cells.to_parquet(out_path, index=False)

    print(f"files {considered}  skipped {len(skipped)}")
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
        lat_note = "lat=all" if min_lat is None else f"lat>={min_lat:g}"
        print(
            f"{sensor:5}  detections={bucket['kept']}  years={years}  "
            f"files={bucket['files']}  read={bucket['rows']}  "
            f"{lat_note}:{bucket['north']}  type0:{bucket['vegetation']}  "
            f"versions[{version_text}]{months}"
        )
        if sensor == VIIRS and bucket["kept"] < 1000:
            print("WARNING  VIIRS detections are near 0. The confidence or type filter is wrong.", file=sys.stderr)
    print(f"wrote {out_path}  rows={len(cells)}  detections={int(cells['n'].sum()) if len(cells) else 0}")
    return cells


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help"}:
        print(USAGE, end="")
        return
    if len(args) not in (2, 3):
        raise SystemExit(USAGE)
    raw_dir, out_path = args[0], args[1]
    min_lat = parse_min_lat(args[2] if len(args) == 3 else None)
    run(Path(raw_dir), Path(out_path), min_lat)


if __name__ == "__main__":
    main()
