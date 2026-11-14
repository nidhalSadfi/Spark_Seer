# EmberLedger

A harmonized MODIS + VIIRS burning-activity calendar, built for the NASA Space Apps Challenge 2026
(Tunis), challenge **Harmonization of MODIS and VIIRS Hot Spots**.

## Run it

```bash
# Use a virtualenv: a system Python with old numpy breaks pyarrow ("Unable to find a usable engine").
python3 -m venv .venv            # Debian/Ubuntu: needs `sudo apt install python3-venv` first
source .venv/bin/activate        # gives you `python`, `pip`, `uvicorn` from the venv
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

Fake data, to run the API and front end before the real data exists:

```bash
python scripts/make_fake_data.py              # writes data/fake/ (gitignored); --optional adds low/high/*_days
EMBER_DATA=data/fake/harmonized.parquet uvicorn api.main:app --reload --port 8000
```

The script generates fake monthly counts for the four areas (2001-2025, summer peak, sparse
winters) and runs the real `pipeline/calibrate.py` on them. It plants four anomalies so every flag
appears. Its validation report says `"synthetic": true` and lists the planted anomalies, so
`/validation` and `/calendar` `meta.validation` show that the data is fake. `export_backups.py`
refuses to write it into `web/backup/`; pass another folder to test the offline mode. **Never
commit this data or show it as results.**

## Deploy (Render, free web service)

1. Commit the files the API serves: `data/harmonized.parquet`, `data/harmonized_validation.json`
   (small) and `data/areas.json`. Never commit `data/raw/`: it is gitignored because the raw CSVs
   are ~127 MB and one file is over GitHub's 100 MB limit.
2. Merge to `main` and push. `render.yaml` deploys from `main`. It lives at the **repo root** (Render only
   reads it there) and sets `rootDir: backend`, because this API sits in the `backend/` folder of the monorepo.
3. Render dashboard: **New > Blueprint**, pick this GitHub repo, apply. `render.yaml` sets the free
   plan, `pip install -r requirements.txt`, the start command
   `uvicorn api.main:app --host 0.0.0.0 --port $PORT`, the health check `/health` and
   `PYTHON_VERSION=3.11.9`.
   Without Blueprints: **New > Web Service**, runtime Python, **Root Directory `backend`**, same build and start commands,
   env var `PYTHON_VERSION=3.11.9`, health check path `/health`.
4. Open `https://<service>.onrender.com/health`: expect `"status": "ok"` and the real row count.
   A 503 there means the data files were not committed, and Render marks the deploy as failed.

Notes:
- Free instances sleep after about 15 minutes idle, and the next request takes up to a minute.
  Open `/health` a few minutes before the demo.
- The core API needs no secrets. Any API keys for the teammate routers go in Render's
  **Environment** tab, never in the repo: `ANTHROPIC_API_KEY` turns on the passport's optional AI
  wording, and `EMBER_LLM_MODEL` overrides its model (default `claude-haiku-5-5`). Without the
  key, the passport is still returned, without AI text.
- If the server dies mid-demo, the front end can read `web/backup/*.json` (same JSON as the API).
  Run `python scripts/export_backups.py` once the real data is in place, then commit `web/backup/`.

## API

Base URL: `http://localhost:8000` locally, `https://<service>.onrender.com` on Render. All core
endpoints are `GET` and return JSON. CORS allows any origin for `GET` and `POST`. FastAPI serves
interactive docs at `/docs`.

Errors return `{"detail": "..."}`: **400** for bad parameters, **503** when a data file is missing
(the message names the file).

Contract: the front end builds against `web/mock_calendar.json`. Existing fields are never renamed
or removed, only added to. All example values below are synthetic or illustrative.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness, plus which data file is loaded and how many rows it has |
| `GET /areas` | Named demo areas (ids for `/calendar?area=`) |
| `GET /calendar` | Monthly harmonized burning-activity calendar for a bbox or an area |
| `GET /validation` | Held-out validation report of the MODIS->VIIRS harmonization |
| `POST /investigate` | Evidence Passport for one area and month (`api/passport.py`, teammate) |
| `GET /weather?area=<id>` | 3-day Open-Meteo weather context plus historical context (`api/weather.py`, teammate) |

