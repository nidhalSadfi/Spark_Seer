#!/usr/bin/env python3
"""Historical weather at each demo-area centre from Open-Meteo.

One archive request per area. No API key. Writes ``data/weather/<id>.json``
with the fetch time and the daily valid times from the archive.

    python pipeline/weather.py data/demo_areas.json data/weather
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
START_DATE = "2001-01-01"
END_DATE = "2025-12-31"
DAILY = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "precipitation_sum",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "relative_humidity_2m_mean",
    "relative_humidity_2m_min",
    "relative_humidity_2m_max",
]


def centre(bbox: list[float]) -> tuple[float, float]:
    min_lon, min_lat, max_lon, max_lat = bbox
    if not (min_lon < max_lon and min_lat < max_lat):
        raise ValueError(f"bbox is not minLon,minLat,maxLon,maxLat: {bbox}")
    return (min_lat + max_lat) / 2.0, (min_lon + max_lon) / 2.0


def fetch_area(area: dict, session: requests.Session) -> dict:
    lat, lon = centre(area["bbox"])
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "start_date": START_DATE,
        "end_date": END_DATE,
        "daily": ",".join(DAILY),
        "timezone": "UTC",
        "wind_speed_unit": "ms",
    }
    response = None
    for attempt in range(6):
        response = session.get(ARCHIVE_URL, params=params, timeout=180)
        if response.status_code == 429 or response.status_code >= 500:
            wait = 15 * (attempt + 1)
            print(f"wait  {area['id']}  HTTP {response.status_code}  retry in {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        break
    assert response is not None
    response.raise_for_status()
    fetched_at = datetime.now(timezone.utc).isoformat()
    body = response.json()
    if "daily" not in body or "time" not in body["daily"]:
        reason = body.get("reason") or body
        raise RuntimeError(f"{area['id']}: archive response has no daily series ({reason})")
    return {
        "area_id": area["id"],
        "name": area["name"],
        "bbox": area["bbox"],
        "bbox_order": ["minLon", "minLat", "maxLon", "maxLat"],
        "centre": {"latitude": lat, "longitude": lon},
        "source": "Open-Meteo Historical Weather API",
        "source_url": ARCHIVE_URL,
        "fetched_at": fetched_at,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "note": "Weather context for the fire record. Not an official fire-danger index.",
        "variables": DAILY,
        "response": body,
    }


def load_areas(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    areas = payload["areas"] if isinstance(payload, dict) else payload
    if not areas:
        raise SystemExit(f"no areas in {path}")
    return areas


def run(areas_path: Path, out_dir: Path) -> list[Path]:
    areas = load_areas(areas_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    failures: list[str] = []
    with requests.Session() as session:
        session.headers["User-Agent"] = "emberledger-p1/0.1 (historical weather; open-meteo archive)"
        for index, area in enumerate(areas):
            if index:
                time.sleep(12.0)
            try:
                record = fetch_area(area, session)
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                failures.append(f"{area.get('id', '?')}: {exc}")
                print(f"FAIL  {area.get('id', '?')}: {exc}", file=sys.stderr)
                continue
            dest = out_dir / f"{area['id']}.json"
            dest.write_text(json.dumps(record, indent=2) + "\n")
            days = len(record["response"]["daily"]["time"])
            grid = record["response"]
            print(
                f"wrote {dest}  days={days}  "
                f"centre={record['centre']['latitude']:.3f},{record['centre']['longitude']:.3f}  "
                f"grid={grid.get('latitude')},{grid.get('longitude')}  "
                f"fetched_at={record['fetched_at']}"
            )
            written.append(dest)
    if failures:
        raise SystemExit("weather failed for: " + "; ".join(failures))
    return written


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        raise SystemExit("usage: python pipeline/weather.py data/demo_areas.json data/weather")
    run(Path(args[0]), Path(args[1]))


if __name__ == "__main__":
    main()
