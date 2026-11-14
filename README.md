# EmberGuard

Harmonized MODIS + VIIRS fire-activity monitoring for North Africa: a FastAPI backend over NASA
FIRMS data and a React dashboard (NASA Space Apps Challenge 2026, Tunis).

## Layout

```
backend/              FastAPI API, harmonization pipeline, tests (from origin/backend-core)
backend/firms_tool/   NASA FIRMS download tool (copied from origin/harmonization-modis-viirs)
frontend/             React + Vite dashboard (from origin/frontend)
docker-compose.yml    local services (Postgres for now)
.env.example          environment variables, with safe local-only defaults
render.yaml           Render blueprint for the API (rootDir: backend)
```

The processing architecture spec will live at `docs/processing/ARCHITECTURE.md`.

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

## Database

```bash
cp .env.example .env    # then fill in the keys you need
docker compose up -d db
```

Only the `db` service exists for now; `api` and `worker` services arrive in later phases.

## Where the code came from

`backend/` and `frontend/` are subtree merges of the teammate branches, so their history is
preserved. `backend/firms_tool/` was copied (not merged) from
`origin/harmonization-modis-viirs@e07ecf5` (`harmonization-modis-viirs/firms_tool/`), together with
its tests (`backend/tests/test_firms_*.py`); that branch's docs and pipeline were left out.

To pull teammate updates later:

```bash
git fetch origin
git merge -X subtree=backend origin/backend-core
git merge -X subtree=frontend origin/frontend
```

Changes to firms_tool on `origin/harmonization-modis-viirs` are not picked up by a merge: copy
them over by hand.