### `GET /health`

No parameters.

```json
{"status": "ok", "data_file": "data/harmonized.parquet", "rows": 48210}
```

`rows` counts the rows in `harmonized.parquet` (cell x month with detections). If the file is
missing the response is a 503.

### `GET /areas`

No parameters. Read from `data/areas.json`. The boxes are approximate and will be refined.

```json
{"areas": [
  {"id": "bizerte", "name": "Bizerte", "bbox": [9.0, 36.8, 10.3, 37.4]},
  {"id": "nw_tunisia", "name": "Northwest Tunisia", "bbox": [8.3, 36.2, 9.2, 37.0]},
  {"id": "kabylie", "name": "Kabylie", "bbox": [3.7, 36.3, 5.2, 37.0]},
  {"id": "ne_algeria", "name": "Northeast Algeria", "bbox": [7.0, 36.4, 8.6, 37.0]}
]}
```

`bbox` is `[minLon, minLat, maxLon, maxLat]` in decimal degrees.

### `GET /calendar`

| Parameter | Required | Default | Meaning |
|---|---|---|---|
| `bbox` | one of `bbox` / `area` | | `minLon,minLat,maxLon,maxLat`, e.g. `9.0,36.8,10.3,37.4` |
| `area` | one of `bbox` / `area` | | an `id` from `/areas`, e.g. `bizerte` |
| `start` | no | `2001` | first year |
| `end` | no | `2025` | last year (inclusive) |

It returns 400 when neither or both of `bbox` and `area` are given, when the area id is unknown,
or when the bbox is malformed. Examples: `/calendar?area=bizerte&start=2003&end=2025`,
`/calendar?bbox=8.0,35.0,11.0,37.5`.

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
- `months` is a full grid: 12 entries per year from `start` to `end`. Months with no detections
  are zeros.
- Every 0.5 degree grid cell that overlaps (or touches) the bbox is included, so the covered area can be
  slightly larger than the bbox. `meta.cells` is the number of cells with detections.
- Optional fields `low`, `high`, `MODIS_days`, `VIIRS_days` appear **only if**
  `harmonized.parquet` has those columns. The API never computes them. They are summed over the
  area's cells like the counts (so `*_days` is a total over cells, not calendar days), and they
  are `null` for a month where any cell lacks the value.
- An area with no detections returns
  `{"months": [], "meta": {"cells": 0, "note": "no detections in this area"}}`.

`web/mock_calendar.json` is **synthetic** data for UI work only. Never show it as real results.

### `GET /validation`

No parameters. This is the report `pipeline/calibrate.py` writes to
`data/harmonized_validation.json`: fit on the training years, predict VIIRS from MODIS on the
held-out test years, compared at region x month level.

```json
{"train_years": [2012, 2019], "test_years": [2020, 2021],
 "error_pct_uncorrected": 66.4, "error_pct_global_ratio": 23.1,
 "error_pct_cell_month_ratio": 20.9, "k_global": 3.04, "test_months": 24}
```

- `error_pct_*`: mean absolute monthly error as a % of the mean observed VIIRS count. The three
  variants are raw MODIS, one global ratio, and the per-cell-month ratio used by the API.
- `k_global`: overall VIIRS/MODIS ratio over the training years. `test_months`: months scored.
- `series` (per-month detail) is included **only if** the file contains one. `calibrate.py`
  does not write it today. `/calendar`'s `meta.validation` always leaves `series` out.

### Teammate routers

`api/passport.py` and `api/weather.py` are mounted automatically when they exist. Each must export
`router` (a FastAPI `APIRouter`), and their endpoints are documented by their owner and at `/docs`.
If one is missing or fails to import, the API still starts and logs
`WARNING: passport router not loaded: <reason>`.

Code that needs the data should use `api/core.py`: `get_data()`, `get_validation()`,
`get_areas()`, `get_area(id)`, `calendar_for_bbox(bbox, start, end)`. Data loads on first use and
is cached.

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
and modified by the team. Backend core (api/core.py, /areas, /validation, /health, tests,
scripts/export_backups.py, render.yaml) drafted with Claude Code (Anthropic).
(Keep this list current: it goes in the "Use of SI" field.)
