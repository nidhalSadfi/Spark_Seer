"""Cache-only routes. Include this router in the existing FastAPI application."""
import csv
import asyncio
import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from pipeline.fires import read_history
from pipeline.grid import locate_cell, make_grid
from pipeline.weather import parse_utc, FIELDS
from pipeline.weather_map import LIMITATION, to_geojson

router = APIRouter()
ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data/weather.json"
GRID_PATH = ROOT / "data/weather_grid.json"


def validate_snapshot(result: dict, require_grid: bool) -> None:
    def number_or_null(value):
        return value is None or (not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value))

    if not isinstance(result, dict) or result.get("provider") != "meteomatics":
        raise ValueError("Invalid provider")
    timestamp = result.get("retrieved_at_utc")
    if not isinstance(timestamp, str):
        raise ValueError("Invalid retrieval timestamp")
    parse_utc(timestamp)
    areas = result.get("areas")
    if not isinstance(areas, dict) or not areas:
        raise ValueError("Invalid forecast areas")
    for area in areas.values():
        for key, bound in (("lat", 90), ("lon", 180)):
            value = area[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > bound:
                raise ValueError("Invalid point coordinate")
        if not isinstance(area["hourly"], list) or len(area["hourly"]) != 72 or len(area["daily"]) != 3:
            raise ValueError("Invalid forecast windows")
        for summary in area["daily"]:
            for key in ("max_temperature_c", "min_humidity_pct", "max_wind_speed_kmh",
                        "max_wind_gust_kmh", "rainfall_total_mm", "rainfall_hours_available",
                        "first_valid_time_utc", "last_valid_time_utc"):
                if key not in summary:
                    raise ValueError("Invalid summary")
            if not all(isinstance(summary[key], str) for key in ("first_valid_time_utc", "last_valid_time_utc")):
                raise ValueError("Invalid summary timestamps")
            if not all(number_or_null(summary[key]) for key in ("max_temperature_c", "min_humidity_pct",
                       "max_wind_speed_kmh", "max_wind_gust_kmh", "rainfall_total_mm", "rainfall_hours_available")):
                raise ValueError("Invalid summary values")
        for row in area["hourly"]:
            if not isinstance(row["valid_time_utc"], str):
                raise ValueError("Invalid hourly sample")
            if not all(number_or_null(row[key]) for key in (*FIELDS.values(), "wind_speed_kmh", "wind_gust_kmh")):
                raise ValueError("Invalid hourly values")
        if not isinstance(area["warnings"], list) or not all(isinstance(warning, str) for warning in area["warnings"]):
            raise ValueError("Invalid warnings")
    if require_grid:
        grid = result["grid"]
        if grid["type"] != "FeatureCollection" or not grid["features"]:
            raise ValueError("Invalid grid")
        if {feature["properties"]["grid_id"] for feature in grid["features"]} != set(areas):
            raise ValueError("Grid and forecast points differ")
        for feature in grid["features"]:
            geometry = feature["geometry"]
            if geometry["type"] != "Polygon" or not geometry["coordinates"]:
                raise ValueError("Invalid grid geometry")
            for point in geometry["coordinates"][0]:
                if len(point) != 2 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in point):
                    raise ValueError("Invalid grid coordinate")


def read_cache(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        validate_snapshot(result, path == GRID_PATH)
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(503, "Weather cache unavailable; run the weather refresh command") from None


def metadata(result: dict) -> dict:
    try:
        age = round((datetime.now(timezone.utc) - parse_utc(result["retrieved_at_utc"])).total_seconds() / 3600, 2)
    except (KeyError, ValueError, TypeError, AttributeError):
        age = None
    return {"provider": result.get("provider"), "retrieved_at_utc": result.get("retrieved_at_utc"),
            "forecast_issued_at_utc": result.get("model_run_issued_at_utc"),
            "cache_age_hours": age, "stale": age is None or age < 0 or age >= 3,
            "forecast_type": result.get("product"), "limitation": LIMITATION}


def current_grid() -> dict:
    if GRID_PATH.exists():
        try:
            return read_cache(GRID_PATH)["grid"]
        except KeyError:
            raise HTTPException(503, "Grid snapshot invalid") from None
    configured = os.getenv("WEATHER_CELLS_PATH")
    try:
        return make_grid(Path(configured) if configured else None)
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, "Grid configuration unavailable") from None


