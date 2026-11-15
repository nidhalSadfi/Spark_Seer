# EmberGuard: Meteomatics Weather Integration

The complete map/API/demo startup instructions are now in [README.md](README.md).
The original two-location command and output contract below remain supported.

Drop-in weather integration for existing `pipeline/`, `api/`, `web/` project layout. Do not restructure the existing repository.

## Setup

1. Copy `pipeline/weather.py` to your existing `pipeline/` directory.
2. Copy `data/demo_areas.json` to `data/`, merge additional area centres when your P1 has finalized them.
3. Copy the two variables in `.env.weather.example` into repository-root `.env` (do not commit `.env`).
4. `pip install -r requirements-weather.txt` (or add the two dependencies to your existing requirements).
5. Put `.env` and `data/weather.json` into `.gitignore` if needed; do not commit credentials or constantly changing forecast cache.

```bash
python -m pipeline.weather --areas data/demo_areas.json --output data/weather.json --dry-run
python -m pipeline.weather --areas data/demo_areas.json --output data/weather.json
python -m unittest discover -s tests -p 'test_weather.py'
```

The live call needs Meteomatics authentication, network connectivity and entitlement for the selected weather parameters. The test command uses synthetic offline data and makes no API requests.

## Output contract

`data/weather.json` stores `provider`, `retrieved_at_utc`, source metadata and per-area `hourly` (72 values) and `daily` (three **rolling 24-hour** windows) sections.

A window includes `max_temperature_c`, `min_humidity_pct`, `max_wind_speed_kmh`, `max_wind_gust_kmh`, `rainfall_total_mm`, `rainfall_hours_available`, first/last forecast validity times and warnings.

- `precip_1h:mm` is right-aligned: each hourly value measures precipitation during the preceding hour.
- The `retrieved_at_utc` timestamp is **not** a forecast model issue time. `model_run_issued_at_utc` is `null` until you implement a source-specific initialization query.
- These are point forecasts at area centres, **not** per-0.5°-cell forecasts or official fire danger forecasts.
- If some parameters are unavailable, check your account and test the failing parameter individually; missing fields remain `null`, never zero.
- The script batches at most eight locations per request, avoiding per-click API calls.

## FastAPI integration

P3 can copy the route from `api/weather_router_example.py` or adapt its logic to the existing `GET /weather?area=bizerte` endpoint. If new, add to your FastAPI app:

```python
from api.weather_router_example import router as weather_router
app.include_router(weather_router)
```

Do not register a duplicate `GET /weather` if P3 already implemented one. P4 should use `daily` for the 3 weather cards and display source, data age and the statement **"Weather context, not an official fire-danger index."**

## Official docs

- https://www.meteomatics.com/en/api/getting-started/
- https://www.meteomatics.com/en/api/response/json/
- https://www.meteomatics.com/en/api/request/required-parameters/date-time-description/
- https://www.meteomatics.com/en/api/available-parameters/mos-parameters/
