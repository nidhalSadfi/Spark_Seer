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
# 3. Serve the API (data path: env EMBER_DATA, default data/harmonized.parquet)
uvicorn api.main:app --reload --port 8000
# GET http://localhost:8000/calendar?area=bizerte&start=2003&end=2025
# 4. Offline demo fallback: writes web/backup/<area_id>.json + validation.json from the real data
python scripts/export_backups.py
```

Tests (synthetic data in a tmp dir, never the real files):

```bash
pip install -r requirements-dev.txt
pytest
```

## Deploy (Render, free web service)

1. Commit the files the API serves: `data/harmonized.parquet`, `data/harmonized_validation.json`
   (small) and `data/areas.json`. Never commit `data/raw/`: it is gitignored because the raw CSVs
   are ~127 MB and one file is over GitHub's 100 MB limit.
2. Merge to `main` and push. `render.yaml` deploys from `main`.
3. Render dashboard: **New > Blueprint**, pick this GitHub repo, apply. `render.yaml` sets the free
   plan, `pip install -r requirements.txt`, the start command
   `uvicorn api.main:app --host 0.0.0.0 --port $PORT`, the health check `/health` and
   `PYTHON_VERSION=3.11.9`.
   Without Blueprints: **New > Web Service**, runtime Python, same build and start commands,
   env var `PYTHON_VERSION=3.11.9`, health check path `/health`.
4. Open `https://<service>.onrender.com/health`: expect `"status": "ok"` and the real row count.
   A 503 there means the data files were not committed, and Render marks the deploy as failed.

Notes:
- Free instances sleep after about 15 minutes idle, and the next request takes up to a minute.
  Open `/health` a few minutes before the demo.
- The core API needs no secrets. Any API keys for the teammate routers go in Render's
  **Environment** tab, never in the repo.
- If the server dies mid-demo, the front end can read `web/backup/*.json` (same JSON as the API).
  Run `python scripts/export_backups.py` once the real data is in place, then commit `web/backup/`.

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
