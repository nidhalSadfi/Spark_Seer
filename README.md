# EmberLedger

A harmonized MODIS + VIIRS burning-activity calendar, built for the NASA Space Apps Challenge 2026
(Tunis), challenge **Harmonization of MODIS and VIIRS Hot Spots**.

## Run it

```bash
pip install -r requirements.txt
# 1. Put unzipped FIRMS yearly country CSVs (MODIS C6.1 + VIIRS S-NPP 375 m) under data/raw/
python pipeline/ingest.py data/raw data/cells.parquet 34   # 34 = keep north of 34N (drops Saharan gas flares)
# 2. Fit MODIS->VIIRS ratios, validate on held-out years, write the harmonized series
python pipeline/calibrate.py data/cells.parquet data/harmonized.parquet
# 3. Serve the calendar
uvicorn api.main:app --reload --port 8000
# GET http://localhost:8000/calendar?bbox=8.0,35.0,11.0,37.5&start=2003&end=2025
```

## API contract (front end builds against `web/mock_calendar.json`)

`GET /calendar?bbox=minLon,minLat,maxLon,maxLat&start=YYYY&end=YYYY` (example values are synthetic)

```json
{
  "months": [
    {"year": 2017, "month": 8, "modis_raw": 82, "viirs_raw": 246, "harmonized": 246,
     "support": 410, "z": 10.5, "flag": "high"}
  ],
  "meta": {
    "cells": 30,
    "critical_months": [7, 8, 9],
    "validation": {"train_years": [2012, 2019], "test_years": [2020, 2021],
                   "error_pct_uncorrected": 66.4, "error_pct_global_ratio": 23.1,
                   "error_pct_cell_month_ratio": 20.9, "k_global": 3.04, "test_months": 24}
  }
}
```

- `harmonized`: VIIRS-equivalent detections. Before 2012 = MODIS x ratio k; from 2012 = observed VIIRS.
- `flag`: `high` / `low` (robust z-score vs the same month in other years, |z| >= 3),
  `low_confidence` (pre-2012 month whose ratio rests on < 30 MODIS detections), or `normal`.
- `support`: MODIS training detections behind the ratio for that calendar month.
- `critical_months`: months whose median activity is in the top quarter of the year.

`web/mock_calendar.json` is **synthetic** data for UI work only. Never show it as real results.

## Method and limits

- Filters: MODIS confidence >= 30; VIIRS confidence nominal/high; `type == 0` (vegetation fires).
  VIIRS restricted to Suomi NPP so the reference is a single instrument.
- Grid: 0.5 degree cells x month. Ratio per cell and calendar month, shrunk toward the
  regional monthly ratio (pseudo-count 20) so sparse cells stay stable.
- Validation: fit 2012-2019, predict 2020-2021 VIIRS from MODIS, compare with no correction
  and with one global ratio.
- Scope: Tunisia + Algeria north of 34N. South of it, persistent oil/gas flares on Saharan fields
  pass the FIRMS `type == 0` filter and would swamp the fire signal.
- Limits: fires too small for MODIS cannot be recovered before 2012 (pre-2012 values are a
  lower bound); scaling MODIS by k also scales its noise.

## Data

NASA FIRMS active fire data (MODIS Collection 6.1 MCD14ML, VIIRS 375 m VNP14IMG), from
https://firms.modaps.eosdis.nasa.gov/. We acknowledge the use of data from NASA's Fire Information
for Resource Management System (FIRMS), part of NASA's LANCE and ESDIS.

## Use of AI

Pipeline and API starter code drafted with Claude (Anthropic) during the hackathon and reviewed
and modified by the team. (Keep this list current: it goes in the "Use of SI" field.)
