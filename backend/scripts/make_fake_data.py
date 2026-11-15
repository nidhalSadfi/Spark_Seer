"""FAKE FIRMS-like data for local testing and UI work. Never commit it or present it as results.

Writes data/fake/ (gitignored):
    cells.parquet                synthetic monthly detection counts (same schema as pipeline/ingest.py)
    harmonized.parquet           made by the real pipeline/calibrate.py from those counts
    harmonized_validation.json   calibrate.py's report on the fake counts, marked "synthetic": true

Usage:
    python scripts/make_fake_data.py [--optional]   # --optional adds low/high/MODIS_days/VIIRS_days
    EMBER_DATA=data/fake/harmonized.parquet uvicorn api.main:app --reload --port 8000
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from api import core  # noqa: E402
from pipeline import calibrate  # noqa: E402

OUT = ROOT / "data" / "fake"
YEARS = range(2001, 2026)  # MODIS over the whole period, VIIRS S-NPP from 2012
# Planted anomalies (area, year, month, factor), so the UI has known "high" and "low" months.
PLANTED = [("bizerte", 2008, 7, 5.0), ("ne_algeria", 2014, 8, 4.0),
           ("kabylie", 2019, 9, 5.0), ("petite_kabylie", 2016, 8, 0.05)]


def cells_of(area: dict) -> set:
    """Every 0.5 degree cell (cy, cx) the API selects for this area's bbox."""
    x0, y0, x1, y1 = (int(np.floor(v / core.CELL_DEG)) for v in area["bbox"])
    return {(cy, cx) for cy in range(y0, y1 + 1) for cx in range(x0, x1 + 1)}


def fake_counts(rng) -> pd.DataFrame:
    """Monthly MODIS / VIIRS detection counts per cell, in pipeline/ingest.py's output schema."""
    areas = {a["id"]: cells_of(a) for a in core.get_areas()}
    months = np.arange(1, 13)
    season = 0.1 + 6 * np.exp(-((months - 8) ** 2) / 3)  # summer peak; sparse winters -> low_confidence
    viirs_per_modis = 3.0 + 0.5 * np.cos(2 * np.pi * (months - 1) / 12)  # VIIRS sees more small fires
    wet_dry = dict(zip(YEARS, rng.lognormal(0, 0.25, len(YEARS))))   # year-to-year swings, all areas
    rows = []
    for cy, cx in sorted(set().union(*areas.values())):
        busy = rng.lognormal(0.5, 0.8)  # some cells burn far more than others
        for year in YEARS:
            for i, month in enumerate(months):
                lam = busy * season[i] * wet_dry[year]
                for area, y, m, factor in PLANTED:
                    if (y, m) == (year, month) and (cy, cx) in areas[area]:
                        lam *= factor
                rows.append((cy, cx, year, month, "MODIS", rng.poisson(lam)))
                if year >= 2012:
                    rows.append((cy, cx, year, month, "VIIRS", rng.poisson(lam * viirs_per_modis[i])))
    df = pd.DataFrame(rows, columns=["cy", "cx", "year", "month", "sensor", "n"])
    return df[df.n > 0].reset_index(drop=True)  # ingest.py only writes cell-months with detections


def main(out_dir: Path, optional: bool = False) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cells, harmonized = out_dir / "cells.parquet", out_dir / "harmonized.parquet"
    fake_counts(np.random.default_rng(2026)).to_parquet(cells, index=False)
    calibrate.main(str(cells), str(harmonized))  # the real pipeline step, on fake counts

    report_path = out_dir / "harmonized_validation.json"
    report = json.loads(report_path.read_text())
    report.update(synthetic=True,
                  note="SYNTHETIC test data from scripts/make_fake_data.py, not real FIRMS data",
                  planted_anomalies=[dict(area=a, year=y, month=m, factor=f) for a, y, m, f in PLANTED])
    report_path.write_text(json.dumps(report, indent=2))

    if optional:  # the optional /calendar fields; their real definitions are the data person's call
        h = pd.read_parquet(harmonized)
        pre = h.year < 2012  # pretend bounds exist only where the value is MODIS * k
        h["low"], h["high"] = (h.harmonized * 0.75).where(pre), (h.harmonized * 1.25).where(pre)
        h["MODIS_days"], h["VIIRS_days"] = h.MODIS.clip(upper=28), h.VIIRS.clip(upper=28)
        h.to_parquet(harmonized, index=False)
    print(f"FAKE data written to {out_dir}. Serve it with: EMBER_DATA={harmonized} uvicorn api.main:app")


if __name__ == "__main__":
    main(OUT, optional="--optional" in sys.argv[1:])
