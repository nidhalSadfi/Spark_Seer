"""Weather context: GET /weather?area=<id>.

Context only. This is not an official fire-danger index and not an ignition
probability. Values come straight from the Open-Meteo forecast API.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Query

from api import passport as pp

router = APIRouter()

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
TZ = "Africa/Tunis"
CACHE_TTL_S = 3600
HTTP_TIMEOUT_S = 10.0
LABEL = "Weather context, not an official fire-danger index"
ISSUE_TIME_NOTE = ("Open-Meteo does not publish a forecast issue time, so none is given. "
                   "fetched_at is when we retrieved the forecast.")

_cache: dict[str, tuple[float, dict]] = {}


def bbox_centre(bbox: list[float]) -> tuple[float, float]:
    min_lon, min_lat, max_lon, max_lat = bbox
    return round((min_lat + max_lat) / 2, 4), round((min_lon + max_lon) / 2, 4)


def fetch_forecast(lat: float, lon: float) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,relative_humidity_2m,wind_gusts_10m,precipitation",
        "forecast_days": 3,
        "timezone": TZ,
    }
    r = httpx.get(OPEN_METEO_URL, params=params, timeout=HTTP_TIMEOUT_S)
    r.raise_for_status()
    return r.json()


def summarise_daily(data: dict) -> list[dict]:
    h = data["hourly"]
    by_day: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for i, t in enumerate(h["time"]):
        day = t[:10]
        for key in ("temperature_2m", "relative_humidity_2m", "wind_gusts_10m", "precipitation"):
            v = h[key][i]
            if v is not None:
                by_day[day][key].append(v)

    def agg(fn, vals, nd=1):
        return round(fn(vals), nd) if vals else None

    return [{
        "date": day,
        "temp_max_c": agg(max, d["temperature_2m"]),
        "humidity_min_pct": agg(min, d["relative_humidity_2m"], 0),
        "gust_max_kmh": agg(max, d["wind_gusts_10m"]),
        "rain_total_mm": agg(sum, d["precipitation"]),
    } for day, d in sorted(by_day.items())][:3]


def historical_context(bbox: list[float], month: int) -> dict:
    try:
        cal = pp.calendar_for_bbox(bbox, pp.CAL_START, pp.CAL_END)
    except Exception as e:
        return {"available": False, "reason": f"calendar unavailable: {type(e).__name__}"}
    critical = cal.get("meta", {}).get("critical_months", [])
    return {
        "available": True,
        "month": month,
        "median_harmonized_same_month": pp.median_same_month(cal.get("months", []), month),
        "median_years": [pp.BASELINE_START_YEAR, pp.BASELINE_END_YEAR],
        "is_critical_month": month in critical,
        "critical_months": critical,
        "note": "Median of harmonized satellite hotspot detections (VIIRS terms) for this "
                "calendar month. Pre-2012 values are lower bounds.",
    }


@router.get("/weather")
def weather(area: str = Query(..., description="Area id from data/areas.json")) -> dict:
    now = time.time()
    hit = _cache.get(area)
    if hit and now - hit[0] < CACHE_TTL_S:
        return hit[1]

    a = pp.find_area(area)
    lat, lon = bbox_centre(a["bbox"])
    try:
        data = fetch_forecast(lat, lon)
        days = summarise_daily(data)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        raise HTTPException(503, f"Weather service unavailable (Open-Meteo: {type(e).__name__}). "
                                 "No weather data is shown rather than guessed values.")

    current_month = datetime.now(ZoneInfo(TZ)).month
    payload = {
        "area": {"id": a["id"], "name": a.get("name", a["id"]), "bbox": a["bbox"]},
        "point": {"latitude": lat, "longitude": lon},
        "label": LABEL,
        "source": "Open-Meteo forecast API",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "forecast_issue_time": None,
        "forecast_issue_time_note": ISSUE_TIME_NOTE,
        "timezone": TZ,
        "days": days,
        "historical_context": historical_context(a["bbox"], current_month),
    }
    _cache[area] = (now, payload)
    return payload
