# EmberGuard

Harmonized MODIS + VIIRS fire-activity monitoring for North Africa: a FastAPI backend over NASA
FIRMS data and a React dashboard (NASA Space Apps Challenge 2026, Tunis).

## Layout

```
backend/              FastAPI API, harmonization pipeline, tests (from origin/MNS-backend)
backend/firms_tool/   NASA FIRMS download tool (copied from origin/harmonization-modis-viirs)
frontend/             React + Vite dashboard (from origin/frontend)
docker-compose.yml    local services (Postgres for now)
.env.example          environment variables, with safe local-only defaults
render.yaml           Render blueprint for the API (rootDir: backend)
```

The processing architecture spec is `docs/processing/ARCHITECTURE.md`.

## Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
python -m firms_tool sensors     # firms_tool CLI; needs FIRMS_MAP_KEY for API downloads
```

See `backend/README.md` for running the API, the data pipeline and the Render deploy.

## Frontend

Vite 8 needs Node `^20.19` or `>=22.12`. Then `cd frontend && npm install && npm run dev`.
See `frontend/README.md`.

## Database and worker

```bash
cp .env.example .env    # then fill in the keys you need
docker compose up -d db
set -a; . ./.env; set +a          # the Python code reads the environment, not .env
cd backend
alembic upgrade head              # creates the processing tables (alembic upgrade head --sql prints the DDL)
python -m worker.bootstrap        # once: approved calibration 1 + dataset 1 from data/ (a second run does nothing)
python -m worker --once           # one scheduler tick; without --once it ticks every WORKER_TICK_SECONDS
pytest -m pg                      # PostgreSQL tests against TEST_DATABASE_URL (skipped if unreachable)
```

`python -m worker.bootstrap` (run from `backend/`) loads, in one transaction, calibration version 1
(k from `data/harmonized.parquet`, its validation report `data/harmonized_validation.json`, the
approved r of `data/inter_viirs_fit.json`, and the static-mask version of `STATIC_MASK_FILE` or
`--mask`) and approved dataset version 1 (every parquet row into `cell_month`; VIIRS is NULL
before 2012, spec §15). If any calibration or dataset version already exists it does nothing and
exits 0. `--parquet`, `--validation`, `--inter-viirs` and `--mask` override the files. Set
`STATIC_MASK_FILE` the same way for the worker: `firms_poll` refuses to run with a mask other than
the calibration's (I6).

The worker polls NASA FIRMS (`firms_poll`, every `FIRMS_POLL_INTERVAL_HOURS`) and stores raw
detections; see `docs/processing/ARCHITECTURE.md`. When there are new, modified or still-pending
detections, it recomputes their cell-months (all stored detections of each) with the approved
calibration, runs the review flow, writes the rows to `cell_month_staging` and then publishes them
in one transaction (`cell_month`, `cell_month_history`, a new `dataset_versions` row), or keeps
them unpublished when the run is held or rejected; those detections stay pending for the next
poll. Each job records its steps (`job_steps`), LLM attempts (`llm_attempts`), `ai_review` and the
decision with the gate results and the Validation reviewer's verdict (`processing_jobs.decision`).
Staging rows are deleted `STAGING_RETENTION_DAYS` (default 30) after their job finished.
`monthly_reconcile` is not implemented yet; its schedule stays disabled. It needs `pip install -r requirements-worker.txt`
(requirements.txt plus CrewAI, pinned to 1.15.x); the API keeps using `requirements.txt` and never
imports CrewAI. `requirements-dev.txt` includes the worker requirements, so `pytest` covers both. Only the `db` service exists for now; `api` and
`worker` services arrive in later phases. `docker/initdb/` creates the `emberguard_test` database
when the db volume is first initialised.

## AI review (CrewAI + OpenRouter)

`backend/worker/review.py` runs the processing stages as a CrewAI flow, then three reviewer agents
whose verdict can only downgrade the deterministic hard gates (spec §7). Configure it in `.env`:

- `OPENROUTER_API_KEY`: required when a model list is set.
- `OPENROUTER_MODELS`: comma-separated OpenRouter model ids, tried in order. **Empty (the default)
  disables the review**: runs are decided by the hard gates alone (`ai_review = "disabled"`) and
  CrewAI is not even imported.
- `OPENROUTER_BASE_URL`, `OPENROUTER_TIMEOUT_S`, `OPENROUTER_RETRIES`: endpoint, per-request
  timeout and HTTP retries.
- `REVIEW_DEADLINE_S`: time budget for the whole review; no model is started after it. If every
  model fails, a run that passed the gates is published with `ai_review = "unavailable"`.
- `REVIEW_SUMMARY_MAX_CHARS`: cap on each summary an agent is shown.
- `OPENROUTER_QUOTA_CHECK` (default `true`): check the key's remaining free-model requests first;
  set `false` for a paid key.

Free tier (50 free-model requests a day per key, shared by 8 polls): one attempt costs at most 3
requests (summaries are in the prompts, one answer per agent). With fewer than 3 left the review is
skipped (`reason: "daily_quota"`), and a daily-quota 429 stops the model chain; either way a run
that passed the gates is still published (D3, `ai_review = "unavailable"`).

Before importing CrewAI the worker switches off its telemetry, tracing, version check and `.env`
loading, and points its storage at `CREWAI_STORAGE_DIR` (default: a per-user directory under the
system temp dir), so CrewAI writes nothing under `$HOME`. `firms_poll` runs this flow for every
job with pending detections; tests use stub models only.

## Processing science

`backend/processing/` holds the deterministic stages of spec §5-§6 (shared filter, country clip,
static-source mask, 0.5° counts, harmonization with a frozen calibration, completeness, gates G1-G4,
drift) as pure Python, tested on synthetic data only. Two CLIs build the real artefacts once the
FIRMS yearly country CSVs for Tunisia and Algeria exist (run from `backend/`):

```bash
python -m processing.build_mask data/raw data/static_mask.json          # then STATIC_MASK_FILE=...
python -m processing.fit_inter_viirs data/raw data/inter_viirs_fit.json  # proposed r (S-NPP / NOAA-20)
```

The inter-VIIRS factor r is fitted on a Mediterranean pool and validated on Tunisia + Algeria
(spec §5). Download the pool (S-NPP and NOAA-20 yearly country files, about 105 MB), then fit:

```bash
python -m firms_tool archive --sensors viirs_snpp viirs_noaa20 --years 2019-2025 \
    --countries Morocco Spain Portugal Italy Greece Turkey --out <pool_dir>
