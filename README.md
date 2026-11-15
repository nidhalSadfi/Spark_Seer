# EmberLedger

A harmonized MODIS + VIIRS burning-activity calendar, built for the NASA Space Apps Challenge 2026
(Tunis), challenge **Harmonization of MODIS and VIIRS Hot Spots**.

Repository layout:

| Folder | Owner | What |
|---|---|---|
| `api/`, `scripts/`, `tests/`, `web/demo/` | backend | the API, data build scripts, tests, demo page |
| `harmonization-modis-viirs/` | data | FIRMS downloader (`firms_tool`), the real ingest, data exploration |
| `meteomatics-weather/` | weather | Meteomatics forecast grid and map, mounted at `/meteomatics/*` |
| `pipeline/calibrate.py` | data | MODIS -> VIIRS ratios, validation, harmonized series |

## Run it

The real data is committed (`data/harmonized.parquet`, `data/fires.csv`), so serving needs no build:

```bash
# Use a virtualenv: a system Python with old numpy breaks pyarrow ("Unable to find a usable engine").
python3 -m venv .venv            # Debian/Ubuntu: needs `sudo apt install python3-venv` first
source .venv/bin/activate        # gives you `python`, `pip`, `uvicorn` from the venv
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
# open http://localhost:8000/ for the demo page, or GET /calendar?area=bizerte&start=2003&end=2025
```

Rebuild the data from NASA FIRMS (public files, no login; about 2 minutes and 122 MB):

```bash
pip install -r requirements-dev.txt
# 1. Download the yearly country CSVs for Tunisia and Algeria into data/raw/ (gitignored)
(cd harmonization-modis-viirs && python -m firms_tool archive --sensors modis_c61 --years 2001-2025 \
    --countries Tunisia Algeria --out ../data/raw)
(cd harmonization-modis-viirs && python -m firms_tool archive --sensors viirs_snpp --years 2012-2025 \
    --countries Tunisia Algeria --out ../data/raw)
# 2. Count detections per 0.5 degree cell and month; 34 = keep north of 34N (drops Saharan gas flares)
python harmonization-modis-viirs/pipeline/ingest.py data/raw data/cells.parquet 34
# 3. Fit MODIS->VIIRS ratios, validate on held-out years, write data/harmonized.parquet (+ _validation.json)
python scripts/build_data.py          # renames ingest's lat_i/lon_i to cy/cx, runs pipeline/calibrate.py
# 4. Fire layer for /meteomatics/fires/calendar, and the offline demo backups
python scripts/export_fires.py        # data/fires.csv
python scripts/export_backups.py      # web/backup/
```

Expected step 2 output: MODIS 67,230 and VIIRS 137,470 detections, 16,607 rows (the same as
`harmonization-modis-viirs/docs/p1-ingest.md`).

Meteomatics forecasts (needs an account; credentials stay in the gitignored
`meteomatics-weather/.env`, see `meteomatics-weather/.env.weather.example`):

```bash
cd meteomatics-weather
python -m pipeline.weather       # data/weather.json: the two named points (/meteomatics/weather)
python -m pipeline.weather_map   # data/weather_grid.json: 35 cells (/meteomatics/weather/map, /cell, /point)
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

## Demo page

`web/demo/` is a plain HTML/JS page (no build step, no CDN) that exercises every endpoint and
explains the method. The API serves it: `http://localhost:8000/` (or the Render URL) redirects to
`/web/demo/`. It shows the burning calendar (hover a cell, click it for the Evidence Passport),
yearly MODIS / VIIRS / harmonized totals across the 2012 switch, the typical year, the validation
errors, the weather context, table views, and an **API log** with the raw JSON of every request.

- Link parameters: `?area=bizerte&month=2008-07&theme=dark`, `?bbox=146,-38,153,-28` for any box
  (minLon,minLat,maxLon,maxLat), and `?api=https://<service>.onrender.com` when the page is served
  from elsewhere.
