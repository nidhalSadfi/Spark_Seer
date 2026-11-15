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
- **Live fire map** (`src/components/map/FireMap.jsx`): Leaflet on the Esri Dark Gray map or Esri satellite imagery (the *Satellite / Map* button; no API key; the choice is remembered in the browser), fire clusters sized by FRP over a glow layer of hotspot detections, 24 h playback slider, layer toggles, legend, rectangle AOI drawing and *Show my location* (browser geolocation: a blue dot with its accuracy circle and the distance to the nearest fire; browsers only allow it on https pages and on localhost). Wide views (*All areas*, or a box wider than 30°) get the API's grid answer of `/fires/live`: one marker per 0.25° or 0.5° square with its detection count and fire power, no playback (`fireFromCell` in `src/utils/fireClusters.js`). At most 500 markers are drawn (the strongest); every fire still glows.
- **Area menu**: *All areas* first (a whole-world box for the calendar and the passport, no box at all for the live map; the weather outlook asks for one area), then the areas of `GET /areas` grouped under *Tunisia and Algeria* and *World*.
- **Page text** stays plain: no addresses, ports, commands, settings or endpoint names. Setup and troubleshooting are in this README and the root one. Data comes from `getActiveFires()` in `fireApi.js`. It reads filters via `useFilters()` and calls `setAoi(...)` when an area is drawn. The map page adds the selected-fire panel (`FireDetails.jsx`) and the satellite-pass / fire-weather chart (`FirePassChart.jsx`).

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

Reports are shared through the API's `/reports` endpoint when it is reachable. When it is not (server down, no database, or an API
without the endpoint) they fall back to this browser's `localStorage` and the pages say so: those reports are not shared between
users or devices. All reads and writes go through `src/services/reportsStore.js`. The satellite check uses the map's feed.

## Social Alerts
`/dashboard/social` (sidebar: *Social Alerts*) lists the fires that the **incident agents** find in public posts and news
(Bluesky, X, GDELT news, Kanari signals), merged into one ticket per incident and checked against satellite detections
(*Satellite overlap*, *No satellite support* or *Mentioned*). A ticket shows its place and precision, a map, the satellite check,
the lead time between the first post and the first satellite detection, the 25-year history of its 0.5° cell, and every public
mention with its original link.

- **Report an alert.** *Report as fire alert* files the ticket as an unverified community report at its coordinates, with the
  ticket number, the number of mentions and the first link in the note. It then shows as a **?** on the map and goes through the
  usual review in *Fire Reports*. The page remembers which tickets were reported (from the note), so none is filed twice.
  *Report all … not yet reported* does the same for the tickets in the current filter, at most 25 per click.
- **Tickets are unverified public mentions**, not official alerts. When a ticket's location is only known to a wide area, the page
  says the **?** marks the centre of that area. Links from the feeds are shown only if they are `http(s)`.
- **The agents start with the backend.** Starting the API (`cd backend && uvicorn api.main:app --reload --port 8000`) also
  starts them, as a separate process on `127.0.0.1:8765` that holds the social-media keys (read from the repo-root `.env`)
  and makes the paid calls; there is nothing else to start. The page expects them at `VITE_INCIDENTS_URL` (default
  `http://127.0.0.1:8765`). When they do not answer it asks the backend (`GET /incidents/status`) and says what is wrong: the
  backend is not running, the agents are still starting or keep stopping (with the reason), autostart is off, or they are on
  another address. It looks again every few seconds, so it recovers by itself. The agents accept the dashboard's origin
  (`localhost:5173` and `:4173`); for another one set `INCIDENT_CORS_ORIGINS` for the backend.

### Agents tab (the console)
`/dashboard/social?tab=agents` is the agents' console inside the page, so no second window is needed:

- **Search.** Type countries or zones and run them live (last 6 to 72 hours) or **replay past dates** (3 Aug 2026 to yesterday, up
  to 31 days). A run asks a model and searches X, Bluesky and GDELT, so it spends the free quotas of the agents' keys; only one
  run goes at a time. **Schedules** (live only, at least every 15 min) can be added and removed.
- **The agents as a node graph**, drawn in Hairline's style (`src/components/social/AgentGraph.jsx` + `agentGraph.css`):
  isometric tiles for the model agents (stepped tile), the sources (ring) and the pipeline steps, dashed links that turn solid as
  data flows. It is live: the server's step events (server-sent events) light the tiles as the run goes, a tile rises while its
  agent works, its LED says working / done / failed / skipped, and a few words under it say what it produced ("kept 30/42",
  "5/8 fires"). Hover or focus a tile (Tab, arrow keys) to read what it does and said last; click or Enter to filter the activity
  log to it. It uses Hairline's `--hairline-*` colour variables, so it follows the palette of the landing page's figures. (The
  library's own figures have a fixed shape and take no data, so this one is drawn with the same rules instead of imported.)
- **Activity and recent runs.** The step log of the run (newest first) and the last 12 runs; pick one to see its graph again.
  Tickets and runs refresh as the agents work, and a toast says when a run finishes or fails.
- **Keys.** The agents read their keys from the repo-root `.env` (the file the backend uses; names in `.env.example`) or from the
  environment the backend was started in. The search form shows which keys they can see (never the values) and blocks *Run*
  while the Groq key is missing; a missing X, Bluesky or FIRMS key only skips that source. Restart the backend after editing `.env`.

Mapping and report text: `src/utils/incidents.js`; run events → graph state: `src/utils/agentFlow.js`; client:
`src/services/incidentsApi.js`.

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
reports), `node dev-checks/processing-calendar.check.mjs` (job-detail mapping, calendar coverage, stats skipping
"none" months), `node dev-checks/incidents.check.mjs` (Social Alerts tickets, link safety, report text) and
`node dev-checks/agent-flow.check.mjs` (agent events → graph state).
