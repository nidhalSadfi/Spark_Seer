"""Shared WGS84 geographical cells for forecast points and fire detections."""
import json
import math
from pathlib import Path


def make_grid(path: Path | None = None) -> dict:
    if path is not None:
        grid = json.loads(path.read_text(encoding="utf-8"))
        if grid.get("type") != "FeatureCollection" or not grid.get("features"):
            raise ValueError("Expected a nonempty cell FeatureCollection")
        identifiers = set()
        for feature in grid["features"]:
            properties = feature.setdefault("properties", {})
            identifier = properties.get("grid_id", feature.get("id"))
            if identifier is None or str(identifier) in identifiers:
                raise ValueError("Cells require unique grid_id properties or feature IDs")
            properties["grid_id"] = str(identifier)
            identifiers.add(str(identifier))
            if feature["geometry"]["type"] != "Polygon":
                raise ValueError("Cell geometry must be Polygon")
            ring = feature["geometry"]["coordinates"][0]
            if len(ring) != 5 or ring[0] != ring[-1]:
                raise ValueError("Cells must be closed rectangular polygons")
            west, south = min(point[0] for point in ring), min(point[1] for point in ring)
            east, north = max(point[0] for point in ring), max(point[1] for point in ring)
            if (not math.isclose(east - west, .5, abs_tol=1e-6)
                    or not math.isclose(north - south, .5, abs_tol=1e-6)
                    or set(map(tuple, ring)) != {(west, south), (east, south), (east, north), (west, north)}):
                raise ValueError("Cells must be 0.5 degree WGS84 rectangles")
            properties.update(lat=(south + north) / 2, lon=(west + east) / 2)
        return grid
    features = []
    for latitude_index in range(70, 75):
        for longitude_index in range(16, 23):
            south, west = latitude_index / 2, longitude_index / 2
            north, east = south + .5, west + .5
            identifier = f"tn_{south:.1f}_{west:.1f}"
            features.append({"type": "Feature", "id": identifier,
                             "properties": {"grid_id": identifier, "lat": south + .25,
                                            "lon": west + .25},
                             "geometry": {"type": "Polygon", "coordinates": [[
                                 [west, south], [east, south], [east, north],
                                 [west, north], [west, south]]]}})
    return {"type": "FeatureCollection", "features": features}


def locate_cell(grid: dict, lat: float, lon: float) -> dict | None:
    for feature in grid["features"]:
        ring = feature["geometry"]["coordinates"][0]
        west, east = min(point[0] for point in ring), max(point[0] for point in ring)
        south, north = min(point[1] for point in ring), max(point[1] for point in ring)
        if west <= lon < east and south <= lat < north:
            return feature
    return None
