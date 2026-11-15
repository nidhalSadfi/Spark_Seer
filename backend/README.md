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

World data (local only; 6.5 GB download, about 45 minutes; everything goes to `data/world/`, which
is gitignored):

```bash
(cd harmonization-modis-viirs && python -m firms_tool archive --sensors modis_c61 --years 2001-2025 \
    --global --out ../data/world/raw)
(cd harmonization-modis-viirs && python -m firms_tool archive --sensors viirs_snpp --years 2012-2025 \
    --global --out ../data/world/raw)
python scripts/ingest_world.py data/world/raw data/world/cells.parquet --workers 3   # ~4 min, <2 GB RAM
python scripts/build_data.py data/world/cells.parquet data/world/harmonized.parquet
EMBER_DATA=data/world/harmonized.parquet uvicorn api.main:app --port 8000
# any place: /calendar?bbox=148,-38,154,-28 or http://localhost:8000/web/demo/?bbox=148,-38,154,-28
```

`ingest_world.py` is the data person's ingest at world scale: it imports their filters and gives
identical output (checked on the North Africa files), but counts one file chunk at a time instead of
holding 334 million detections in memory. Run on 2026-11-15: 333.7 million detections (MODIS
102.4 M, VIIRS 231.3 M), 5.0 M harmonized cell-months, 54,400 cells. The API then needs about
0.7 GB of RAM (more than Render's free 512 MB) and answers `/calendar` in about 60 ms (0.7 s for the
whole world). The world validation (5.4 % error) compares world-wide monthly totals, where regional
errors cancel: it is not comparable with the 11.5 % for North Africa.

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
2. Merge to `main` and push. `render.yaml` (at the repo root, not in `backend/`) deploys from
   `main`.
3. Render dashboard: **New > Blueprint**, pick this GitHub repo, apply. `render.yaml` sets the free
   plan, root directory `backend`, the build command (`pip install -r requirements.txt`, then the Meteomatics refresh), the
   start command `uvicorn api.main:app --host 0.0.0.0 --port $PORT`, the health check `/health`
   and `PYTHON_VERSION=3.11.9`. It asks for `METEOMATICS_USER`, `METEOMATICS_PASSWORD` and
   `ANTHROPIC_API_KEY`. All three are optional: leave them empty and the build still succeeds.
   Without Blueprints: **New > Web Service**, runtime Python, root directory `backend`, same build and start commands,
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
endpoints are `GET` and return JSON; only `/reports` also takes `POST`, `PATCH` and `DELETE`. CORS
allows any origin for `GET`, `POST`, `PATCH` and `DELETE`, with any request header (the reports
page sends `X-Admin-Token`). FastAPI serves interactive docs at `/docs`.

