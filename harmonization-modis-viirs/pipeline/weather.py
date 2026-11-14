#!/usr/bin/env python3
"""Historical weather at area centres from Open-Meteo.

The P1 preview is the world 0.5° grid: 259,200 centres. That path is a
dry run and does not call Open-Meteo. The four North Africa demo boxes
remain a regional fetch, one request per box. No API key.

    python pipeline/weather.py docs/p1/global_areas.json data/weather-global --dry-run
    python pipeline/weather.py --world-grid --dry-run data/weather-global
    python pipeline/weather.py data/demo_areas.json data/weather
    python pipeline/weather.py docs/p1/demo_areas.json data/weather

``--dry-run`` prints the centres and does not call Open-Meteo.
``--step 30`` is a coarser preview (72 centres). ``--fetch`` would request
every centre of a world grid and is outside the P1 run.
"""

from __future__ import annotations

import argparse
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
# Same 0.5° cell size as the fire grid. Longitude spans [-180, 180), latitude [-90, 90].
WORLD_WEST = -180.0
WORLD_SOUTH = -90.0
WORLD_EAST = 180.0
WORLD_NORTH = 90.0
DEFAULT_GRID_STEP = 0.5


def centre(bbox: list[float]) -> tuple[float, float]:
    min_lon, min_lat, max_lon, max_lat = bbox
    if not (min_lon < max_lon and min_lat < max_lat):
        raise ValueError(f"bbox is not minLon,minLat,maxLon,maxLat: {bbox}")
    return (min_lat + max_lat) / 2.0, (min_lon + max_lon) / 2.0


def _divisions(start: float, stop: float, step: float) -> int:
    if step <= 0:
        raise ValueError(f"grid step must be positive, got {step}")
    span = stop - start
    count = int(round(span / step))
    if count <= 0 or abs(count * step - span) > 1e-6 * max(1.0, abs(span)):
        raise ValueError(f"step {step} does not tile {start} to {stop}")
    return count


def _edges(start: float, stop: float, step: float) -> list[float]:
    count = _divisions(start, stop, step)
    return [round(start + i * step, 6) for i in range(count + 1)]


def _coord(value: float) -> str:
    if abs(value) < 5e-7:
        return "0"
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


def grid_summary(
    step: float = DEFAULT_GRID_STEP,
    west: float = WORLD_WEST,
    south: float = WORLD_SOUTH,
    east: float = WORLD_EAST,
    north: float = WORLD_NORTH,
) -> dict:
    """Count and corner boxes of a regular world grid, without building every cell."""
    _divisions(south, north, step)
    _divisions(west, east, step)
    first = [round(west, 6), round(south, 6), round(west + step, 6), round(south + step, 6)]
    last = [round(east - step, 6), round(north - step, 6), round(east, 6), round(north, 6)]
    return {
        "count": grid_count(step, west, south, east, north),
        "step": step,
        "first_bbox": first,
        "last_bbox": last,
    }


def grid_count(
    step: float = DEFAULT_GRID_STEP,
    west: float = WORLD_WEST,
    south: float = WORLD_SOUTH,
    east: float = WORLD_EAST,
    north: float = WORLD_NORTH,
) -> int:
    return _divisions(south, north, step) * _divisions(west, east, step)


def world_grid_areas(
    step: float = DEFAULT_GRID_STEP,
    west: float = WORLD_WEST,
    south: float = WORLD_SOUTH,
    east: float = WORLD_EAST,
    north: float = WORLD_NORTH,
) -> list[dict]:
    """Boxes covering [west, east) x [south, north) on a regular grid.

    ``id`` is the southwest corner, the same idea as a fire ``cell_id``.
    The Open-Meteo point is the box centre.
    """
    areas = []
    lat_edges = _edges(south, north, step)
    lon_edges = _edges(west, east, step)
    for lat0, lat1 in zip(lat_edges, lat_edges[1:]):
        for lon0, lon1 in zip(lon_edges, lon_edges[1:]):
            bbox = [lon0, lat0, lon1, lat1]
            lat, lon = centre(bbox)
            areas.append({
                "id": f"{_coord(lat0)}_{_coord(lon0)}",
                "name": f"{lat:.2f}, {lon:.2f}",
                "bbox": bbox,
            })
    return areas


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


def _grid_from_payload(payload: dict, step: float | None) -> dict:
    grid = payload.get("grid") or {}
    return {
        "step": float(step if step is not None else grid.get("step_deg", DEFAULT_GRID_STEP)),
        "west": float(grid.get("west", WORLD_WEST)),
        "south": float(grid.get("south", WORLD_SOUTH)),
        "east": float(grid.get("east", WORLD_EAST)),
        "north": float(grid.get("north", WORLD_NORTH)),
    }


