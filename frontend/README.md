# Spark Seer: frontend

React dashboard for Spark Seer. The repo-level overview and the commands for running the backend and the frontend
together are in the [root README](../README.md); the API is documented in [../backend/README.md](../backend/README.md).

Stack: React + Vite, Tailwind CSS v4, Motion, ECharts, Leaflet, TanStack Query/Table, NumberFlow, Sonner, Lucide.

## Run it

```bash
npm install
cp .env.example .env.local     # optional: set VITE_API_URL if the API is not at http://localhost:8000
npm run dev                    # http://localhost:5173
```

Start the backend first (see the root README). Without it the dashboard falls back to generated sample data.
Other scripts: `npm run build`, `npm run preview`, `npm run lint`.

## Status
- Data comes from the EmberLedger API (see below). Without it, or if it is unreachable, the app shows **illustrative sample data** generated in `src/services/fireApi.js` (`mockCalendar`), labelled as such.
- The map's near-real-time fire layer is **always mock data** (the backend has no endpoint for it yet).
- **Live fire map** (`src/components/map/FireMap.jsx`): Leaflet on the Esri Dark Gray basemap (no API key), fire clusters sized by FRP over a glow layer of hotspot detections, 24 h playback slider, layer toggles, legend and rectangle AOI drawing. Data comes from `getActiveFires()` in `fireApi.js`. It reads filters via `useFilters()` and calls `setAoi(...)` when an area is drawn. The map page adds the selected-fire panel (`FireDetails.jsx`) and the satellite-pass / fire-weather chart (`FirePassChart.jsx`).

## Structure
`src/components/{layout,dashboard,map,reports,calendar,analytics,common,ui}`, `pages/`, `services/`, `hooks/`, `context/`, `utils/`

## Community fire reports
Anyone can flag a fire the satellites have not seen, and someone else can confirm or reject it:

1. **Report**: on *Global Fire Map* press the pin button (top right of the map), then click the spot, type its coordinates or use
   your location, say what you see, and submit. The alert shows on the map as a **?** (unverified). No weather lookup happens yet.
2. **Review**: the *Fire Reports* page (sidebar; the badge counts alerts waiting) lists the alerts with a small map and the satellite
   hotspots found within 10 km. Press **Yes, it is a fire** or **No, false alarm** (both can be undone with *Reopen as unverified*).
3. **Confirmed**: the **?** turns into the usual flame icon (with a small green tick, so it is not mistaken for a satellite detection),
   and the weather and location info for its coordinates are fetched: `GET /weather?lat=&lon=` (3-day Open-Meteo outlook, the named
   area it falls in, typical satellite activity for the month). False alarms disappear from the map.

Reports are stored **in this browser only** (`localStorage`, see `src/services/reportsStore.js`): they are not shared between users or
devices, because the API has no reports endpoint yet. All reads and writes go through that one file, so moving to an API later means
changing only it. The satellite check uses the map's feed, which is still sample data.

## Landing page
`/` is an interactive landing page (Hairline figures, ember particles, keyboard controls); the dashboard lives under `/dashboard`.
Edit the challenge title/summary and the team name and members in `src/config/project.js`: empty fields show placeholders.

## EmberLedger API integration
The dashboard reads the EmberLedger backend: `GET /health`, `/areas`, `/calendar`, `/validation`, `/weather` and
`POST /investigate`. All calls live in `src/services/emberApi.js` (HTTP client in `src/services/http.js`).

| UI | Endpoint |
|---|---|
| Area selector | `/areas` |
| Overview KPIs, calendar heatmap, anomaly charts, area ranking, Data Explorer + CSV | `/calendar?area=` (or `?bbox=` for an area drawn on the map) |
| Calendar flags (outlined cells), z-score, support | `/calendar` fields `flag`, `z`, `support` |
| "Evidence passport" in the calendar inspector | `POST /investigate` |
| Analytics → harmonization validation | `/validation` |
| Overview → weather outlook | `/weather?area=` |
| Fire Reports → weather and info for a confirmed report | `/weather?lat=&lon=` |
| Header badge: live / synthetic / offline | `/health` + `/validation.synthetic` |
| Last year of the year pickers and the calendar | `/health` field `data_end_year` (2025 if absent or offline) |
| Partial / no-data months in the calendar, stats and Data Explorer | `/calendar` field `coverage` |
| Processing Center (`/dashboard/processing`) | `/processing/status`, `/processing/jobs`, `/processing/jobs/<id>`, `/processing/datasets` |

- If the backend is unreachable the app falls back to generated sample data and says so in the header and page footers.
- If `/validation` reports `"synthetic": true` (backend running `make_fake_data.py`), the UI labels everything as synthetic test data.
- The dataset covers Tunisia and northern Algeria (north of 34°N); an area drawn elsewhere on the map shows a "no detections" notice.
- **End year.** `/calendar` is called without `end`, so the API's default (its last year with data, e.g. 2026 with the
  database) decides; the year pickers end at `/health`'s `data_end_year`. `DATA_END_YEAR` (2025, `services/constants.js`) is
  only the fallback while `/health` has not answered, when the API is offline (sample data) or for an older API.
- **Missing is not zero.** A `/calendar` month with `coverage: "none"` (no data loaded, or in the future) is shown as no data
  (blank calendar cell, empty table cell and CSV value, left out of medians and "latest month"), never as the API's zero
  fill. A `"partial"` month (near-real-time, still being observed) gets a dashed yellow outline and a *partial* mark, and is
  left out of historical medians. Logic: `src/utils/calendarMatrix.js` and `src/utils/stats.js`.

## Processing Center
`/dashboard/processing` (sidebar: *Processing Center*) shows what the backend worker did with each FIRMS poll: scheduler
state, approved dataset and calibration versions, the job list (filter by kind and status), and a job drawer (`?job=<id>`
in the URL) with the steps timeline, counts, hard gates G1-G4, soft flags, the decision and its basis, and the three-agent
review (Filter, Harmonization and Validation reviewers, the verdict with reasons and flagged cell-months, every LLM attempt
with model, outcome, latency, tokens and error, so fallbacks between free models are visible). It is read-only (no "run"
button) and refreshes every 30 s while the tab is visible. Without a database the API answers 503 and the page says the
processing database is not connected; there is no mock data. Mapping: `src/utils/processing.js`.

## Dev checks
Plain Node scripts for the pure mapping functions (no test runner): `node dev-checks/live-data.check.mjs` (live fires,
reports) and `node dev-checks/processing-calendar.check.mjs` (job-detail mapping, calendar coverage, stats skipping
"none" months).
