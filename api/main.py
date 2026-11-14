"""Step 3: the one endpoint the front end needs.

Run:  uvicorn api.main:app --reload --port 8000
Call: GET /calendar?bbox=8.0,35.0,11.0,37.5          (minLon,minLat,maxLon,maxLat)
"""
import json
import os

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

CELL_DEG = 0.5
DATA = os.environ.get("EMBER_DATA", "data/harmonized.parquet")
MIN_SUPPORT = 30  # MODIS detections behind a ratio before we call it "reliable"
MIN_COUNT = 10    # months below this (and normally below it) are never flagged as anomalies
Z_FLAG = 3.0      # robust z-score threshold; 2 flags too many months on noisy counts

app = FastAPI(title="EmberLedger")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
H = pd.read_parquet(DATA)
VALIDATION = json.load(open(DATA.replace(".parquet", "_validation.json")))


@app.get("/calendar")
def calendar(bbox: str, start: int = 2001, end: int = 2025):
    try:
        x0, y0, x1, y1 = map(float, bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be minLon,minLat,maxLon,maxLat")
    sel = H[(H.cx >= int(np.floor(x0 / CELL_DEG))) & (H.cx <= int(np.floor(x1 / CELL_DEG)))
            & (H.cy >= int(np.floor(y0 / CELL_DEG))) & (H.cy <= int(np.floor(y1 / CELL_DEG)))
            & H.year.between(start, end)]
    if sel.empty:
        return {"months": [], "meta": {"cells": 0, "note": "no detections in this area"}}

    m = sel.groupby(["year", "month"]).agg(
        modis_raw=("MODIS", "sum"), viirs_raw=("VIIRS", "sum"),
        harmonized=("harmonized", "sum")).reset_index()
    # Support = training MODIS detections behind this area's ratio, per calendar month.
    support = sel.drop_duplicates(["cy", "cx", "month"]).groupby("month").support.sum()

    # Fill missing months with zeros so the calendar grid is complete.
    full = pd.MultiIndex.from_product([range(start, end + 1), range(1, 13)], names=["year", "month"])
    m = m.set_index(["year", "month"]).reindex(full, fill_value=0).reset_index()
    m["support"] = m.month.map(support).fillna(0)

    # Robust anomaly score vs. the same calendar month in other years (median / MAD).
    g = m.groupby("month").harmonized
    med, mad = g.transform("median"), g.transform(lambda s: (s - s.median()).abs().median())
    m["z"] = ((m.harmonized - med) / (1.4826 * mad.replace(0, np.nan))).fillna(0).round(2)
    # Ignore "anomalies" in near-empty months: tiny counts swing wildly by chance.
    busy = (m.harmonized >= MIN_COUNT) | (med >= MIN_COUNT)
    m["flag"] = np.select([(m.z >= Z_FLAG) & busy, (m.z <= -Z_FLAG) & busy], ["high", "low"], "normal")
    m.loc[(m.year < 2012) & (m.support < MIN_SUPPORT), "flag"] = "low_confidence"

    clim = m.groupby("month").harmonized.median()
    critical = [int(mo) for mo in clim.index if clim[mo] >= clim.quantile(0.75) and clim[mo] > 0]

    return {
        "months": m.round(1).to_dict(orient="records"),
        "meta": {"cells": int(sel[["cy", "cx"]].drop_duplicates().shape[0]),
                 "critical_months": critical, "validation": VALIDATION},
    }
