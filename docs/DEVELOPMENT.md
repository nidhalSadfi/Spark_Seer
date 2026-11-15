# EmberGuard: development guide

Everything needed to run, configure and extend EmberGuard locally. The project overview is in the
[README](../README.md); the processing spec is [processing/ARCHITECTURE.md](processing/ARCHITECTURE.md).

## Run everything

Five things make up the project. You start the database, the API and the dashboard; the agents come with the API; the worker is optional and keeps the live map fresh.

| Part | What it does | How it starts | Port |
|---|---|---|---|
| **Postgres** | stores detections, reports, processing history | `docker compose up -d db` | 5432 |
| **API** (`backend/api`) | serves the data to the dashboard | `uvicorn api.main:app` | 8000 |
| **Fire incident agents** | find fires in social media and news (Social Alerts page) | **started and stopped by the API**, nothing to run | 8765 (local only) |
| **Worker** (`backend/worker`) | polls NASA FIRMS and processes new detections | `python -m worker` | none |
| **Dashboard** (`frontend`) | the web app | `npm run dev` | 5173 |

**Needs:** Docker (for Postgres), Python 3.11+, Node `^20.19` or `>=22.12`. On Windows, run the backend commands inside WSL (Ubuntu).

### First time only

```bash
cp .env.example .env              # then fill in the keys you have (see "Keys" below)
docker compose up -d db           # Postgres 16; it restarts by itself after a reboot

cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
set -a; . <(tr -d '\r' < ../.env); set +a   # alembic and the worker read the environment, not .env (tr drops Windows line endings)
alembic upgrade head              # creates the tables
python -m worker.bootstrap        # loads the approved calibration and dataset (running it again does nothing)

cd ../frontend && npm install
```

### Every day

**One command** (Linux, macOS or WSL), from the repository root:

```bash
./run-world.sh db
```