- Offline: if the API is unreachable, the page reads `web/backup/*.json` and says so. To try it
  without the API: `python -m http.server -d web 8080`, then open `http://localhost:8080/demo/`.
- It is a test and explanation page. The team's React app lives on the `frontend` branch and can
  reuse its `fetch` calls (`web/demo/app.js`).

## Deploy (Render, free web service)

1. The files the API serves are committed: `data/harmonized.parquet`,
   `data/harmonized_validation.json`, `data/areas.json`, `data/fires.csv` and `web/backup/`.
   Never commit `data/raw/`: it is gitignored because the raw CSVs are ~122 MB.
2. Merge to `main` and push. `render.yaml` deploys from `main`.
3. Render dashboard: **New > Blueprint**, pick this GitHub repo, apply. `render.yaml` sets the free
   plan, the build command (`pip install -r requirements.txt`, then the Meteomatics refresh), the
   start command `uvicorn api.main:app --host 0.0.0.0 --port $PORT`, the health check `/health`
   and `PYTHON_VERSION=3.11.9`. It asks for `METEOMATICS_USER`, `METEOMATICS_PASSWORD` and
   `ANTHROPIC_API_KEY`. All three are optional: leave them empty and the build still succeeds.
   Without Blueprints: **New > Web Service**, runtime Python, same build and start commands,
   env var `PYTHON_VERSION=3.11.9`, health check path `/health`.
4. Open `https://<service>.onrender.com/health`: expect `"status": "ok"` and `"rows": 13070`.
   A 503 there means the data files were not committed, and Render marks the deploy as failed.

Notes:
- Free instances sleep after about 15 minutes idle, and the next request takes up to a minute.
  Open `/health` a few minutes before the demo.
- The core API needs no secrets. Any API keys for the teammate routers go in Render's
  **Environment** tab, never in the repo: `ANTHROPIC_API_KEY` turns on the passport's optional AI
  wording, and `EMBER_LLM_MODEL` overrides its model (default `claude-haiku-5-5`). Without the
  key, the passport is still returned, without AI text.
- Meteomatics forecasts are fetched once per deploy (build command), so they go stale after
  3 hours: `/meteomatics/weather*` then returns them with `"stale": true`. Redeploy (Render:
  **Manual Deploy**) before the demo for a fresh forecast. Without credentials those routes
  return 503, and `/weather` (Open-Meteo, no key) still works.
- If the server dies mid-demo, the front end can read `web/backup/*.json` (same JSON as the API).
  Run `python scripts/export_backups.py` once the real data is in place, then commit `web/backup/`.

## API

Base URL: `http://localhost:8000` locally, `https://<service>.onrender.com` on Render. All core
endpoints are `GET` and return JSON. CORS allows any origin for `GET` and `POST`. FastAPI serves
interactive docs at `/docs`.

Errors return `{"detail": "..."}`: **400** for bad parameters, **503** when a data file is missing
(the message names the file).

Contract: the front end builds against `web/mock_calendar.json`. Existing fields are never renamed
or removed, only added to. Example values below are real (the committed data) unless marked.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness, plus which data file is loaded and how many rows it has |
| `GET /areas` | Named demo areas (ids for `/calendar?area=`) |
| `GET /calendar` | Monthly harmonized burning-activity calendar for a bbox or an area |
| `GET /validation` | Held-out validation report of the MODIS->VIIRS harmonization |
| `POST /investigate` | Evidence Passport for one area and month (`api/passport.py`, teammate) |
| `GET /weather?area=<id>` | 3-day Open-Meteo weather context plus historical context (`api/weather.py`, teammate) |
| `GET /meteomatics/weather?area=<id>` | Meteomatics 72 h forecast at a named point (`meteomatics-weather/`, teammate) |
| `GET /meteomatics/weather/map?window=0..2&variable=` | Forecast GeoJSON for the 35-cell grid, one 24 h window |
| `GET /meteomatics/weather/grid-meta` | Grid geometry and bounds (works without a forecast) |
| `GET /meteomatics/weather/cell/<grid_id>` | 72 h forecast for one cell, e.g. `tn_37.0_9.5` |
| `GET /meteomatics/weather/point?lat=&lon=` | Forecast of the cell that contains a point |
| `GET /meteomatics/fires/calendar?grid_id=&day=&limit=` | Real VIIRS detections per day and as map points (`data/fires.csv`) |

