"""Coordinate join for an existing NASA FIRMS historical CSV export."""
import csv
import json
import math
from collections import Counter
from datetime import date
from functools import lru_cache
from pathlib import Path

from pipeline.grid import locate_cell


@lru_cache(maxsize=2)
def index_history(path: str, modified_ns: int, size: int, grid_json: str) -> tuple[dict, int]:
    grid = json.loads(grid_json)
    buckets = {}
    skipped = 0
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"latitude", "longitude", "acq_date"}.issubset(reader.fieldnames or []):
            raise ValueError("FIRMS CSV requires latitude, longitude and acq_date")
        for row in reader:
            try:
                latitude, longitude = float(row["latitude"]), float(row["longitude"])
                if not math.isfinite(latitude) or not math.isfinite(longitude):
                    raise ValueError("Nonfinite coordinate")
                observed = date.fromisoformat(row["acq_date"]).isoformat()
                cell = locate_cell(grid, latitude, longitude)
                if cell is None:
                    skipped += 1
                    continue
            except (ValueError, TypeError, KeyError):
                skipped += 1
                continue
            identifier = cell["properties"]["grid_id"]
            buckets.setdefault((identifier, observed), []).append({"type": "Feature", "geometry": {
                "type": "Point", "coordinates": [longitude, latitude]}, "properties": {
                    "grid_id": identifier, "date": observed,
                    "acq_time": row.get("acq_time"), "satellite": row.get("satellite"),
                    "confidence": row.get("confidence"), "frp_mw": row.get("frp")}})
    return buckets, skipped


def read_history(path: Path, grid: dict, grid_id: str | None = None,
                 selected_date: str | None = None, limit: int = 2000) -> dict:
    result = {"type": "FeatureCollection", "source": "NASA FIRMS imported historical CSV",
              "available": path.exists(), "features": [], "calendar": [], "skipped_rows": 0,
              "matching_detections": 0, "truncated": False,
              "note": "Satellite detections are historical observations, not unique fires or ignition predictions. "
                      "Counts describe imported records; absent dates do not prove zero fires."}
    if not result["available"]:
        return result
    stat = path.stat()
    buckets, skipped = index_history(str(path.resolve()), stat.st_mtime_ns, stat.st_size,
                                     json.dumps(grid, sort_keys=True))
    counts = Counter()
    for (identifier, observed), features in buckets.items():
        if grid_id and identifier != grid_id:
            continue
        counts[observed] += len(features)
        if selected_date and observed != selected_date:
            continue
        result["matching_detections"] += len(features)
        remaining = max(0, limit - len(result["features"]))
        result["features"].extend(features[:remaining])
    result["skipped_rows"] = skipped
    result["truncated"] = result["matching_detections"] > len(result["features"])
    result["calendar"] = [{"date": observed, "count": count} for observed, count in sorted(counts.items())]
    return result