It starts Postgres, applies the migrations, starts the API (which starts the agents) and the dashboard, then prints the addresses. (If you installed the frontend's packages on Windows it cannot start the dashboard inside WSL: it says so and you run `npm run dev` on Windows, see the last tips below.) Ctrl+C stops the API, the agents and the dashboard. It stops anything already listening on ports 8000, 8001, 5173 and 5174 first. In a second terminal, start the worker if you want the live map to keep updating:

```bash
cd backend && source .venv/bin/activate
set -a; . <(tr -d '\r' < ../.env); set +a
python -m worker                  # ticks every WORKER_TICK_SECONDS; add --once for a single tick
```

**Or one terminal per part**, which is easier to read and restart (each block in its own terminal, starting from the repository root):

```bash
docker compose up -d db                                   # 1. database (skip if it already runs)

cd backend && source .venv/bin/activate                   # 2. API + agents
uvicorn api.main:app --reload --port 8000 --env-file ../.env

cd backend && source .venv/bin/activate                   # 3. worker (optional)
set -a; . <(tr -d '\r' < ../.env); set +a && python -m worker

cd frontend && npm run dev                                # 4. dashboard
```

`--env-file ../.env` makes uvicorn load the root `.env` for you, including its Windows line endings. The agents read their keys from the same file themselves.

**Quick look without a database or any setup:** the committed Tunisia and Algeria data is enough for the calendar, analytics and Data Explorer.

```bash
cd backend && source .venv/bin/activate && uvicorn api.main:app --reload --port 8000   # 1st terminal
cd frontend && npm run dev                                                              # 2nd terminal
```

Without the database, `/reports`, `/fires/live` and `/processing/*` answer 503. That is expected: reports are kept in the browser, the map shows labelled sample fires, and Processing Center says it is not available. `./run-world.sh` (no argument) is the same quick look in one command: it starts the API (and the agents) and the dashboard on the committed data. If you have built the world history file `backend/data/world/harmonized.parquet`, it uses that instead. You do not need it: building it is optional, takes the multi-GB FIRMS yearly downloads and `python scripts/build_world.py all` (see `backend/README.md`, "World coverage").

### Check that it works

- Dashboard: <http://localhost:5173/dashboard>. The badge at the top right says **Live data** (not *Offline*).
- API: <http://localhost:8000/health>. With the database it says `"data_source":"database"`.
- Agents: <http://localhost:8000/incidents/status> says `"running":true`. In the dashboard, open **Social Alerts → Agents**: it lists which keys the agents can see.
- Reports: <http://localhost:8000/reports> returns `{"reports":[]}` (a 503 means the database is not up).

### Keys (`.env`)

Only what you have is needed; each missing key switches off just its own part.

| Name in `.env` | Used for | Without it |
|---|---|---|
| `DATABASE_URL` (and `POSTGRES_*`) | everything stored in Postgres | database endpoints answer 503; the calendar still works from the file |
| `FIRMS_MAP_KEY` or `FIRMS_MAP_KEY_1` | the worker's FIRMS polling; the agents' satellite check | no live updates; the agents skip the FIRMS check |
| `test_fire_groq_key` | the agents' language model | no social search can run |
| `twitterapi` | the agents' X search | X is skipped |
| `bsky_handle` + `bsky_password` | the agents' Bluesky search | Bluesky is skipped |
| `OPENROUTER_API_KEY` + `OPENROUTER_MODELS` | the worker's AI review | the AI review is off |

Restart the API after editing `.env`. `INCIDENTS_AUTOSTART=0` stops the API from starting the agents; `INCIDENT_PORT` changes their port.

### Stop

Ctrl+C in each terminal (the agents stop with the API). `docker compose stop db` stops Postgres and keeps its data; `docker compose down` removes the container but not the data volume.

### If something does not work

- **`: command not found` when loading `.env`**, or odd database or key errors: the file has Windows line endings. Use the `tr -d '\r'` form shown above, or convert it once with `sed -i 's/\r$//' .env`.
- **`./backend/scripts/build_world.py` prints `command not found` errors**: it is a Python script, so run it with `python` (from `backend/`: `python scripts/build_world.py all`), not as `./...`. It is only for building the optional world history file.
- **The dashboard does not start from `run-world.sh` (log: `Cannot find native binding`)**: `frontend/node_modules` was installed on one system (Windows) and the script runs in another (WSL). The bundler is a native binary per system, so the folder only works where it was installed. The script now detects this, leaves the API and agents running and tells you to start the dashboard on Windows instead: in PowerShell, `cd frontend` then `npm run dev`, and open <http://localhost:5173/dashboard>. It talks to the API in WSL through `localhost:8000`. Do one or the other, not both: running `npm install` in WSL replaces the Windows build.
- **`npm install` hangs on the spinner or fails with `ECONNRESET`**: your network probably blocks `registry.npmjs.org`. Use a mirror, then install again: `npm config set registry https://registry.yarnpkg.com` (npm rewrites the addresses in `package-lock.json` to it, so nothing else changes). Windows and WSL each have their own npm settings, so set it in the one you install from. Inside WSL, installing into `/mnt/c/...` is slow (a few minutes), that is normal. If `git status` then shows `frontend/package-lock.json` as changed, undo it with `git checkout -- frontend/package-lock.json`.
- **`/reports` answers 503**: Postgres is not running (`docker compose up -d db`) or `DATABASE_URL` was not loaded (use `--env-file ../.env`).
- **The dashboard says *Offline* but the backend runs in WSL or Docker**: see the paragraph under *Frontend* below.
- **Social Alerts says the agents are offline or not running**: open <http://localhost:8000/incidents/status> to see whether the API started them and why not.

## Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
python -m firms_tool sensors     # firms_tool CLI; needs FIRMS_MAP_KEY for API downloads
```

See `backend/README.md` for running the API, the data pipeline and the Render deploy.

Starting the API also starts the fire incident agents behind the dashboard's *Social Alerts* page, as a separate process
(they call a language model, which the API process never does). See **Run everything** above for how to start the whole
project and which keys it reads.

## Frontend

Vite 8 needs Node `^20.19` or `>=22.12`. Then `cd frontend && npm install && npm run dev`.
See `frontend/README.md`.

**The dashboard says "API offline" / "This page cannot reach the backend" although the backend is running.** If the backend
runs in WSL (or Docker) and the dev server on Windows, the browser may not be able to reach the backend's port. Test it: open
<http://localhost:8000/health> in your Windows browser. If that fails while `curl http://127.0.0.1:8000/health` works inside WSL,
WSL's localhost forwarding is not active. Run `wsl --shutdown` in PowerShell, reopen Ubuntu and start the backend again. If it
still fails, run the frontend inside WSL too (then both use the same `localhost`), or run the backend natively on Windows.
Starting uvicorn with `--host 0.0.0.0` makes WSL forward it, but also exposes the API to your network: only on a network you
trust, and never for the agents' port (8765), which makes paid calls.

## Database and worker

```bash
cp .env.example .env    # then fill in the keys you need
docker compose up -d db
set -a; . <(tr -d '\r' < .env); set +a   # the Python code reads the environment, not .env (tr drops Windows line endings,
                                         # which bash would keep as a hidden \r at the end of every value)
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
exits 0. `--parquet` (or `--data`), `--validation`, `--inter-viirs` and `--mask` override the files;
`--replace` supersedes existing versions with new ones (the world cut-over, see
`backend/README.md`, "World coverage"). Set
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