Errors return `{"detail": "..."}`: **400** for bad parameters, **503** when a data file is missing
(the message names the file) or when an endpoint needs the database and none is usable (see
[Database-backed endpoints](#database-backed-endpoints)).

Contract: the front end builds against `web/mock_calendar.json`. Existing fields are never renamed
or removed, only added to. Example values below are real (the committed data) unless marked.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness, plus which data file is loaded and how many rows it has |
| `GET /areas` | Named demo areas (ids for `/calendar?area=`) |
| `GET /calendar` | Monthly harmonized burning-activity calendar for a bbox or an area |
| `GET /validation` | Held-out validation report of the MODIS->VIIRS harmonization |
| `GET /processing/status` | Scheduler state per job kind, approved dataset and calibration versions, last error (database) |
| `GET /processing/jobs` | Processing jobs, newest first, paginated, filterable by kind and status (database) |
| `GET /processing/jobs/<id>` | One job: counts, steps, gates, soft flags, decision, verdict, LLM attempts (database) |
| `GET /processing/datasets` | Dataset version history and calibration versions (database) |
| `GET /fires/live` | Stored NRT FIRMS detections of the last hours, for the fire map (database) |
| `GET/POST /reports`, `PATCH/DELETE /reports/<id>` | Community fire reports (database) |
| `POST /investigate` | Evidence Passport for one area and month (`api/passport.py`, teammate) |
| `GET /weather?area=<id>` or `?lat=&lon=` | 3-day Open-Meteo weather context plus historical context, for a named area or any point (`api/weather.py`, teammate) |
| `GET /meteomatics/weather?area=<id>` | Meteomatics 72 h forecast at a named point (`meteomatics-weather/`, teammate) |
| `GET /meteomatics/weather/map?window=0..2&variable=` | Forecast GeoJSON for the 35-cell grid, one 24 h window |
| `GET /meteomatics/weather/grid-meta` | Grid geometry and bounds (works without a forecast) |
| `GET /meteomatics/weather/cell/<grid_id>` | 72 h forecast for one cell, e.g. `tn_37.0_9.5` |
| `GET /meteomatics/weather/point?lat=&lon=` | Forecast of the cell that contains a point |
| `GET /meteomatics/fires/calendar?grid_id=&day=&limit=` | Real VIIRS detections per day and as map points (`data/fires.csv`) |

### `GET /health`

No parameters.

```json
{"status": "ok", "data_file": "data/harmonized.parquet", "rows": 13070,
 "data_source": "parquet", "dataset_version_id": null}
```

`rows` counts the rows of the table `/calendar` uses (cell x month with detections).
`data_source` says where that table comes from: `"database"` (the approved `cell_month` table;
`dataset_version_id` is the approved version) or `"parquet"` (`data_file`; `dataset_version_id`
null). If the parquet is needed and missing the response is a 503.

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
or when the bbox is malformed (also minLon > maxLon or minLat > maxLat: send a box across the
antimeridian as two boxes). Examples: `/calendar?area=bizerte&start=2003&end=2025`,
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

Data source: with `DATABASE_URL` set and an approved dataset version in the database, the table
behind `/calendar` is the approved `cell_month` table (spec §10), cached per approved
`dataset_version_id`; the version is re-checked at most every `API_VERSION_CHECK_S` seconds
(default 30), so a newly promoted version shows up within that time. Without a usable database
(unset, unreachable, not bootstrapped) it is the parquet file, exactly as before. The fields and
the maths are the same either way:

- The database stores VIIRS as NULL before 2012 (the sensor did not exist); `viirs_raw` stays `0`
  for those months, as with the parquet.
- Optional `MODIS_days` / `VIIRS_days` come from the database only when every stored row has them
  (that is, when the bootstrapped parquet had them); `low` / `high` are not stored in the database.
- With the database, years after the parquet (e.g. 2026 from the 3-hour job) are available with
  `end=2026`; the default `end=2025` gives the same response as the parquet. Months the database
  holds as unknown (NULL, spec I7) are summed as 0, like empty months.

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

### Database-backed endpoints

`/processing/*`, `/fires/live` and `/reports` read PostgreSQL (spec §10). The examples below are
abridged (`"..."`) and come from the local database on 2026-11-15 (real NRT detections, jobs and
review), except the report, which is made up. The API connects lazily
to `DATABASE_URL` on first use, with a short connect timeout (`API_DB_CONNECT_TIMEOUT_S`, default
3 s). When `DATABASE_URL` is unset, the server is unreachable or the schema is not migrated, these
endpoints answer **503** with a `detail` that says which (`database not configured: ...`,
`database unavailable at <url with the password masked>: ...`, `database schema is not up to
date ...; run alembic upgrade head`), and the calendar endpoints keep working from the parquet.

The API process never imports `worker`, `crewai` or an LLM client (invariant I1, tested in a fresh
interpreter after serving every endpoint). Every GET runs in a `READ ONLY` transaction, so it
cannot write; only the `/reports` writes use a read-write transaction, and they touch
`community_reports` only. Timestamps are ISO 8601 UTC with a `Z`
(`2026-11-15T03:31:06.622Z`; milliseconds only when non-zero); dates are `YYYY-MM-DD`.

Configuration (all optional; see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | unset | PostgreSQL URL; unset = parquet only, database endpoints 503 |
| `API_VERSION_CHECK_S` | `30` | seconds between checks for a new approved dataset version behind `/calendar` |
| `API_DB_CONNECT_TIMEOUT_S` | `3` | connect timeout of the API's database connections |
| `REPORTS_ADMIN_TOKEN` | unset | when set, `PATCH`/`DELETE /reports` need it in `X-Admin-Token` |

`POST /processing/jobs` (manual trigger, spec §10) is not implemented; there is no `ADMIN_TOKEN`.

#### `GET /processing/status`

```json
{
  "generated_at": "2026-11-15T03:53:05.608Z",
  "scheduler": {"kinds": [
    {"kind": "firms_poll", "enabled": true, "cadence": "interval", "interval_seconds": 10800,
     "day_of_month": null, "next_run_at": "2026-11-15T06:42:43.184Z", "overdue": false,
     "last_claimed_at": "2026-11-15T03:42:43.184Z", "last_job_id": 4,
     "active_job": null,
     "last_run": {"id": 4, "status": "succeeded", "...": "a job list item, see below"},
     "last_success": {"job_id": 4, "finished_at": "2026-11-15T03:43:43.571Z"},
     "last_error": null}
  ]},
  "approved": {
    "dataset_version": {"id": 2, "parent_id": 1, "job_id": 4, "calibration_version_id": 1,
                        "status": "approved", "created_at": "...", "approved_at": "...", "summary": {}},
    "calibration_version": {"id": 1, "status": "approved", "label": "bootstrap", "k_table_ref": "parquet:...",
                            "snpp_end": "2026-11-02", "r_global": 0.983, "mask_version": "mask-7935676995ad",
                            "created_at": "...", "approved_at": "..."}
  },
  "data": {"raw_detections": 3744, "pending_detections": 0, "approved_cell_months": 13251},
  "last_error": null
}
```

- One `kinds` entry per `schedules` row. `active_job` is the queued or running job of that kind
  (a job list item plus `lease_owner`, `lease_expires_at`, `heartbeat_at`), or null. `last_run`
  is the newest finished job of the kind; `last_success` the newest `succeeded` or `no_change`;
  `overdue` is true when an enabled kind's `next_run_at` has passed (no worker is claiming it).
- `last_error` (per kind and overall): the newest `failed`, `abandoned` or `rejected` job,
  `{"job_id", "kind", "status", "error", "finished_at"}`, or null. A rejection usually has no
  `error`; its reasons are in the job detail (`verdict`, `gates`).
- `approved.*` is null before the bootstrap.

#### `GET /processing/jobs`

| Parameter | Default | Meaning |
|---|---|---|
| `kind` | all | job kind (`firms_poll`, `monthly_reconcile`), or several separated by commas |
| `status` | all | `queued`, `running`, `succeeded`, `no_change`, `held`, `rejected`, `failed`, `abandoned`, or several separated by commas |
| `limit` | `50` | 1-200 |
| `offset` | `0` | jobs to skip |

Newest first (by id). 400 for an unknown status, a malformed kind, or `limit`/`offset` out of range.

```json
{"jobs": [
  {"id": 2, "kind": "firms_poll", "status": "held", "trigger": "schedule",
   "created_at": "2026-11-15T03:31:06.622Z", "started_at": "2026-11-15T03:31:06.622Z",
   "finished_at": "2026-11-15T03:33:03.360Z", "duration_s": 116.739,
   "counts": {"fetched": 176, "new": 35, "modified": 0, "unchanged": 141, "pending": 2952,
              "affected": 174, "detections": 2952, "staged": 174},
   "decision": "hold", "ai_review": "completed", "dataset_version_id": null,
   "calibration_version_id": 1, "error": null}
 ],
 "total": 4, "limit": 50, "offset": 0}
```

- `counts`: rows fetched from FIRMS, new / modified / unchanged among them, detections pending
  processing, affected cell-months, detections loaded for them, rows staged. A count is null when
  the job did not get that far (a `no_change` job has no `affected`).
- `decision`: `approve`, `hold` or `reject` when the job processed data, else null.
  `ai_review`: `completed`, `unavailable` (every model failed: decided on the gates), `disabled`
  (no models configured), or null. `dataset_version_id`: the version the job published, if any.
  `error`: why a job failed, or null.

#### `GET /processing/jobs/<id>`

The list item fields plus everything the Processing Center shows; 404 for an unknown id.

```json
{
  "id": 2, "kind": "firms_poll", "status": "held", "...": "all job list item fields",
  "lease": {"owner": "<host>:<pid>", "expires_at": "2026-11-15T03:37:46.630Z", "heartbeat_at": "2026-11-15T03:33:03.360Z"},
  "decision_basis": "verdict",
  "gates": {"passed": true, "results": [
    {"name": "G1_schema_range", "passed": true, "detail": {"rows": 174, "failures": {}, "affected_keys": 174}},
    {"name": "G2_conservation", "passed": true, "detail": {}},
    {"name": "G3_missing_not_zero", "passed": true, "detail": {}},
    {"name": "G4_frozen_calibration", "passed": true, "detail": {}}]},
  "soft_flags": [{"flag": "mixed_reference", "rows": 84, "keys": [[68, -5, 2026, 11]]}],
  "verdict": {"decision": "hold", "reasons": ["All hard gates passed ..."], "flagged_keys": [[68, -5, 2026, 11]]},
  "review": {"model": "nvidia/nemotron-3.5-super-vl:free", "attempts": 1, "reason": null},
  "steps": [
    {"id": 8, "name": "availability", "status": "succeeded", "started_at": "...", "finished_at": "...",
     "duration_s": 0.905, "summary": {"sources": {}}},
    {"id": 24, "name": "publish", "status": "skipped", "started_at": "...", "finished_at": "...",
     "duration_s": 0.003, "summary": {"note": "run held: approved data unchanged, ...", "decision": "hold"}}],
  "llm_attempts": [
    {"id": 1, "step": "review", "attempt_no": 1, "model": "nvidia/nemotron-3.5-super-vl:free",
     "started_at": "...", "latency_ms": 104186, "outcome": "ok", "error_class": null, "error": null,
     "tokens": {"prompt": 6699, "completion": 17714, "total": 24413}}],
  "dataset_version": null
}
```

- `decision_basis`: why the decision was taken: `verdict` (the Validation reviewer's verdict),
  `gates_only` (gates passed, no usable review: published on the gates), `hard_gate_failed`
  (rejected without review). Null when the job made no decision.
- `gates`: the hard gates G1-G4 as the worker recorded them (`passed` is their conjunction, null
  without a decision; `detail` is the gate's own report). `soft_flags`: the flags raised, each
  `{"flag", "rows", "keys"}` (keys possibly truncated by the worker). Keys are
  `[cy, cx, year, month]` (0.5 degree cell indices, as in `cell_month`).
- `verdict`: the reviewer's typed verdict, or null (no review, or a fallback). `review`: the model
  that produced it, how many LLM attempts were made, and the reason when the review was skipped
  or failed; null without a decision.
- `steps`: the job's timeline, in start order, each with its `summary` exactly as the worker wrote
  it (fetch counts per source, filter drops, harmonize flags, completeness per month, the
  promotion's counts...). Step `status`: `running`, `succeeded`, `skipped`, `failed`.
- `llm_attempts`: every model call, in order: `outcome` is `ok`, `timeout`, `rate_limited`,
  `quota_exhausted`, `http_error`, `invalid_output` or `error`; `error_class` and `error` (short,
  redacted) explain a failure; `tokens` are null when the provider reported none.
- `dataset_version`: the version this job published (shape as in `/processing/datasets`), or null.

#### `GET /processing/datasets`

`limit` (1-200, default 50) and `offset` as for jobs. Newest first.

```json
{"datasets": [
  {"id": 2, "parent_id": 1, "job_id": 4, "calibration_version_id": 1, "status": "approved",
   "created_at": "...", "approved_at": "...",
   "summary": {"job_id": 4, "keys": 181, "inserted": 181, "updated": 0, "history_rows": 0,
               "months": ["2026-10", "2026-11"], "soft_flags": ["mixed_reference", "modis_partial", "partial"],
               "cleared_pending": 3744, "...": "..."},
   "current_rows": 181},
  {"id": 1, "parent_id": null, "job_id": null, "calibration_version_id": 1, "status": "superseded",
   "summary": {"source": "bootstrap", "rows": 13070, "years": [2001, 2025]}, "current_rows": 13070}],
 "total": 2, "limit": 50, "offset": 0,
 "calibrations": [{"id": 1, "status": "approved", "label": "bootstrap", "...": "as in /processing/status"}]}
```

`summary` is the worker's record of the version (the bootstrap's source file, or the promoting
job's keys, months, soft flags and promotion counts). `current_rows`: approved `cell_month` rows
whose latest value comes from this version.

#### `GET /fires/live`

| Parameter | Default | Meaning |
|---|---|---|
| `hours` | `48` | window length, whole hours 1-168 |
| `bbox` | none | `minLon,minLat,maxLon,maxLat` (same rules and 400s as `/calendar`) |
| `source` | all | FIRMS source id(s), e.g. `VIIRS_NOAA20_NRT`, several separated by commas |

NRT detections from `raw_detections` (never FIRMS itself) whose acquisition time (UTC,
`acq_date` + `acq_time`) lies in `[end - hours, end]`, where `end` is the newest stored NRT
detection of the requested sources. The bbox does not move the window, so a quiet area shows an
empty list, not old fires. 400 for bad `hours`, `bbox` or `source`.

```json
{"snapshot": "2026-11-15T03:42:45.302Z",
 "window": {"start": "2026-11-14T00:58:00Z", "end": "2026-11-15T00:58:00Z"},
 "detections": [
   {"id": 2858, "lat": 35.85041, "lon": 8.59264, "acq_time": "2026-11-14T01:15:00Z",
    "source": "VIIRS_NOAA21_NRT", "satellite": "N21", "sensor": "viirs", "confidence": "n",
    "frp": 0.77, "daynight": "N"},
   "...",
   {"id": 2952, "lat": 37.57137, "lon": -0.92264, "acq_time": "2026-11-15T00:58:00Z",
    "source": "VIIRS_NOAA21_NRT", "satellite": "N21", "sensor": "viirs", "confidence": "n",
    "frp": 1.66, "daynight": "N"}],
 "sources": [{"source": "MODIS_NRT", "max_acq_date": "2026-11-14"},
             {"source": "VIIRS_NOAA20_NRT", "max_acq_date": "2026-11-14"},
             {"source": "VIIRS_NOAA21_NRT", "max_acq_date": "2026-11-15"},
             {"source": "VIIRS_SNPP_NRT", "max_acq_date": "2026-11-02"}],
 "meta": {"count": 155, "product": "NRT", "hours": 24, "bbox": [-2.5, 34.0, 11.5, 38.0]}}
```

- `snapshot`: when the newest stored FIRMS response was fetched (`window.end` if none).
- `detections`: oldest first. `satellite`, `source` and `confidence` are as FIRMS delivered them
  (MODIS confidence is a 0-100 number as text, VIIRS `l`/`n`/`h`); `sensor` is `modis` for MODIS
  sources and `viirs` otherwise; `frp` may be null.
- `sources`: every stored NRT source (of the filter) with its newest acquisition date, so the map
  can say which satellite is behind.
- With no stored NRT detection, `snapshot` and the window are null and `detections` is empty.

#### Community reports: `/reports`

```json
{"id": "0b6c3f5e-8a7d-4a0e-9a59-7d3c2f1e0a11", "lat": 36.95, "lon": 9.1, "observed": "smoke",
 "note": "smoke over the hill", "status": "pending", "createdAt": "2026-11-15T09:12:44.512Z",
 "reviewedAt": null}
```

| Call | Body | Answer |
|---|---|---|
| `GET /reports` | | `{"reports": [report]}`, newest first, at most 500 |
| `POST /reports` | `{"lat", "lon", "observed", "note"}` | **201** and the new report (`status` `pending`) |
| `PATCH /reports/<id>` | `{"status": "pending" \| "confirmed" \| "false"}` | the updated report |
| `DELETE /reports/<id>` | | **204** |

- `lat` -90..90 and `lon` -180..180 (JSON numbers), `observed` one of `smoke`, `flames`, `both`,
  `unsure`, `note` optional (trimmed, at most 280 characters after trimming). Invalid bodies get
  **422** with FastAPI's `detail` list. Other fields in the body (`id`, `status`, times) are
  ignored: ids are server-generated UUIDs.
- `PATCH` sets `reviewedAt` to the review time for `confirmed`/`false` and clears it for
  `pending`. `PATCH`/`DELETE` of an unknown id: **404**.
- When `REPORTS_ADMIN_TOKEN` is set, `PATCH` and `DELETE` need the header
  `X-Admin-Token: <token>`: **401** without it, **403** with a wrong one (compared in constant
  time; checked before the body or the id). Unset, they are open (local development only).
  `GET` and `POST` are always open.
- Schema: migration `0003` creates `community_reports` (run `alembic upgrade head`); until then
  the endpoints answer 503.

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
is cached (from the database or the parquet, see `/calendar`). Database access goes through
`api/database.py` (`read()` for READ ONLY transactions).

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
- Terra only before July 2002: MODIS Aqua's first detection is 2002-07-04, and Aqua makes about
  60 % of MODIS detections (North Africa, 2003-2011). So January 2001 to June 2002 reads at about
  40 % of its true level (world 2001: 0.36 of an average 2003-2011 year), and on a large box those
  months are flagged `low`. Not corrected yet (open question for the method).
- Volcanoes and gas flares outside North Africa: some pass `type == 0`, and VIIRS sees them far
  more than MODIS, so their cells get extreme ratios (k up to 597: Holuhraun, Kilauea, Galapagos,
  Piton de la Fournaise, the Bakken oil field). 340 of 458,159 world cell-months have k > 20, about
  0.1 % of pre-2012 harmonized detections. A world release needs a static-source mask, like the
  34N floor here.

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
