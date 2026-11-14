# Spark Seer

Satellite fire-intelligence for the **NASA Space Apps Challenge 2026**: a web dashboard on top of an API that harmonizes
MODIS and VIIRS active-fire records into one burning-activity calendar, with historical anomaly analysis.

```
Spark_Seer/
├── frontend/     React + Vite dashboard (landing page, map, calendar, analytics, data explorer)   → frontend/README.md
├── backend/      EmberLedger API: Python / FastAPI + the MODIS→VIIRS harmonization pipeline          → backend/README.md
└── render.yaml   Render deploy blueprint for the API (must stay at the repo root; it points at backend/)
```

## Run it

Two processes: the **backend** (API on port 8000) and the **frontend** (Vite on port 5173). Start the backend first.
If it isn't running, the dashboard still works but falls back to generated sample data and says so in the header.

### Backend (`backend/`, Python 3.11+)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate              # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Then start the API with **either** fake test data **or** the real pipeline output:

```bash
# A) Fake data (quick start). Clearly marked synthetic: the UI labels it as such. Never present it as results.
python scripts/make_fake_data.py --optional                                       # writes backend/data/fake/
EMBER_DATA=data/fake/harmonized.parquet uvicorn api.main:app --reload --port 8000    # macOS / Linux
```
```powershell
# A) Fake data, Windows PowerShell
python scripts/make_fake_data.py --optional
$env:EMBER_DATA = "data/fake/harmonized.parquet"
uvicorn api.main:app --reload --port 8000
```
```bash
# B) Real data: unzipped FIRMS yearly CSVs (MODIS C6.1 + VIIRS S-NPP 375 m) in backend/data/raw/
python pipeline/ingest.py data/raw data/cells.parquet 34
python pipeline/calibrate.py data/cells.parquet data/harmonized.parquet
uvicorn api.main:app --reload --port 8000
```

Check it: <http://localhost:8000/health> should return `"status": "ok"`; interactive docs are at <http://localhost:8000/docs>.
Optional: set `ANTHROPIC_API_KEY` to turn on the passport's AI wording (the passport works without it).
Backend tests: `pip install -r requirements-dev.txt && pytest`. Full API reference: [backend/README.md](backend/README.md).

### Frontend (`frontend/`)

```bash
cd frontend
npm install
cp .env.example .env.local     # optional: only needed if the API is not at http://localhost:8000
npm run dev                    # http://localhost:5173
```

Set `VITE_API_URL` in `frontend/.env.local` to use another backend, e.g. a deployed one (`https://<service>.onrender.com`;
free Render instances take up to a minute to wake up). Other scripts: `npm run build`, `npm run preview`, `npm run lint`.

## Troubleshooting
- **Header badge says "API offline · sample data"**: the frontend cannot reach the backend. Check `curl http://localhost:8000/health` and `VITE_API_URL`, then reload.
- **`Cannot find native binding` when running `npm run dev`**: `frontend/node_modules` was installed on a different OS (e.g. Windows) than the one running Vite (e.g. WSL). Run `npm install` and `npm run dev` in the same environment. Keep `package-lock.json`.
- **`npm install` hangs or `ECONNRESET`**: `registry.npmjs.org` may be blocked by a network firewall. Use a mirror: `npm install --registry https://registry.yarnpkg.com`.
- **`POST /investigate` returns 500** (the passport button fails): the backend predates the integer-years fix in `backend/api/passport.py` (`CAL_START`/`CAL_END` must be ints, not `"YYYY-MM"` strings). This repo already contains the fix.

## Deploying the API
`render.yaml` (repo root) deploys `backend/` to Render on every merge to `main`; see the Deploy section of
[backend/README.md](backend/README.md). The data files it serves (`data/harmonized.parquet`, `data/harmonized_validation.json`)
must be committed under `backend/data/`.

## Data
NASA FIRMS active-fire data (MODIS Collection 6.1, VIIRS 375 m). We acknowledge the use of data from NASA's Fire Information
for Resource Management System (FIRMS), part of NASA's LANCE and ESDIS.
