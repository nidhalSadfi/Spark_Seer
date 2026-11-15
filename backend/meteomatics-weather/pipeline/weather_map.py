"""Batched provider refresh and atomic map snapshot. Never invoked per map click."""
import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from pipeline.grid import make_grid
from pipeline.weather import build_output, build_url, get_forecast, next_hour_utc, parse_utc

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data/weather_grid.json"
LIMITATION = ("Point forecasts at cell centres; colours are not polygon averages. "
              "Weather context, not an official fire-danger index or fire-ignition prediction. "
              "Default bounding-box cells may include sea and neighbouring countries.")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def to_geojson(grid: dict, snapshot: dict, window: int = 0) -> dict:
    features = []
    for feature in grid["features"]:
        properties = feature["properties"]
        area = snapshot["areas"][properties["grid_id"]]
        features.append({**feature, "properties": {
            **properties, **area["daily"][window], "warnings": area["warnings"],
            "spatial_scope": area["spatial_scope"]}})
    return {"type": "FeatureCollection", "provider": snapshot["provider"],
            "retrieved_at_utc": snapshot["retrieved_at_utc"],
            "valid_from_utc": snapshot["valid_from_utc"],
            "valid_until_utc": snapshot["valid_until_utc"],
            "window": window, "limitation": LIMITATION, "features": features}


def points_geojson(snapshot: dict) -> dict:
    return {"type": "FeatureCollection", "provider": snapshot["provider"],
            "retrieved_at_utc": snapshot["retrieved_at_utc"],
            "limitation": "Original cached point forecasts only; no weather values assigned to grid cells.",
            "features": [{"type": "Feature", "id": identifier,
                          "geometry": {"type": "Point", "coordinates": [area["lon"], area["lat"]]},
                          "properties": area}
                         for identifier, area in snapshot["areas"].items()]}


def refresh(grid: dict, output: Path = DEFAULT_OUTPUT, now: datetime | None = None,
            force: bool = False, fetch=None) -> dict:
    now = now or datetime.now(timezone.utc)
    fingerprint = hashlib.sha256(json.dumps(grid, sort_keys=True).encode()).hexdigest()
    if output.exists() and not force:
        try:
            cached = json.loads(output.read_text())
            age = (now - parse_utc(cached["retrieved_at_utc"])).total_seconds()
            if 0 <= age < 3 * 3600 and cached.get("grid_fingerprint") == fingerprint:
                return cached
        except (OSError, ValueError, KeyError, TypeError):
            pass
    start = next_hour_utc(now)
    points = {feature["properties"]["grid_id"]: feature["properties"] for feature in grid["features"]}
    items = list(points.items())
    merged = None
    batch_cache = output.parent / ".weather-cache"
    for offset in range(0, len(items), 8):
        batch = dict(items[offset:offset + 8])
        url = build_url(start, batch)
        cache_path = batch_cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        raw = None
        if cache_path.exists() and not force:
            try:
                raw = json.loads(cache_path.read_text())
                if not isinstance(raw, dict) or raw.get("status") != "OK" or not isinstance(raw.get("data"), list):
                    raw = None
            except (OSError, ValueError, AttributeError):
                raw = None
        if raw is None:
            raw = (fetch or get_forecast)(url)
            if raw.get("status") != "OK" or not isinstance(raw.get("data"), list):
                raise RuntimeError("Invalid provider batch; previous snapshot preserved")
        part = build_output(raw, batch, start, now)
        if any(not any(row[field] is not None for row in area["hourly"]
                       for field in ("temperature_c", "relative_humidity_pct", "wind_speed_ms",
                                     "wind_gust_ms", "precip_1h_mm")) for area in part["areas"].values()):
            raise RuntimeError("Provider returned no values for a grid point; previous snapshot preserved")
        atomic_json(cache_path, {key: raw[key] for key in ("status", "data", "dateGenerated") if key in raw})
        if merged is None:
            merged = part
            merged["provider_batch_generated_at_utc"] = []
        else:
            merged["areas"].update(part["areas"])
        merged["provider_batch_generated_at_utc"].append(raw.get("dateGenerated"))
    merged.update(grid_fingerprint=fingerprint, grid=grid, limitation=LIMITATION)
    atomic_json(output, merged)
    for old_cache in batch_cache.glob("*.json"):
        if now.timestamp() - old_cache.stat().st_mtime > 86400:
            old_cache.unlink(missing_ok=True)
    return merged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cells", type=Path, help="Existing 0.5 degree Polygon cells with grid_id")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--geojson", type=Path, default=ROOT / "data/weather_map.geojson")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--export-legacy-only", action="store_true",
                        help="Export existing two-point cache as Point GeoJSON without network calls")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if args.export_legacy_only:
        legacy = json.loads((ROOT / "data/weather.json").read_text())
        path = ROOT / "data/weather_points.geojson"
        atomic_json(path, points_geojson(legacy))
        print(f"Exported original cached point forecasts to {path}")
        return
    configured = os.getenv("WEATHER_CELLS_PATH")
    grid = make_grid(args.cells or (Path(configured) if configured else None))
    atomic_json(ROOT / "data/weather_grid.geojson", grid)
    try:
        result = refresh(grid, args.output, force=args.force)
    except RuntimeError as error:
        parser.exit(1, str(error) + "\n")
    atomic_json(args.geojson, to_geojson(grid, result))
    print(f"Saved {len(result['areas'])} real point forecasts and {args.geojson}")


if __name__ == "__main__":
    main()
