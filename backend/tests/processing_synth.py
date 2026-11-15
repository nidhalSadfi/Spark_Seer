"""SYNTHETIC FIRMS-like detections for the processing tests. NOT real FIRMS data: the shapes match
raw_detections and the FIRMS archive CSVs, the values are made up."""

from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

RAW_COLUMNS = ["source", "product", "satellite", "latitude", "longitude", "acq_date", "acq_time", "confidence",
               "type", "instrument"]
SATELLITE = {"MODIS": "Aqua", "VIIRS_SNPP": "N", "VIIRS_NOAA20": "N20", "VIIRS_NOAA21": "N21"}
MODIS_HEADER = ["latitude", "longitude", "brightness", "scan", "track", "acq_date", "acq_time", "satellite",
                "instrument", "confidence", "version", "bright_t31", "frp", "daynight", "type"]
VIIRS_HEADER = ["latitude", "longitude", "bright_ti4", "scan", "track", "acq_date", "acq_time", "satellite",
                "instrument", "confidence", "version", "bright_ti5", "frp", "daynight", "type"]
ALL_FAMILIES = ("MODIS", "SNPP", "NOAA20", "NOAA21")
NOW = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
_DEFAULT = object()


def det(source: str, lat: float, lon: float, day, *, confidence=_DEFAULT, type=_DEFAULT, acq_time: str = "1200",
        satellite: str | None = None) -> dict:
    """One raw_detections-shaped row. Defaults pass the filter (SP: type 0; NRT: no type)."""
    prefix, product = source.rsplit("_", 1)
    is_modis = prefix == "MODIS"
    if confidence is _DEFAULT:
        confidence = "80" if is_modis else "n"
    if type is _DEFAULT:
        type = 0 if product == "SP" else None
    return {"source": source, "product": product, "satellite": satellite or SATELLITE.get(prefix, "?"),
            "latitude": float(lat), "longitude": float(lon),
            "acq_date": day if isinstance(day, date) else date.fromisoformat(day), "acq_time": acq_time,
            "confidence": confidence, "type": type, "instrument": "MODIS" if is_modis else "VIIRS"}


def frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=RAW_COLUMNS)
    df["type"] = pd.array(df["type"].tolist(), dtype="Int64")
    return df


