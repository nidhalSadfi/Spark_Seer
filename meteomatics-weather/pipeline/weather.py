"""EmberGuard: Meteomatics 72-hour point-forecast adapter.

Run from repository root:
    python -m pipeline.weather --areas data/demo_areas.json --output data/weather.json

Uses multi-location requests with at most eight points to reduce API usage.
Weather context ONLY; this is not a fire-danger index or ignition forecast.
"""
from __future__ import annotations

import argparse
import json
import os
import math
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

API_BASE = "https://api.meteomatics.com"
FIELDS = {
    "t_2m:C": "temperature_c",
    "relative_humidity_2m:p": "relative_humidity_pct",
    "wind_speed_10m:ms": "wind_speed_ms",
    "wind_gusts_10m_1h:ms": "wind_gust_ms",
    "precip_1h:mm": "precip_1h_mm",
}


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def next_hour_utc(now: datetime) -> datetime:
    now = now.astimezone(timezone.utc)
    return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def read_areas(path: Path) -> dict[str, dict[str, Any]]:
    areas = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(areas, dict) or not areas or len(areas) > 10:
        raise ValueError("Expected 1–10 named forecast points in the areas JSON")
    for key, area in areas.items():
        lat, lon = float(area["lat"]), float(area["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"Invalid coordinate for {key}")
        area["lat"] = lat
        area["lon"] = lon
    return areas


def build_url(start: datetime, areas: dict[str, dict[str, Any]]) -> str:
    end = start + timedelta(hours=71)  # exactly 72 hourly valid times
    timerange = f"{iso_utc(start)}--{iso_utc(end)}:PT1H"
    parameters = ",".join(FIELDS)
    locations = "+".join(f"{a['lat']:.5f},{a['lon']:.5f}" for a in areas.values())
    return f"{API_BASE}/{timerange}/{parameters}/{locations}/json"


def get_forecast(url: str, attempts: int = 3) -> dict[str, Any]:
    user = os.getenv("METEOMATICS_USER")
    password = os.getenv("METEOMATICS_PASSWORD")
    if not user or not password:
        raise RuntimeError("Set METEOMATICS_USER and METEOMATICS_PASSWORD in .env")
    try:
        for attempt in range(attempts):
            try:
                response = requests.get(url, auth=(user, password), timeout=(10, 60))
            except requests.RequestException:
                if attempt + 1 == attempts:
                    raise
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < attempts:
                    delay = float(2 ** attempt)
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            try:
                                delay = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                            except (TypeError, ValueError):
                                pass
                    if delay > 60:
                        raise RuntimeError("Meteomatics rate limit: retry later; previous cache preserved")
                    time.sleep(max(0, delay))
                    continue
            response.raise_for_status()
            break
        payload = response.json()
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "unknown"
        hints = {401: "Check username/password", 403: "Check parameter access or account permissions", 429: "Quota/rate limit reached"}
        raise RuntimeError(f"Meteomatics HTTP {code}: {hints.get(code, 'Check request parameters and account access')}") from exc
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError("Meteomatics request failed: network error or invalid JSON") from None
    if not isinstance(payload, dict) or payload.get("status") != "OK" or not isinstance(payload.get("data"), list):
        raise RuntimeError("Unexpected Meteomatics response: missing status OK or data")
    return payload


def valid_value(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value not in {-999, -888, -777, -666})


def ensure_usable_forecast(output: dict) -> None:
    if not output.get("areas") or any(
        not any(valid_value(row.get(field)) for row in area["hourly"] for field in FIELDS.values())
        for area in output["areas"].values()
    ):
        raise RuntimeError("Provider returned no usable data for a point; previous cache preserved")


def non_null_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [float(row[key]) for row in rows if valid_value(row.get(key))]


def maximum(rows: list[dict[str, Any]], key: str, multiplier: float = 1.0) -> float | None:
    vals = non_null_values(rows, key)
    return round(max(vals) * multiplier, 2) if vals else None


def minimum(rows: list[dict[str, Any]], key: str) -> float | None:
    vals = non_null_values(rows, key)
    return round(min(vals), 2) if vals else None


def build_output(payload: dict[str, Any], areas: dict[str, dict[str, Any]], start: datetime,
                 retrieved_at: datetime | None = None) -> dict[str, Any]:
    """Transform standard Meteomatics JSON response into a stable frontend contract."""
    retrieved_at = retrieved_at or datetime.now(timezone.utc)
    valid_times = [iso_utc(start + timedelta(hours=i)) for i in range(72)]
    matrix: dict[str, dict[str, dict[str, Any]]] = {
        key: {t: {"valid_time_utc": t, **{f: None for f in FIELDS.values()}} for t in valid_times}
        for key in areas
    }
    received = {key: set() for key in areas}

    for param_block in payload.get("data", []):
        param = param_block.get("parameter")
        if param not in FIELDS:
            continue
        field = FIELDS[param]
        for coords in param_block.get("coordinates", []):
            lat, lon = coords.get("lat"), coords.get("lon")
            if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                continue
            # Coordinate order is not assumed. Reject unrelated points.
            matching = [key for key, area in areas.items()
                        if abs(area["lat"] - lat) < 0.02 and abs(area["lon"] - lon) < 0.02]
            if len(matching) != 1:
                continue
            key = matching[0]
            received[key].add(param)
            for point in coords.get("dates", []):
                try:
                    timestamp = iso_utc(parse_utc(point["date"]))
                except (KeyError, ValueError, TypeError):
                    continue
                value = point.get("value")
                if timestamp in matrix[key]:
                    matrix[key][timestamp][field] = value if valid_value(value) else None

    result_areas = {}
    for key, area in areas.items():
        hourly = list(matrix[key].values())
        for row in hourly:
            if isinstance(row["wind_speed_ms"], (int, float)):
                row["wind_speed_kmh"] = round(row["wind_speed_ms"] * 3.6, 2)
            else:
                row["wind_speed_kmh"] = None
            if isinstance(row["wind_gust_ms"], (int, float)):
                row["wind_gust_kmh"] = round(row["wind_gust_ms"] * 3.6, 2)
            else:
                row["wind_gust_kmh"] = None
        windows = []
        for i in range(3):
            block = hourly[i * 24:(i + 1) * 24]
            rains = non_null_values(block, "precip_1h_mm")
            windows.append({
                "period": f"{i * 24}-{(i + 1) * 24}h",
                "first_valid_time_utc": block[0]["valid_time_utc"],
                "last_valid_time_utc": block[-1]["valid_time_utc"],
                "sample_count": len(block),
                "max_temperature_c": maximum(block, "temperature_c"),
                "min_humidity_pct": minimum(block, "relative_humidity_pct"),
                "max_wind_speed_kmh": maximum(block, "wind_speed_ms", multiplier=3.6),
                "max_wind_gust_kmh": maximum(block, "wind_gust_ms", multiplier=3.6),
                # Do not report a misleading low rainfall total when hours are missing.
                "rainfall_total_mm": round(sum(rains), 2) if len(rains) == len(block) else None,
                "rainfall_hours_available": len(rains),
                "hours_available": {field: len(non_null_values(block, field)) for field in FIELDS.values()},
            })
        missing = [p for p in FIELDS if p not in received[key]]
        warnings = [f"Missing parameter: {p}" for p in missing]
        warnings += [f"No valid values for parameter: {p}" for p, f in FIELDS.items()
                     if p not in missing and not non_null_values(hourly, f)]
        if any(w["rainfall_total_mm"] is None for w in windows):
            warnings.append("Incomplete rainfall coverage; total omitted where necessary")
        if all(row["temperature_c"] is None for row in hourly):
            warnings.append("Temperature unavailable; cannot use this forecast as a complete assessment")
        result_areas[key] = {
            "area": key,
            "name": area.get("name", key),
            "lat": area["lat"], "lon": area["lon"],
            "spatial_scope": "point forecast at selected area centre, not area-wide average",
            "hourly": hourly,
            "daily": windows,  # three rolling 24-hour windows, not calendar days
            "warnings": warnings,
        }

    return {
        "provider": "meteomatics",
        "product": "weather_context_not_fire_danger_index",
        "retrieved_at_utc": iso_utc(retrieved_at),
        "provider_response_generated_at_utc": payload.get("dateGenerated"),
        "model_run_issued_at_utc": None,  # unknown unless a source-specific init-time query is made
        "valid_from_utc": valid_times[0], "valid_until_utc": valid_times[-1],
        "interval": "PT1H", "forecast_window_hours": 72,
        "units": {"temperature": "C", "relative_humidity": "%", "wind": "km/h", "precipitation": "mm"},
        "note": "Each daily item is a rolling 24-hour window. Precipitation is accumulated over the hour preceding its valid time.",
        "areas": result_areas,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Meteomatics 72-hour weather for EmberGuard demo areas")
    parser.add_argument("--areas", type=Path, default=Path("data/demo_areas.json"))
    parser.add_argument("--output", type=Path, default=Path("data/weather.json"))
    parser.add_argument("--dry-run", action="store_true", help="Print URL without network request")
    args = parser.parse_args()
    load_dotenv(Path.cwd() / ".env")
    areas = read_areas(args.areas)
    start = next_hour_utc(datetime.now(timezone.utc))
    items = list(areas.items())
    urls = [build_url(start, dict(items[offset:offset + 8])) for offset in range(0, len(items), 8)]
    if args.dry_run:
        print("Meteomatics request URL (without credentials):")
        print("\n".join(urls))
        return
    print(f"Fetching {len(areas)} location(s), 5 parameters, 72 hourly points")
    payload = {"status": "OK", "data": []}
    for url in urls:
        batch = get_forecast(url)
        payload["data"].extend(batch["data"])
        payload["dateGenerated"] = batch.get("dateGenerated")
    output = build_output(payload, areas, start)
    ensure_usable_forecast(output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement preserves previous good cache on error.
    tmp = args.output.with_name(args.output.name + ".tmp")
    tmp.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(args.output)
    print(f"Wrote {args.output} (provider=meteomatics; areas={', '.join(output['areas'])})")
    for key, data in output["areas"].items():
        print(f"  {key}: {len(data['hourly'])} hours, {len(data['daily'])} windows; warnings={data['warnings']}")


if __name__ == "__main__":
    main()
