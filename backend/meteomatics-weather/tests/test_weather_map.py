import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pipeline.weather import build_output
from pipeline.grid import make_grid, locate_cell
from pipeline.weather_map import refresh, to_geojson, points_geojson

START = datetime(2026, 11, 14, 15, tzinfo=timezone.utc)


def payload(areas, start=START):
    from datetime import timedelta
    values = {"t_2m:C": 22, "relative_humidity_2m:p": 40,
              "wind_speed_10m:ms": 5, "wind_gusts_10m_1h:ms": 9, "precip_1h:mm": 1}
    return {"status": "OK", "data": [
        {"parameter": param, "coordinates": [
            {"lat": area["lat"], "lon": area["lon"], "dates": [
                {"date": (start + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "value": value} for hour in range(72)]} for area in areas.values()]}
        for param, value in values.items()]}


class GridTests(unittest.TestCase):
    def test_legacy_forecasts_export_as_points_without_spatial_fabrication(self):
        areas = {"bizerte": {"lat": 37.27, "lon": 9.87}}
        out = build_output(payload(areas), areas, START)
        exported = points_geojson(out)
        self.assertEqual(exported["features"][0]["geometry"], {
            "type": "Point", "coordinates": [9.87, 37.27]})
        self.assertEqual(len(exported["features"]), 1)
        self.assertEqual(exported["features"][0]["properties"]["daily"][0]["rainfall_total_mm"], 24)

    def test_cells_and_coordinate_link(self):
        grid = make_grid()
        self.assertEqual(len(grid["features"]), 35)
        cell = locate_cell(grid, 37.27, 9.87)
        self.assertEqual(cell["properties"]["grid_id"], "tn_37.0_9.5")
        self.assertIsNone(locate_cell(grid, 0, 0))
        boundary = locate_cell(grid, 36.5, 9.5)
        self.assertEqual(boundary["properties"]["grid_id"], "tn_36.5_9.5")

    def test_reuse_existing_cells(self):
        grid = make_grid()
        grid["features"][0]["properties"]["grid_id"] = "existing-cell"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cells.geojson"
            path.write_text(json.dumps(grid))
            self.assertEqual(make_grid(path)["features"][0]["properties"]["grid_id"], "existing-cell")

    def test_missing_and_reserved_values_never_become_weather(self):
        areas = {"point": {"lat": 36.25, "lon": 9.25}}
        for invalid in [-999, -888, -777, -666, True, float("nan"), float("inf")]:
            raw = payload(areas)
            raw["data"][0]["coordinates"][0]["dates"][0]["value"] = invalid
            out = build_output(raw, areas, START)
            self.assertIsNone(out["areas"]["point"]["hourly"][0]["temperature_c"])

    def test_batching_cache_and_failure_preservation(self):
        grid = make_grid()
        calls = []

        def fetch(url):
            locations = url.split("/")[-2].split("+")
            self.assertLessEqual(len(locations), 8)
            areas = {str(index): dict(zip(["lat", "lon"], map(float, point.split(","))))
                     for index, point in enumerate(locations)}
            calls.append(url)
            from pipeline.weather import parse_utc
            return payload(areas, parse_utc(url.split("/")[3].split("--")[0]))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weather_grid.json"
            out = refresh(grid, path, now=START, fetch=fetch)
            self.assertEqual(len(calls), 5)
            self.assertEqual(len(out["areas"]), 35)
            self.assertEqual(out["areas"]["tn_37.0_9.5"]["daily"][2]["rainfall_total_mm"], 24)
            self.assertEqual(len(to_geojson(grid, out)["features"]), 35)
            refresh(grid, path, now=START, fetch=fetch)
            self.assertEqual(len(calls), 5)
            before = path.read_bytes()
            with self.assertRaises(RuntimeError):
                refresh(grid, path, now=START, force=True,
                        fetch=lambda url: (_ for _ in ()).throw(RuntimeError("unavailable")))
            self.assertEqual(path.read_bytes(), before)

    def test_missing_rain_hour_omits_total_and_counts_coverage(self):
        areas = {"point": {"lat": 36.25, "lon": 9.25}}
        raw = payload(areas)
        raw["data"][-1]["coordinates"][0]["dates"][0]["value"] = -999
        out = build_output(raw, areas, START)
        summary = out["areas"]["point"]["daily"][0]
        self.assertIsNone(summary["rainfall_total_mm"])
        self.assertEqual(summary["rainfall_hours_available"], 23)
        self.assertEqual(summary["hours_available"]["precip_1h_mm"], 23)

    def test_batch_resume_after_later_batch_failure(self):
        from pipeline.weather import parse_utc
        calls = []
        def fetch(url):
            calls.append(url)
            if len(calls) == 2:
                raise RuntimeError("Quota")
            areas = {str(index): dict(zip(["lat", "lon"], map(float, point.split(","))))
                     for index, point in enumerate(url.split("/")[-2].split("+"))}
            return payload(areas, parse_utc(url.split("/")[3].split("--")[0]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weather_grid.json"
            with self.assertRaises(RuntimeError):
                refresh(make_grid(), path, now=START, fetch=fetch)
            self.assertFalse(path.exists())
            result = refresh(make_grid(), path, now=START, fetch=fetch)
            self.assertEqual(len(result["areas"]), 35)
            self.assertEqual(len(calls), 6)

    def test_invalid_batch_cache_is_refetched(self):
        from pipeline.weather import next_hour_utc
        import hashlib
        from pipeline.weather import build_url
        grid = make_grid()
        points = {feature["properties"]["grid_id"]: feature["properties"] for feature in grid["features"][:8]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / ".weather-cache"
            cache.mkdir()
            key = hashlib.sha256(build_url(next_hour_utc(START), points).encode()).hexdigest()
            (cache / (key + ".json")).write_text("[]")
            with patch("pipeline.weather_map.get_forecast", side_effect=RuntimeError("refetched")):
                with self.assertRaisesRegex(RuntimeError, "refetched"):
                    refresh(grid, root / "snapshot.json", now=START)


if __name__ == "__main__":
    unittest.main()
