# EmberGuard weather map implementation

The supplied repository is the weather starter, also present in the adjacent ZIP.
It contains no geographical cell dataset, NASA history, API application or frontend.
Preserve `pipeline/weather.py` and its two-location CLI and build on its output contract.

1. Test missing values, retry handling, deterministic 0.5° grid IDs, coordinate joins,
   batches of at most eight, persistent caches and failure preservation.
2. Implement `pipeline/grid.py`, `pipeline/weather_map.py` and extend the adapter's
   value validation and bounded retries. Use half-degree boundaries from 35–37.5°N,
   8–11.5°E; allow an existing Polygon FeatureCollection to supply exact cell IDs.
   Default rectangles cover a northern Tunisia bounding box, including offshore and
   border portions; disclose that point forecasts are not polygon averages.
3. Add FastAPI cache-only endpoints, GeoJSON export and coordinate-based NASA FIRMS
   CSV import/calendar endpoints. Missing history is unavailable, never zero fires.
4. Add React/Leaflet application, rolling-window layers, exact values/hourly details,
   date calendar and detection markers linked by grid ID. Serve the build via FastAPI.
5. Run offline regression/API tests, frontend build and browser tests where available.
   Attempt a real refresh with server-side .env and document actual network blockers.

No synthetic production forecast or fire data. Never copy credentials into frontend,
logs or generated files. Requests use the existing five Meteomatics parameters.
Publish one atomic snapshot; preserve the last good snapshot if any batch fails.
Use disk batch caches to resume within one forecast start hour, and a three-hour TTL
for the assembled snapshot. UI interactions never issue provider requests.

## Verification record

Implemented all five steps. Original two-location integration tests remain green.
22 backend tests and two frontend unit tests pass; Vite production build passes
using pinned CDN Leaflet because its npm package was absent from local caches.
Generated geometry for 35 cells and actual two-point GeoJSON from the supplied
Meteomatics cache; never assigned those point values to grid cells.

External blockers: restricted DNS prevents a new Meteomatics grid fetch and npm
Leaflet install. No NASA data or original calendar/cell files were supplied.
Socket/browser end-to-end verification is unavailable; ASGI requests verify route
behaviour. The complete grid forecast artifact requires a successful live refresh.

Independent review identified and corrected four issues: malformed batch cache
recovery, nested API cache validation, unavailable legacy forecast preservation,
and repeated blocking NASA CSV reads. Regression tests cover these; NASA imports
are indexed by modification time off the event loop and marker output is bounded.