def load_areas(path: Path, step: float | None = None) -> tuple[list[dict], bool]:
    """Return ``(areas, is_grid)``.

    A grid file stores ``grid`` instead of ``areas`` and is expanded here.
    ``step`` overrides ``grid.step_deg`` when the file is a grid.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "grid" in payload:
        if "areas" in payload:
            raise SystemExit(f"{path} has both areas and grid; keep one")
        spec = _grid_from_payload(payload, step)
        return world_grid_areas(**spec), True
    if step is not None:
        raise SystemExit("--step applies to a world grid")
    areas = payload["areas"] if isinstance(payload, dict) else payload
    if not areas:
        raise SystemExit(f"no areas in {path}")
    return list(areas), False


def print_dry_run(areas: list[dict], step: float | None = None) -> None:
    first_lat, first_lon = centre(areas[0]["bbox"])
    last_lat, last_lon = centre(areas[-1]["bbox"])
    step_text = f"  step={step:g}" if step is not None else ""
    print(f"dry-run  areas={len(areas)}{step_text}  {START_DATE}..{END_DATE}")
    print(f"first  {areas[0]['id']}  centre={first_lat:.4f},{first_lon:.4f}  bbox={areas[0]['bbox']}")
    print(f"last   {areas[-1]['id']}  centre={last_lat:.4f},{last_lon:.4f}  bbox={areas[-1]['bbox']}")
    print("Open-Meteo was not called")


def print_grid_dry_run(spec: dict) -> None:
    summary = grid_summary(spec["step"], spec["west"], spec["south"], spec["east"], spec["north"])
    first_lat, first_lon = centre(summary["first_bbox"])
    last_lat, last_lon = centre(summary["last_bbox"])
    print(f"dry-run  areas={summary['count']}  step={summary['step']:g}  {START_DATE}..{END_DATE}")
    print(f"first  centre={first_lat:.4f},{first_lon:.4f}  bbox={summary['first_bbox']}")
    print(f"last   centre={last_lat:.4f},{last_lon:.4f}  bbox={summary['last_bbox']}")
    print("Open-Meteo was not called")


def run(
    out_dir: Path,
    areas: list[dict],
    *,
    dry_run: bool = False,
    grid: bool = False,
    fetch_grid: bool = False,
    step: float | None = None,
) -> list[Path]:
    if dry_run:
        print_dry_run(areas, step if grid else None)
        return []
    if grid and not fetch_grid:
        raise SystemExit(
            f"world grid has {len(areas)} centres. "
            "Open-Meteo was not called. Re-run with --dry-run, or pass --fetch to request every centre."
        )
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
            grid_body = record["response"]
            print(
                f"wrote {dest}  days={days}  "
                f"centre={record['centre']['latitude']:.3f},{record['centre']['longitude']:.3f}  "
                f"grid={grid_body.get('latitude')},{grid_body.get('longitude')}  "
                f"fetched_at={record['fetched_at']}"
            )
            written.append(dest)
    if failures:
        raise SystemExit("weather failed for: " + "; ".join(failures))
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python pipeline/weather.py",
        description=(
            "Historical weather at area centres from Open-Meteo. "
            "The world 0.5° grid is a dry-run preview of 259200 centres. "
            "The four demo boxes in docs/p1/demo_areas.json are the regional fetch."
        ),
    )
    parser.add_argument("areas", nargs="?", help="area JSON, or the output directory when --world-grid is set")
    parser.add_argument("out_dir", nargs="?", help="directory for <id>.json files")
    parser.add_argument(
        "--world-grid",
        action="store_true",
        help="preview the 0.5° world grid (259200 centres) instead of an area file",
    )
    parser.add_argument("--step", type=float, default=None, help="grid step in degrees (overrides the file)")
    parser.add_argument("--dry-run", action="store_true", help="print centres and do not call Open-Meteo")
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="call Open-Meteo for a world grid (one request per cell; outside the P1 run)",
    )
    return parser


def _resolve(args: argparse.Namespace) -> tuple[list[dict] | None, dict | None, Path | None]:
    """Return ``(areas, grid_spec, out_dir)``. A dry-run grid leaves areas unset."""
    if args.world_grid:
        if args.areas and args.out_dir:
            raise SystemExit("--world-grid takes an output directory, not an area file")
        spec = {
            "step": args.step if args.step is not None else DEFAULT_GRID_STEP,
            "west": WORLD_WEST,
            "south": WORLD_SOUTH,
            "east": WORLD_EAST,
            "north": WORLD_NORTH,
        }
        out = Path(args.out_dir or args.areas) if (args.out_dir or args.areas) else None
        return None, spec, out
    if not args.areas or not args.out_dir:
        raise SystemExit(
            "usage: python pipeline/weather.py AREAS.json OUT_DIR [--dry-run]\n"
            "       python pipeline/weather.py docs/p1/global_areas.json data/weather-global --dry-run\n"
            "       python pipeline/weather.py docs/p1/demo_areas.json data/weather\n"
            "       python pipeline/weather.py --world-grid --dry-run data/weather-global"
        )
    path = Path(args.areas)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "grid" in payload:
        if "areas" in payload:
            raise SystemExit(f"{path} has both areas and grid; keep one")
        return None, _grid_from_payload(payload, args.step), Path(args.out_dir)
    if args.step is not None:
        raise SystemExit("--step applies to a world grid")
    areas = payload["areas"] if isinstance(payload, dict) else payload
    if not areas:
        raise SystemExit(f"no areas in {path}")
    return list(areas), None, Path(args.out_dir)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    areas, spec, out_dir = _resolve(args)
    if spec is not None and args.dry_run:
        print_grid_dry_run(spec)
        return
    if spec is not None and not args.fetch:
        count = grid_count(spec["step"], spec["west"], spec["south"], spec["east"], spec["north"])
        raise SystemExit(
            f"world grid has {count} centres. "
            "Open-Meteo was not called. Re-run with --dry-run, or pass --fetch to request every centre."
        )
    if spec is not None:
        if out_dir is None:
            raise SystemExit("pass an output directory")
        areas = world_grid_areas(**spec)
        run(out_dir, areas, grid=True, fetch_grid=True, step=spec["step"])
        return
    assert areas is not None and out_dir is not None
    run(out_dir, areas, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