@router.get("/weather")
async def weather(area: str = "bizerte"):
    result = read_cache(DATA_PATH)
    item = result["areas"].get(area)
    if item is None:
        raise HTTPException(404, "Area not found")
    return {**metadata(result), **item}


MAP_VARIABLES = {
    "temperature": ("max_temperature_c", "C"),
    "humidity": ("min_humidity_pct", "%"),
    "wind": ("max_wind_speed_kmh", "km/h"),
    "gust": ("max_wind_gust_kmh", "km/h"),
    "rainfall": ("rainfall_total_mm", "mm"),
}


@router.get("/weather/map")
async def weather_map(period: int | None = Query(None, ge=0, le=2),
                      window: int | None = Query(None, ge=0, le=2),
                      variable: str | None = Query(None)):
    if period is not None and window is not None and period != window:
        raise HTTPException(422, "period and window disagree; use period")
    if variable is not None and variable not in MAP_VARIABLES:
        raise HTTPException(422, f"variable must be one of {', '.join(MAP_VARIABLES)}")
    selected = period if period is not None else (window or 0)
    result = read_cache(GRID_PATH)
    try:
        collection = to_geojson(result["grid"], result, selected)
    except (KeyError, TypeError, IndexError):
        raise HTTPException(503, "Grid snapshot invalid; refresh weather") from None
    if variable is not None:
        field, unit = MAP_VARIABLES[variable]
        for feature in collection["features"]:
            feature["properties"].update(value=feature["properties"].get(field), unit=unit)
        collection.update(variable=variable, value_field=field, unit=unit)
    return {**collection, "period": selected, **metadata(result)}


@router.get("/weather/grid-meta")
async def grid_meta():
    grid = current_grid()
    coordinates = [point for feature in grid["features"] for point in feature["geometry"]["coordinates"][0]]
    return {"cell_count": len(grid["features"]), "resolution_degrees": .5,
            "crs": "EPSG:4326", "bbox": [min(point[0] for point in coordinates),
            min(point[1] for point in coordinates), max(point[0] for point in coordinates),
            max(point[1] for point in coordinates)], "grid": grid, "limitation": LIMITATION,
            "coordinate_join": "Half-open cells: west/south inclusive, east/north exclusive"}


@router.get("/weather/cell/{grid_id}")
async def weather_cell(grid_id: str):
    result = read_cache(GRID_PATH)
    item = result["areas"].get(grid_id)
    if item is None:
        raise HTTPException(404, "Cell not found")
    return {**metadata(result), "grid_id": grid_id, **item}


@router.get("/weather/point")
async def weather_point(lat: float = Query(..., ge=-90, le=90, allow_inf_nan=False),
                        lon: float = Query(..., ge=-180, le=180, allow_inf_nan=False)):
    result = read_cache(GRID_PATH)
    cell = locate_cell(result["grid"], lat, lon)
    if cell is None:
        raise HTTPException(404, "Point outside the weather grid")
    grid_id = cell["properties"]["grid_id"]
    item = result["areas"].get(grid_id)
    if item is None:
        raise HTTPException(503, "Grid snapshot invalid; refresh weather")
    return {**metadata(result), "query": {"lat": lat, "lon": lon}, "grid_id": grid_id,
            "spatial_note": "Forecast for the containing cell centre, not the exact query point",
            **item}


@router.get("/fires/calendar")
async def fire_calendar(grid_id: str | None = None, day: date | None = None,
                        limit: int = Query(2000, ge=1, le=5000)):
    path = Path(os.getenv("NASA_FIRMS_CSV", str(ROOT / "data/fires.csv")))
    grid = current_grid()
    if grid_id and not any(feature["properties"]["grid_id"] == grid_id for feature in grid["features"]):
        raise HTTPException(404, "Cell not found")
    try:
        return await asyncio.to_thread(read_history, path, grid, grid_id, day.isoformat() if day else None, limit)
    except (OSError, ValueError, csv.Error):
        raise HTTPException(503, "Historical fire CSV could not be read") from None
