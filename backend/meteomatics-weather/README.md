# EmberGuard AI · Meteomatics weather map

React + Leaflet map and FastAPI service, extending the supplied weather starter in
its existing `pipeline/`, `api/`, `data/`, `tests/` layout. The adjacent ZIP was
inspected: it contains the same starter, not an additional map/calendar application.

## Start

Python 3.11+ and Node 18+:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-weather.txt
```

Keep your existing `.env`. For a new installation copy `.env.weather.example` to
`.env` and fill in `METEOMATICS_USER` and `METEOMATICS_PASSWORD` there. Credentials
are read only by Python; no frontend environment variables or provider calls exist.

```bash
# Existing two-location integration remains independent:
.venv/bin/python -m pipeline.weather
# Fetch the grid and export actual provider results:
.venv/bin/python -m pipeline.weather_map
npm install --prefix web
npm run build --prefix web
.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 and API documentation at http://127.0.0.1:8000/docs.
For development run `npm run dev --prefix web` alongside FastAPI and open the Vite
URL (normally http://127.0.0.1:5173). Vite proxies API calls; production is same-origin.
Run the map refresh every hour using your scheduler; its three-hour assembled-cache
TTL avoids excessive provider calls. `--force` bypasses assembled and batch caches.
Map interactions and the UI reload button read local caches only.

## Grid and output

Default: 35 rectangular 0.5° WGS84 cells covering 35–37.5°N, 8–11.5°E. This bounding
box includes sea and parts of neighbouring countries; it is not a country boundary
mask. Each forecast is sampled at the exact cell centre, never interpolated from
the original two points. `tn_37.0_9.5` is the cell starting at 37°N, 9.5°E.
No original geographical cells were supplied. Reuse yours with:

```bash
.venv/bin/python -m pipeline.weather_map --cells data/existing_cells.geojson
```

Input must be a FeatureCollection of 0.5° rectangular Polygon features with unique
`properties.grid_id` (or feature `id`). The original IDs and geometry are retained;
centre coordinates are derived from geometry. Alternatively set `WEATHER_CELLS_PATH`
in server `.env` so the CLI and API use the same cells before a snapshot exists.
The successful snapshot embeds its own grid, preventing mismatched coordinate joins.

- `data/weather_grid.geojson`: geometry only; generated even if provider access fails.
- `data/weather_grid.json`: atomic forecast snapshot with 72 hourly rows and three
  rolling 24-hour summaries per cell, plus grid/provenance metadata.
- `data/weather_map.geojson`: forecast-bearing cells for window 0, generated only
  after a successful real refresh. Other windows are available via the API.
- `data/.weather-cache/`: resumable raw provider batches, keyed by request URL,
  with user identity excluded; old batches are pruned after a successful refresh.
- `data/weather.json`: independent original two-point forecast cache, preserved.

For an actual weather-bearing GeoJSON artifact without new network access, export
the supplied cache with `.venv/bin/python -m pipeline.weather_map --export-legacy-only`.
This writes `data/weather_points.geojson` with the original two Point geometries,
72 hourly samples and three summaries each. Its original retrieval timestamp is
preserved; it is neither a fresh provider fetch nor a forecast across the full grid.

Five parameters: temperature, relative humidity, wind speed, hourly maximum gust,
and hourly precipitation. 35 cells require five sequential requests, at most eight
locations each. Retries are bounded to three attempts for network errors, HTTP 429
and 5xx. Numeric and HTTP-date Retry-After headers are honoured up to 60 seconds;
longer delays stop the refresh. Authentication errors are not retried. A failed
batch leaves the last assembled snapshot unchanged; completed batches can be reused
on retry within the same forecast start hour. Production never synthesizes values.

Missing/nonfinite values and all four Meteomatics reserved codes become `null`.
Rainfall totals require all 24 hours; extrema use available samples and include
per-field availability counts. The UI shows gaps, coverage and stale-cache notices.
The start is the next whole UTC hour; exactly 72 validity times follow. Hourly rain
is right-aligned and covers the preceding hour. Windows are rolling, not local
calendar days. Retrieval time is not a model issue time, which remains unknown.
See [Meteomatics reserved values](https://www.meteomatics.com/en/api/response/reserved-values/)
and [API request documentation](https://www.meteomatics.com/en/api/request/).

## API

| Route | Result |
| --- | --- |
| `GET /weather?area=bizerte` | Original named point, hourly/daily values and freshness |
| `GET /weather/map?window=0` | Cell GeoJSON; window integer 0, 1 or 2 |
| `GET /weather/grid-meta` | Grid geometry, bounds, resolution and join convention |
| `GET /weather/cell/{grid_id}` | Exact centre, 72 hourly rows and three summaries |
| `GET /fires/calendar?grid_id=tn_37.0_9.5&day=2024-07-01` | Imported observations and daily counts |
| `GET /health` | Server health; does not assert provider/cache availability |

Unknown areas/cells return 404; missing/corrupt caches return 503; invalid windows
or dates return 422. Weather responses include retrieval time, cache age and stale
flag (three hours). Stale results remain visible with a warning. Serve only
`web/dist`; never serve the repository root, `.env` or the raw provider cache.

## NASA historical calendar

No historical calendar code or NASA records were present in this starter. This
implementation supplies the calendar and a shared-coordinate integration adapter.
Place your real NASA FIRMS export at `data/fires.csv`, or set `NASA_FIRMS_CSV` in
server `.env`. Required CSV columns: `latitude,longitude,acq_date`; optional columns:
`acq_time,satellite,confidence,frp`. Invalid and out-of-grid rows are counted as
skipped. Dates use ISO YYYY-MM-DD. Cells include west/south edges and exclude
east/north edges, so detections join to exactly one adjacent cell. Selecting a
weather cell filters the calendar; selecting a date filters markers; clicking a
detection selects its weather cell. Historical dates never shift the current forecast.

Absent history is shown as unavailable, not zero fires. Counts refer to imported
satellite detections, not unique fire events; missing dates do not establish no fire.

The import is indexed by cell/date once per file modification in a bounded in-memory
cache. Parsing runs in a worker thread, keeping the API event loop responsive.
Calendar counts cover the full import; marker payloads default to 2,000 detections
(`limit=1..5000`). Responses include `matching_detections` and `truncated`, and the
UI asks users to narrow their date/cell selection when the marker limit is reached.
The initial index requires memory proportional to the imported records; benchmark
your full archive before deploying it on a small server.

## Tests and demo status

```bash
.venv/bin/python -m unittest discover -s tests -v
npm test --prefix web
npm run build --prefix web
```

Backend tests exercise the actual ASGI application with an in-process HTTP transport,
without binding sockets. Synthetic provider responses exist only in tests. The
frontend tests verify missing-value display and layer colour semantics.

In this execution environment DNS/network access to Meteomatics and npm is blocked.
The live grid refresh was attempted and failed safely, preserving the supplied
two-location cache. Geometry GeoJSON is generated; a forecast-bearing grid cannot
honestly be generated until provider access works. NASA history also needs your
actual CSV. No weather values have been copied across cells or invented.

Backend verification: 22 tests pass, including cache resume, failure preservation,
provider retries and actual ASGI endpoint requests. Both frontend unit tests pass.
The production build passes in CDN mode. Local socket/browser end-to-end access
could not be verified in this restricted environment; in-process checks confirm
the original `/weather` still returns the supplied Meteomatics cache, grid metadata
returns 35 cells, unavailable map data returns 503, and `.env` returns 404.

Python dependencies were recovered from local wheel caches. React/Vite dependencies
were recovered from npm caches, but Leaflet was not cached. For this restricted
environment the build supports `EMBERGUARD_CDN_LEAFLET=1`, loading pinned Leaflet
1.9.4 JS/CSS from esm.sh/unpkg instead of bundling it. This build still needs browser
internet access for Leaflet, map tiles and optional fonts. Normal `npm install` and
`npm run build` bundle Leaflet locally. Do not describe the CDN build as verified
offline or the unavailable live forecast as an end-to-end success.

Weather is contextual information, not an official fire-danger index or a
fire-ignition prediction. Coarse point forecasts do not describe every location
inside a cell or provide a safety decision by themselves.
