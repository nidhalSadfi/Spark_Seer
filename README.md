# Spark Seer

Satellite fire-intelligence dashboard (NASA Space Apps 2026) that harmonizes MODIS and VIIRS active-fire records into an activity calendar with historical anomaly analysis.

Stack: React + Vite, Tailwind CSS v4, Motion, ECharts, Leaflet, TanStack Query/Table, NumberFlow, Sonner, Lucide.

## Run it

Two processes: the **backend** (EmberLedger API, Python/FastAPI, on port 8000) and the **frontend** (this app, Vite, on port 5173). Start the backend first. If it is not running, the dashboard still works but falls back to generated sample data and says so in the header.

### Backend (branch `backend-core`)

The API code is not on this branch. Check it out next to this project (or in a second clone):

```bash
git fetch origin
git worktree add ../EmberLedger-api backend-core      # or: git checkout backend-core in another clone
cd ../EmberLedger-api
```

Create a virtualenv and install (Python 3.11+):

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
```powershell
# Windows PowerShell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Then start the API, with **either** fake test data **or** the real pipeline output:

```bash
# A) Fake data (quick start). Clearly marked synthetic: the UI labels it as such. Never present it as results.
python scripts/make_fake_data.py --optional           # writes data/fake/
EMBER_DATA=data/fake/harmonized.parquet uvicorn api.main:app --reload --port 8000        # macOS / Linux
```
```powershell
# A) Fake data, Windows PowerShell
python scripts/make_fake_data.py --optional
$env:EMBER_DATA = "data/fake/harmonized.parquet"
uvicorn api.main:app --reload --port 8000
```
```bash
# B) Real data: unzipped FIRMS yearly CSVs (MODIS C6.1 + VIIRS S-NPP 375 m) in data/raw/
python pipeline/ingest.py data/raw data/cells.parquet 34
python pipeline/calibrate.py data/cells.parquet data/harmonized.parquet
uvicorn api.main:app --reload --port 8000
```

Check it: <http://localhost:8000/health> should return `"status": "ok"`; interactive docs are at <http://localhost:8000/docs>.
Optional: set `ANTHROPIC_API_KEY` to turn on the passport's AI wording (the passport works without it). Backend tests: `pip install -r requirements-dev.txt && pytest`.

### Frontend (this branch, `frontend`)

```bash
npm install
cp .env.example .env.local     # optional: only needed if the API is not at http://localhost:8000
npm run dev                    # http://localhost:5173
```

Set `VITE_API_URL` in `.env.local` to point at another backend, e.g. a deployed one (`https://<service>.onrender.com`; free Render instances take up to a minute to wake up).

Other scripts: `npm run build` (production build), `npm run preview` (serve the build), `npm run lint`.

### Troubleshooting
- **Header badge says "API offline · sample data"**: the frontend cannot reach the backend. Check `curl http://localhost:8000/health` and `VITE_API_URL`, then reload.
- **`Cannot find native binding` when running `npm run dev`**: `node_modules` was installed on a different OS (e.g. Windows) than the one running Vite (e.g. WSL). Run `npm install` and `npm run dev` in the same environment. Keep `package-lock.json`.
- **`npm install` hangs or `ECONNRESET`**: `registry.npmjs.org` may be blocked by a network firewall. Use a mirror: `npm install --registry https://registry.yarnpkg.com`.
- **`POST /investigate` returns 500** (the passport button fails): the backend predates the integer-years fix in `api/passport.py` (`CAL_START`/`CAL_END` must be ints, not `"YYYY-MM"` strings). Update `backend-core`.

## Status
- Data comes from the EmberLedger API (see below). Without it, or if it is unreachable, the app shows **illustrative sample data** generated in `src/services/fireApi.js` (`mockCalendar`), labelled as such.
- The map's near-real-time fire layer is **always mock data** (the backend has no endpoint for it yet).
- **Live fire map** (`src/components/map/FireMap.jsx`): Leaflet on the Esri Dark Gray basemap (no API key), fire clusters sized by FRP over a glow layer of hotspot detections, 24 h playback slider, layer toggles, legend and rectangle AOI drawing. Data comes from `getActiveFires()` in `fireApi.js`. It reads filters via `useFilters()` and calls `setAoi(...)` when an area is drawn. The map page adds the selected-fire panel (`FireDetails.jsx`) and the satellite-pass / fire-weather chart (`FirePassChart.jsx`).

## Structure
`components/{layout,dashboard,map,calendar,analytics,common,ui}`, `pages/`, `services/`, `hooks/`, `context/`, `utils/`

## Landing page
`/` is an interactive landing page (Hairline figures, ember particles, keyboard controls); the dashboard lives under `/dashboard`.
Edit the challenge title/summary and the team name and members in `src/config/project.js` — empty fields show placeholders.

## EmberLedger API integration
The dashboard reads the EmberLedger backend (branch `backend-core`): `GET /health`, `/areas`, `/calendar`, `/validation`,
`/weather` and `POST /investigate`. All calls live in `src/services/emberApi.js` (HTTP client in `src/services/http.js`).

| UI | Endpoint |
|---|---|
| Area selector | `/areas` |
| Overview KPIs, calendar heatmap, anomaly charts, area ranking, Data Explorer + CSV | `/calendar?area=` (or `?bbox=` for an area drawn on the map) |
| Calendar flags (outlined cells), z-score, support | `/calendar` fields `flag`, `z`, `support` |
| "Evidence passport" in the calendar inspector | `POST /investigate` |
| Analytics → harmonization validation | `/validation` |
| Overview → weather outlook | `/weather?area=` |
| Header badge: live / synthetic / offline | `/health` + `/validation.synthetic` |

- If the backend is unreachable the app falls back to generated sample data and says so in the header and page footers.
- If `/validation` reports `"synthetic": true` (backend running `make_fake_data.py`), the UI labels everything as synthetic test data.
- The dataset covers Tunisia and northern Algeria (north of 34°N); an area drawn elsewhere on the map shows a "no detections" notice.