### `GET /health`

No parameters.

```json
{"status": "ok", "data_file": "data/harmonized.parquet", "rows": 13070}
```

`rows` counts the rows in `harmonized.parquet` (cell x month with detections). If the file is
missing the response is a 503.

### `GET /areas`

No parameters. Read from `data/areas.json`, the data person's demo boxes
(`harmonization-modis-viirs/docs/p1/demo_areas.json`).

```json
{"areas": [
  {"id": "bizerte", "name": "Bizerte / NW Tunisia", "bbox": [8.3, 36.0, 9.8, 37.2]},
  {"id": "kabylie", "name": "Kabylie", "bbox": [3.7, 36.3, 5.2, 37.0]},
  {"id": "ne_algeria", "name": "NE Algeria", "bbox": [7.0, 36.4, 8.6, 37.0]},
  {"id": "petite_kabylie", "name": "Petite Kabylie", "bbox": [5.3, 36.4, 6.9, 37.1]}
]}
```

`bbox` is `[minLon, minLat, maxLon, maxLat]` in decimal degrees.

### `GET /calendar`

| Parameter | Required | Default | Meaning |
|---|---|---|---|
| `bbox` | one of `bbox` / `area` | | `minLon,minLat,maxLon,maxLat`, e.g. `8.3,36.0,9.8,37.2` |
| `area` | one of `bbox` / `area` | | an `id` from `/areas`, e.g. `bizerte` |
| `start` | no | `2001` | first year |
| `end` | no | `2025` | last year (inclusive) |

It returns 400 when neither or both of `bbox` and `area` are given, when the area id is unknown,
or when the bbox is malformed. Examples: `/calendar?area=bizerte&start=2003&end=2025`,
`/calendar?bbox=8.0,35.0,11.0,37.5`. Below: `/calendar?area=kabylie`, one month shown (August 2021,
the Kabylie fires).

```json
{
  "months": [
    {"year": 2021, "month": 8, "modis_raw": 1048.0, "viirs_raw": 4483.0, "harmonized": 4483.0,
     "support": 3897.0, "z": 8.3, "flag": "high"}
  ],
  "meta": {
    "cells": 8,
    "critical_months": [7, 8, 9],
    "validation": {"train_years": [2012, 2019], "test_years": [2020, 2021],
                   "error_pct_uncorrected": 72.1, "error_pct_global_ratio": 13.7,
                   "error_pct_cell_month_ratio": 11.5, "k_global": 3.48, "test_months": 24}
  }
}
```

- Counts are JSON numbers rounded to one decimal (`1048.0`).
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
 "error_pct_uncorrected": 72.1, "error_pct_global_ratio": 13.7,
 "error_pct_cell_month_ratio": 11.5, "k_global": 3.48, "test_months": 24}
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

`meteomatics-weather/api/weather_router_example.py` is loaded by path and mounted under
`/meteomatics`, because its own `GET /weather` would clash with `api/weather.py`'s. Its modules
import `pipeline.*` from `meteomatics-weather/pipeline/`; neither `pipeline/` folder has an
`__init__.py`, so Python merges them into one namespace (no module names overlap). Its routes only
read caches: forecasts from `meteomatics-weather/data/weather*.json` (503 until a refresh has run),
fire points from `NASA_FIRMS_CSV` (default `data/fires.csv`). Status codes: 404 unknown area or
cell, 422 bad window or date, 503 no forecast cache.

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
scripts/export_backups.py, scripts/build_data.py, scripts/export_fires.py, render.yaml, the
wiring of the data and Meteomatics branches) drafted with Claude Code (Anthropic).
(Keep this list current: it goes in the "Use of SI" field.)
