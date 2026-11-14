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
`src/components/{layout,dashboard,map,calendar,analytics,common,ui}`, `pages/`, `services/`, `hooks/`, `context/`, `utils/`

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
| Header badge: live / synthetic / offline | `/health` + `/validation.synthetic` |

- If the backend is unreachable the app falls back to generated sample data and says so in the header and page footers.
- If `/validation` reports `"synthetic": true` (backend running `make_fake_data.py`), the UI labels everything as synthetic test data.
- The dataset covers Tunisia and northern Algeria (north of 34°N); an area drawn elsewhere on the map shows a "no detections" notice.