def write_archive_csv(path: Path, rows: list[dict]) -> Path:
    """FIRMS archive-style CSV (MODIS or VIIRS header chosen from the first row's instrument)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = MODIS_HEADER if rows[0]["instrument"] == "MODIS" else VIIRS_HEADER
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({**{h: "" for h in header}, **{k: v for k, v in r.items() if k in header}})
    return path


def archive_row(instrument: str, satellite: str, lat: float, lon: float, day: date, *, confidence, type,
                version: str, acq_time: str = "1200") -> dict:
    return {"latitude": f"{lat:.5f}" if instrument == "VIIRS" else f"{lat:.4f}",
            "longitude": f"{lon:.5f}" if instrument == "VIIRS" else f"{lon:.4f}",
            "acq_date": day.isoformat(), "acq_time": acq_time, "satellite": satellite, "instrument": instrument,
            "confidence": str(confidence), "version": version, "type": "" if type is None else str(type),
            "brightness": "320.1", "bright_ti4": "330.2", "scan": "1.0", "track": "1.0", "frp": "5.0",
            "daynight": "D", "bright_t31": "290.0", "bright_ti5": "295.0"}


# Cells (cy, cx) for the synthetic archive: Tunisia and Algeria north of 34N, one west of 0 and one
# straddling 34N (half its detections are south of the latitude cut).
ARCHIVE_CELLS = [(73, 18), (74, 19), (72, 8), (70, -3), (67, 16)]


def synthetic_archive(raw_dir: Path, years=range(2009, 2022), seed: int = 7) -> dict[str, int]:
    """Write MODIS / VIIRS S-NPP / VIIRS NOAA-20 yearly archive CSVs (SP) under raw_dir.

    Includes rows the filter must drop: low confidence, type 2 and 3, south of 34N. Returns the
    number of rows written per file kind.
    """
    rng = np.random.default_rng(seed)
    out: dict[str, list[dict]] = {}
    months = np.arange(1, 13)
    season = 1 + 6 * np.exp(-((months - 8) ** 2) / 4)
    for year in years:
        for cy, cx in ARCHIVE_CELLS:
            busy = rng.uniform(0.5, 2.0)
            for i, month in enumerate(months):
                lam = busy * season[i]
                ndays = pd.Period(f"{year}-{month:02d}").days_in_month
                plan = [("modis", "MODIS", None, rng.poisson(lam))]
                if year >= 2012:
                    plan.append(("viirs-snpp", "VIIRS", "N", rng.poisson(3 * lam)))
                if (year, month) >= (2018, 4):
                    plan.append(("viirs-jpss1", "VIIRS", "N20", rng.poisson(2.9 * lam)))
                for kind, instrument, sat, n in plan:
                    for _ in range(n):
                        lat = cy * 0.5 + rng.uniform(0, 0.5)
                        lon = cx * 0.5 + rng.uniform(0, 0.5)
                        day = date(year, month, int(rng.integers(1, ndays + 1)))
                        if instrument == "MODIS":
                            conf = int(rng.integers(0, 101))
                            satellite = "Terra" if rng.random() < 0.5 else "Aqua"
                            version = "6.1"
                        else:
                            conf = rng.choice(["l", "n", "h", "N", "H", "nominal", "high", "low"],
                                              p=[0.15, 0.4, 0.2, 0.05, 0.05, 0.05, 0.05, 0.05])
                            satellite = sat
                            version = "2"
                        typ = int(rng.choice([0, 2, 3], p=[0.9, 0.05, 0.05]))
                        acq_time = f"{int(rng.integers(0, 24)):02d}{int(rng.integers(0, 60)):02d}"
                        out.setdefault(f"{kind}_{year}", []).append(
                            archive_row(instrument, satellite, lat, lon, day, confidence=conf, type=typ,
                                        version=version, acq_time=acq_time))
    # Hand-placed edge rows: latitude exactly 34 (kept) and just south of it, a cell edge at
    # 36.5N / 9.0E, negative longitudes, MODIS confidence exactly 30 and 29.
    edge = [("MODIS", "Aqua", 34.0, 9.2, 30), ("MODIS", "Aqua", 33.9999, 9.2, 90), ("MODIS", "Terra", 36.5, 9.0, 29),
            ("MODIS", "Terra", 36.5, 9.0, 31), ("MODIS", "Aqua", 35.2, -0.0001, 80), ("MODIS", "Aqua", 35.2, -1.5, 80)]
    for instrument, sat, lat, lon, conf in edge:
        out.setdefault("modis_2015", []).append(
            archive_row(instrument, sat, lat, lon, date(2015, 7, 4), confidence=conf, type=0, version="6.1"))
    for lat, lon, conf in [(34.0, 9.2, "n"), (36.5, 9.0, "h"), (35.2, -0.00001, "N"), (33.99999, 9.2, "h")]:
        out.setdefault("viirs-snpp_2015", []).append(
            archive_row("VIIRS", "N", lat, lon, date(2015, 7, 4), confidence=conf, type=0, version="2"))
    written = {}
    for name, rows in sorted(out.items()):
        kind, year = name.rsplit("_", 1)
        # Mirror the FIRMS yearly country file names; some in sub-directories (both readers recurse).
        sub = raw_dir / year if int(year) % 2 else raw_dir
        write_archive_csv(sub / f"{kind}_{year}_Tunisia.csv", rows)
        written[name] = len(rows)
    return written


def square_geojson(features: list[tuple[dict, list[list[float]]]]) -> dict:
    """FeatureCollection of polygons: [(properties, [[lon, lat], ...] ring), ...]."""
    return {"type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": props,
                          "geometry": {"type": "Polygon", "coordinates": [ring + [ring[0]]]}}
                         for props, ring in features]}