python -m processing.fit_inter_viirs <pool_dir> data/inter_viirs_fit.json --validate-dir data/raw
```

With `--validate-dir`, r is fitted on the pool over the train years and validated on `data/raw`
over the test years; the output adds the validation errors (r = 1, global r, per-month r), the
pool's file list, hash and countries, and `approved_r` from the acceptance rule (the global r if it
beats r = 1, else 1.0). Only the global r is applied (`Calibration.with_global_r`).

The country clip needs a boundary GeoJSON (`COUNTRY_BOUNDARY_FILE`); none is committed yet. While
the mask or boundary file is unset, the run reports that rule as disabled.

## Where the code came from

`backend/` and `frontend/` are subtree merges of the teammate branches, so their history is
preserved. `backend/firms_tool/` was copied (not merged) from
`origin/harmonization-modis-viirs@e07ecf5` (`harmonization-modis-viirs/firms_tool/`), together with
its tests (`backend/tests/test_firms_*.py`); that branch's docs and pipeline were left out.

To pull teammate updates later:

```bash
git fetch origin
git merge -X subtree=backend origin/MNS-backend   # backend-core plus Meteomatics, real data, demo page
git merge -X subtree=frontend origin/frontend
```

Changes to firms_tool on `origin/harmonization-modis-viirs` are not picked up by a merge: copy
them over by hand.
