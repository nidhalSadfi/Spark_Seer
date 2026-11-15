"""25-year fire history of the 0.5° cell (about 55 km) under a ticket.

Reads data/cells.parquet, the grid built by pipeline/ingest.py from the public FIRMS
yearly files (MODIS C6.1 2001-2025, VIIRS S-NPP 2012-2025, latitude >= 34°N, type 0,
MODIS confidence >= 30, VIIRS confidence nominal or high). data/ is gitignored, so the
file may be missing; then every lookup returns None and tickets carry no history.

MODIS and VIIRS are reported separately and never summed (different pixel sizes).
"""

from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path

from . import config

CELLS_PATH = Path(os.environ.get("INCIDENT_CELLS_PATH") or config.PROJECT / "data" / "cells.parquet")
# Countries whose yearly files went into the grid; a cell outside them has no history, not zero fires.
COVERED = set((os.environ.get("INCIDENT_HISTORY_COUNTRIES") or "TN,DZ,FR").upper().split(","))
MIN_LAT = 34.0
MODIS_YEARS = (2001, 2025)
VIIRS_YEARS = (2012, 2025)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@lru_cache(maxsize=1)
def _frame(path: str):
    if not Path(path).exists():
        return None
    import pandas as pd
    df = pd.read_parquet(path, columns=["cell_id", "lat_i", "lon_i", "lat", "lon", "year", "month", "sensor", "n",
                                        "days"])
    return df


def cell_key(lat: float, lon: float) -> tuple[int, int]:
    return math.floor(lat * 2), math.floor(lon * 2)


def _sensor_summary(rows, years: tuple[int, int], month: int | None) -> dict:
    n_years = years[1] - years[0] + 1
    by_year = rows.groupby("year")["n"].sum()
    by_month = rows.groupby("month")["n"].sum()
    out = {"years_covered": list(years), "years_with_fire": int((by_year > 0).sum()), "of_years": n_years,
           "detections": int(rows["n"].sum()), "fire_days": int(rows["days"].sum())}
    if len(by_year):
        out["worst_year"] = int(by_year.idxmax())
        out["worst_year_detections"] = int(by_year.max())
        out["last_year"] = int(by_year.index.max())
        out["peak_month"] = MONTHS[int(by_month.idxmax()) - 1]
    if month:
        m = rows[rows["month"] == month]
        out["same_month_years"] = int((m.groupby("year")["n"].sum() > 0).sum())
    return out


def cell_history(lat: float | None, lon: float | None, country: str | None, month: int | None = None,
                 path: Path | str | None = None) -> dict | None:
    """Summary for the cell under (lat, lon), or None when the grid does not cover that place."""
    if lat is None or lon is None or lat < MIN_LAT or (country or "").upper() not in COVERED:
        return None
    df = _frame(str(path or CELLS_PATH))
    if df is None:
        return None
    li, lo = cell_key(lat, lon)
    cell = df[(df["lat_i"] == li) & (df["lon_i"] == lo)]
    cell_id = f"{li / 2:.1f}_{lo / 2:.1f}"
    out: dict = {"cell_id": cell_id, "cell_deg": 0.5, "source": "NASA FIRMS archive, public yearly files"}
    for sensor, years in (("MODIS", MODIS_YEARS), ("VIIRS", VIIRS_YEARS)):
        out[sensor.lower()] = _sensor_summary(cell[cell["sensor"] == sensor], years, month)
    # How busy this cell is compared with every cell that had any MODIS fire in the grid.
    modis = df[df["sensor"] == "MODIS"].groupby(["lat_i", "lon_i"])["n"].sum()
    mine = out["modis"]["detections"]
    out["modis"]["busier_than_pct"] = int(round(100 * (modis < mine).mean())) if len(modis) else None
    out["text"] = describe(out, month)
    return out


def describe(h: dict, month: int | None = None) -> str:
    m = h["modis"]
    if not m["detections"]:
        return (f"No MODIS fire detections in this 0.5° cell (about 55 km) in "
                f"{m['years_covered'][0]}–{m['years_covered'][1]}.")
    parts = [f"MODIS saw fire in this 0.5° cell (about 55 km) in {m['years_with_fire']} of {m['of_years']} years "
             f"({m['detections']} detections, peak month {m['peak_month']}, worst year {m['worst_year']})."]
    if month and "same_month_years" in m:
        parts.append(f"{MONTHS[month - 1]} had fire in {m['same_month_years']} of those years.")
    if m.get("busier_than_pct") is not None:
        parts.append(f"Busier than {m['busier_than_pct']}% of the cells with fire in the grid.")
    return " ".join(parts)
