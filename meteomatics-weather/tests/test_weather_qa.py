"""QA coverage for the Meteomatics adapter and the cache-only weather API.

Offline only: provider responses are synthetic; real cached snapshots in data/ are
read (never refreshed) when present.
"""
import contextlib
import io
import json
import logging
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import requests

from api.main import app
from api import weather_router_example as routes
from pipeline.grid import make_grid, locate_cell
from pipeline.weather import FIELDS, build_output, build_url, get_forecast, iso_utc
from pipeline.weather_map import refresh
from test_api import PollingEventLoopPolicy  # noqa: F401  (installs the event-loop policy)
from test_weather_map import START, payload

ROOT = Path(__file__).resolve().parents[1]
SECRET_USER, SECRET_PASSWORD = "qa-user-7f3a", "qa-pass-91bd"
CREDS = {"METEOMATICS_USER": SECRET_USER, "METEOMATICS_PASSWORD": SECRET_PASSWORD}
HOURLY_KEYS = {"valid_time_utc", *FIELDS.values(), "wind_speed_kmh", "wind_gust_kmh"}
DAILY_KEYS = {"period", "first_valid_time_utc", "last_valid_time_utc", "sample_count",
              "max_temperature_c", "min_humidity_pct", "max_wind_speed_kmh", "max_wind_gust_kmh",
              "rainfall_total_mm", "rainfall_hours_available", "hours_available"}
META_KEYS = {"provider", "retrieved_at_utc", "forecast_issued_at_utc", "cache_age_hours",
             "stale", "forecast_type", "limitation"}


def response(status, body=None, headers=None, bad_json=False):
    item = Mock(status_code=status, headers=headers or {})
    if bad_json:
        item.json.side_effect = requests.JSONDecodeError("bad", "doc", 0)
    else:
        item.json.return_value = body
    if status >= 400:
        item.raise_for_status.side_effect = requests.HTTPError(response=item)
    return item


def fetch_from_url(url):
    from pipeline.weather import parse_utc
    areas = {str(i): dict(zip(["lat", "lon"], map(float, p.split(","))))
             for i, p in enumerate(url.split("/")[-2].split("+"))}
    return payload(areas, parse_utc(url.split("/")[3].split("--")[0]))


@patch.dict(os.environ, CREDS)
class ProviderClientTests(unittest.TestCase):
    @patch("pipeline.weather.requests.get")
    def test_credentials_sent_via_basic_auth_not_url(self, get):
        get.return_value = response(200, {"status": "OK", "data": []})
        url = build_url(START, {"p": {"lat": 37.27, "lon": 9.87}})
        get_forecast(url)
        args, kwargs = get.call_args
        self.assertEqual(kwargs["auth"], (SECRET_USER, SECRET_PASSWORD))
        self.assertNotIn(SECRET_USER, args[0])
        self.assertNotIn(SECRET_PASSWORD, args[0])
        self.assertTrue(args[0].startswith("https://"))
        self.assertIsNotNone(kwargs.get("timeout"))

    def test_missing_credentials_fail_before_network(self):
        with patch.dict(os.environ, {"METEOMATICS_USER": "", "METEOMATICS_PASSWORD": ""}), \
                patch("pipeline.weather.requests.get") as get:
            with self.assertRaisesRegex(RuntimeError, "METEOMATICS_USER"):
                get_forecast("https://api.meteomatics.com/x")
            get.assert_not_called()

    @patch("pipeline.weather.time.sleep")
    @patch("pipeline.weather.requests.get")
    def test_http_errors_map_to_safe_messages(self, get, sleep):
        for status, hint in [(401, "username/password"), (403, "permissions"),
                             (400, "request parameters"), (404, "request parameters")]:
            get.reset_mock()
            get.return_value = response(status)
            with self.assertRaises(RuntimeError) as caught:
                get_forecast("https://api.meteomatics.com/x")
            self.assertIn(f"HTTP {status}", str(caught.exception))
            self.assertIn(hint, str(caught.exception))
            self.assertEqual(get.call_count, 1, "client errors must not be retried")

    @patch("pipeline.weather.time.sleep")
    @patch("pipeline.weather.requests.get")
    def test_server_error_retried_then_succeeds(self, get, sleep):
        get.side_effect = [response(503), response(200, {"status": "OK", "data": []})]
        self.assertEqual(get_forecast("https://api.meteomatics.com/x")["status"], "OK")
        sleep.assert_called_once_with(1.0)

    @patch("pipeline.weather.time.sleep")
    @patch("pipeline.weather.requests.get")
    def test_persistent_rate_limit_stops_after_three_attempts(self, get, sleep):
        get.return_value = response(429, headers={"Retry-After": "1"})
        with self.assertRaisesRegex(RuntimeError, "HTTP 429: Quota"):
            get_forecast("https://api.meteomatics.com/x")
        self.assertEqual(get.call_count, 3)

    @patch("pipeline.weather.requests.get")
    def test_invalid_json_and_non_ok_payloads_rejected(self, get):
        for item in [response(200, bad_json=True), response(200, {"status": "ERROR"}),
                     response(200, {"status": "OK", "data": None}), response(200, [])]:
            get.return_value = item
            with self.assertRaises(RuntimeError):
                get_forecast("https://api.meteomatics.com/x")

    @patch("pipeline.weather.time.sleep")
    @patch("pipeline.weather.requests.get")
    def test_secrets_absent_from_errors_and_logs(self, get, sleep):
        failures = [requests.ConnectionError(f"{SECRET_USER}:{SECRET_PASSWORD}"), response(401),
                    response(500), response(200, bad_json=True)]
        for failure in failures:
            get.reset_mock(side_effect=True)
            if isinstance(failure, Exception):
                get.side_effect = failure
            else:
                get.return_value = failure
            log, out = io.StringIO(), io.StringIO()
            handler = logging.StreamHandler(log)
            logging.getLogger().addHandler(handler)
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    with self.assertRaises(RuntimeError) as caught:
                        get_forecast("https://api.meteomatics.com/x")
            finally:
                logging.getLogger().removeHandler(handler)
            text = str(caught.exception) + repr(caught.exception) + log.getvalue() + out.getvalue()
            self.assertNotIn(SECRET_PASSWORD, text)
            self.assertNotIn(SECRET_USER, text)


class ProcessingTests(unittest.TestCase):
    AREA = {"bizerte": {"name": "Bizerte", "lat": 37.27, "lon": 9.87}}

    def varied_payload(self):
        """Distinct per-hour values so window aggregations are verifiable."""
        dates = [iso_utc(START + timedelta(hours=h)) for h in range(72)]
        series = {
            "t_2m:C": [10 + h * 0.25 for h in range(72)],
            "relative_humidity_2m:p": [90 - h for h in range(72)],
            "wind_speed_10m:ms": [(h % 24) / 2 for h in range(72)],
            "wind_gusts_10m_1h:ms": [(h % 24) for h in range(72)],
            "precip_1h:mm": [0.1 if h < 24 else 0.0 if h < 48 else 0.35 for h in range(72)],
        }
        return {"status": "OK", "dateGenerated": "2026-11-14T15:00:00Z", "data": [
            {"parameter": p, "coordinates": [{"lat": 37.27, "lon": 9.87, "dates": [
                {"date": d, "value": v} for d, v in zip(dates, vals)]}]} for p, vals in series.items()]}

    def test_hourly_timestamps_units_and_schema(self):
        out = build_output(self.varied_payload(), self.AREA, START, START)
        area = out["areas"]["bizerte"]
        self.assertEqual(out["units"], {"temperature": "C", "relative_humidity": "%",
                                        "wind": "km/h", "precipitation": "mm"})
        self.assertEqual(out["forecast_window_hours"], 72)
        self.assertEqual(out["valid_from_utc"], "2026-11-14T15:00:00Z")
        self.assertEqual(out["valid_until_utc"], "2026-11-17T14:00:00Z")
        times = [row["valid_time_utc"] for row in area["hourly"]]
        self.assertEqual(times, [iso_utc(START + timedelta(hours=h)) for h in range(72)])
        for row in area["hourly"]:
            self.assertEqual(set(row), HOURLY_KEYS)
            self.assertAlmostEqual(row["wind_speed_kmh"], round(row["wind_speed_ms"] * 3.6, 2))
            self.assertAlmostEqual(row["wind_gust_kmh"], round(row["wind_gust_ms"] * 3.6, 2))
        self.assertEqual(area["warnings"], [])

    def test_24_48_72_hour_aggregations(self):
        daily = build_output(self.varied_payload(), self.AREA, START, START)["areas"]["bizerte"]["daily"]
        self.assertEqual([d["period"] for d in daily], ["0-24h", "24-48h", "48-72h"])
        for d in daily:
            self.assertEqual(set(d), DAILY_KEYS)
            self.assertEqual(d["sample_count"], 24)
        self.assertEqual([d["max_temperature_c"] for d in daily], [15.75, 21.75, 27.75])
        self.assertEqual([d["min_humidity_pct"] for d in daily], [67, 43, 19])
        self.assertEqual([d["max_wind_speed_kmh"] for d in daily], [41.4] * 3)
        self.assertEqual([d["max_wind_gust_kmh"] for d in daily], [82.8] * 3)
        self.assertEqual([d["rainfall_total_mm"] for d in daily], [2.4, 0.0, 8.4])
        self.assertEqual(daily[1]["first_valid_time_utc"], "2026-11-15T15:00:00Z")
        self.assertEqual(daily[2]["last_valid_time_utc"], "2026-11-17T14:00:00Z")

    def test_offset_timestamps_and_out_of_window_values_ignored(self):
        raw = self.varied_payload()
        dates = raw["data"][0]["coordinates"][0]["dates"]
        dates[0]["date"] = "2026-11-14T16:00:00+01:00"  # same instant as START
        dates.append({"date": "2026-11-20T00:00:00Z", "value": 99})
        out = build_output(raw, self.AREA, START, START)["areas"]["bizerte"]
        self.assertEqual(out["hourly"][0]["temperature_c"], 10)
        self.assertEqual(len(out["hourly"]), 72)

    def test_missing_parameter_and_all_invalid_parameter_warn(self):
        raw = self.varied_payload()
        raw["data"] = [b for b in raw["data"] if b["parameter"] != "wind_gusts_10m_1h:ms"]
        for point in raw["data"][1]["coordinates"][0]["dates"]:
            point["value"] = -999
        area = build_output(raw, self.AREA, START, START)["areas"]["bizerte"]
        self.assertIn("Missing parameter: wind_gusts_10m_1h:ms", area["warnings"])
        self.assertIn("No valid values for parameter: relative_humidity_2m:p", area["warnings"])
        self.assertTrue(all(r["wind_gust_kmh"] is None and r["relative_humidity_pct"] is None
                            for r in area["hourly"]))
        self.assertIsNone(area["daily"][0]["min_humidity_pct"])
        self.assertEqual(area["daily"][0]["hours_available"]["relative_humidity_pct"], 0)

    def test_shuffled_and_unrelated_coordinates(self):
        areas = {"a": {"lat": 36.25, "lon": 9.25}, "b": {"lat": 37.25, "lon": 10.25}}
        raw = payload(areas)
        for block in raw["data"]:
            block["coordinates"].reverse()
            block["coordinates"].append({"lat": 0.0, "lon": 0.0, "dates": block["coordinates"][0]["dates"]})
        raw["data"][0]["coordinates"][0]["dates"][0]["value"] = 31.5  # belongs to "b"
        out = build_output(raw, areas, START)["areas"]
        self.assertEqual(out["b"]["hourly"][0]["temperature_c"], 31.5)
        self.assertEqual(out["a"]["hourly"][0]["temperature_c"], 22)

    def test_url_batches_never_exceed_eight_points(self):
        grid = make_grid()
        urls = []
        with tempfile.TemporaryDirectory() as directory:
            refresh(grid, Path(directory) / "g.json", now=START,
                    fetch=lambda url: urls.append(url) or fetch_from_url(url))
        self.assertEqual(len(urls), 5)
        points = [p for url in urls for p in url.split("/")[-2].split("+")]
        self.assertEqual(len(points), 35)
        self.assertEqual(len(set(points)), 35)
        self.assertTrue(all("/" + ",".join(FIELDS) + "/" in url for url in urls))


class CacheFreshnessTests(unittest.TestCase):
    def test_refresh_reuses_fresh_and_refetches_stale_future_or_changed_grid(self):
        grid = make_grid()
        calls = []
        def fetch(url):
            calls.append(url)
            return fetch_from_url(url)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "g.json"
            refresh(grid, path, now=START, fetch=fetch)
            self.assertEqual(len(calls), 5)
            refresh(grid, path, now=START + timedelta(hours=2, minutes=59), fetch=fetch)
            self.assertEqual(len(calls), 5, "fresh snapshot must not hit provider")
            refresh(grid, path, now=START + timedelta(hours=3), fetch=fetch)
            self.assertEqual(len(calls), 10, "3h-old snapshot must refresh")
            snapshot = json.loads(path.read_text())
            snapshot["retrieved_at_utc"] = iso_utc(START + timedelta(days=1))
            path.write_text(json.dumps(snapshot))
            refresh(grid, path, now=START + timedelta(hours=3), fetch=fetch)
            self.assertEqual(len(calls), 10, "same-hour batch cache reused for future-dated snapshot")
            grid["features"] = grid["features"][:8]
            refresh(grid, path, now=START + timedelta(hours=3), fetch=fetch)
            self.assertEqual(len(json.loads(path.read_text())["areas"]), 8)


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.patches = [patch.object(routes, "GRID_PATH", self.root / "weather_grid.json"),
                        patch.object(routes, "DATA_PATH", self.root / "weather.json"),
                        patch.dict(os.environ, CREDS)]
        for item in self.patches:
            item.start()
        self.now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        refresh(make_grid(), routes.GRID_PATH, now=self.now, fetch=fetch_from_url)
        areas = {"bizerte": {"name": "Bizerte", "lat": 37.27, "lon": 9.87}}
        routes.DATA_PATH.write_text(json.dumps(build_output(payload(areas, self.now), areas, self.now, self.now)))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    async def test_weather_area_contract(self):
        result = await self.client.get("/weather?area=bizerte")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["content-type"], "application/json")
        body = result.json()
        self.assertTrue(META_KEYS <= set(body))
        self.assertEqual((body["area"], body["provider"], body["stale"]), ("bizerte", "meteomatics", False))
        self.assertEqual((len(body["hourly"]), len(body["daily"])), (72, 3))
        self.assertEqual((await self.client.get("/weather?area=BIZERTE")).status_code, 404)
        self.assertEqual((await self.client.get("/weather?area=")).status_code, 404)

    async def test_stale_cache_flagged(self):
        snapshot = json.loads(routes.DATA_PATH.read_text())
        snapshot["retrieved_at_utc"] = iso_utc(self.now - timedelta(hours=4))
        routes.DATA_PATH.write_text(json.dumps(snapshot))
        body = (await self.client.get("/weather?area=bizerte")).json()
        self.assertTrue(body["stale"])
        self.assertGreaterEqual(body["cache_age_hours"], 4)
        snapshot["retrieved_at_utc"] = iso_utc(self.now + timedelta(hours=2))
        routes.DATA_PATH.write_text(json.dumps(snapshot))
        self.assertTrue((await self.client.get("/weather?area=bizerte")).json()["stale"])

    async def test_map_variable_and_period(self):
        result = await self.client.get("/weather/map?variable=humidity&period=0")
        self.assertEqual(result.status_code, 200)
        body = result.json()
        self.assertEqual((body["type"], body["variable"], body["period"], body["unit"]),
                         ("FeatureCollection", "humidity", 0, "%"))
        self.assertEqual(len(body["features"]), 35)
        for feature in body["features"]:
            props = feature["properties"]
            self.assertEqual(props["period"], "0-24h")
            self.assertEqual(props["value"], props["min_humidity_pct"])
            self.assertEqual(feature["geometry"]["type"], "Polygon")
        for period, label in [(1, "24-48h"), (2, "48-72h")]:
            body = (await self.client.get(f"/weather/map?variable=rainfall&period={period}")).json()
            self.assertEqual(body["features"][0]["properties"]["period"], label)
            self.assertEqual(body["features"][0]["properties"]["value"], 24)
        for variable, field in routes.MAP_VARIABLES.items():
            props = (await self.client.get(f"/weather/map?variable={variable}")).json()["features"][0]["properties"]
            self.assertEqual(props["value"], props[field[0]])
        self.assertEqual((await self.client.get("/weather/map?window=2")).json()["period"], 2)
        self.assertNotIn("variable", (await self.client.get("/weather/map")).json())

    async def test_map_invalid_parameters(self):
        for query in ["period=3", "period=-1", "period=x", "period=1.5", "window=3",
                      "variable=pressure", "variable=", "period=1&window=2"]:
            result = await self.client.get(f"/weather/map?{query}")
            self.assertEqual(result.status_code, 422, query)
        self.assertEqual((await self.client.get("/weather/map?period=1&window=1")).status_code, 200)

    async def test_grid_meta_geography(self):
        body = (await self.client.get("/weather/grid-meta")).json()
        self.assertEqual((body["cell_count"], body["crs"], body["resolution_degrees"]), (35, "EPSG:4326", .5))
        self.assertEqual(body["bbox"], [8.0, 35.0, 11.5, 37.5])
        ids = [f["properties"]["grid_id"] for f in body["grid"]["features"]]
        self.assertEqual(len(ids), len(set(ids)))
        for feature in body["grid"]["features"]:
            ring = feature["geometry"]["coordinates"][0]
            self.assertEqual(ring[0], ring[-1])
            lons, lats = [p[0] for p in ring], [p[1] for p in ring]
            props = feature["properties"]
            self.assertAlmostEqual(props["lon"], (min(lons) + max(lons)) / 2)
            self.assertAlmostEqual(props["lat"], (min(lats) + max(lats)) / 2)
            self.assertEqual(props["grid_id"], f"tn_{min(lats):.1f}_{min(lons):.1f}")
        self.assertEqual(locate_cell(body["grid"], 37.27, 9.87)["properties"]["grid_id"], "tn_37.0_9.5")

    async def test_cell_matches_grid_geometry(self):
        meta = (await self.client.get("/weather/grid-meta")).json()
        for feature in meta["grid"]["features"]:
            grid_id = feature["properties"]["grid_id"]
            result = await self.client.get(f"/weather/cell/{grid_id}")
            self.assertEqual(result.status_code, 200, grid_id)
            body = result.json()
            self.assertEqual(body["grid_id"], grid_id)
            self.assertEqual((body["lat"], body["lon"]), (feature["properties"]["lat"], feature["properties"]["lon"]))
            self.assertTrue(META_KEYS <= set(body))
        for bad in ["unknown", "tn_37.0_9.6", "%00", "tn_37.0_9.5%20"]:
            self.assertEqual((await self.client.get(f"/weather/cell/{bad}")).status_code, 404, bad)

    async def test_point_lookup_by_coordinates(self):
        for lat, lon, expected in [(37.27, 9.87, "tn_37.0_9.5"), (36.8, 10.18, "tn_36.5_10.0"),
                                   (36.5, 9.5, "tn_36.5_9.5"), (35.0, 8.0, "tn_35.0_8.0")]:
            result = await self.client.get(f"/weather/point?lat={lat}&lon={lon}")
            self.assertEqual(result.status_code, 200, (lat, lon))
            body = result.json()
            self.assertEqual(body["grid_id"], expected)
            self.assertEqual(body["query"], {"lat": lat, "lon": lon})
            cell = (await self.client.get(f"/weather/cell/{expected}")).json()
            self.assertEqual((body["lat"], body["lon"], body["hourly"]), (cell["lat"], cell["lon"], cell["hourly"]))
            self.assertTrue(META_KEYS <= set(body))
        for query in ["lat=37.5&lon=9.0", "lat=36&lon=11.5", "lat=0&lon=0", "lat=34.99&lon=9"]:
            self.assertEqual((await self.client.get(f"/weather/point?{query}")).status_code, 404, query)
        for query in ["lat=91&lon=9", "lat=36&lon=-181", "lat=abc&lon=9", "lat=36", "lon=9", "",
                      "lat=nan&lon=9", "lat=36&lon=inf"]:
            self.assertEqual((await self.client.get(f"/weather/point?{query}")).status_code, 422, query)

    async def test_endpoints_503_without_cache(self):
        routes.GRID_PATH.unlink()
        routes.DATA_PATH.unlink()
        for path in ["/weather?area=bizerte", "/weather/map?variable=humidity&period=0",
                     "/weather/cell/tn_37.0_9.5", "/weather/point?lat=37.27&lon=9.87"]:
            result = await self.client.get(path)
            self.assertEqual(result.status_code, 503, path)
            self.assertIn("refresh", result.json()["detail"])
        self.assertEqual((await self.client.get("/weather/grid-meta")).status_code, 200)

    async def test_credentials_never_in_responses(self):
        paths = ["/weather?area=bizerte", "/weather/map?variable=humidity&period=0",
                 "/weather/grid-meta", "/weather/cell/tn_37.0_9.5", "/weather/cell/x",
                 "/weather/point?lat=37.27&lon=9.87",
                 "/weather/map?period=9", "/openapi.json", "/docs"]
        for path in paths:
            result = await self.client.get(path)
            blob = result.text + json.dumps(dict(result.headers))
            self.assertNotIn(SECRET_PASSWORD, blob, path)
            self.assertNotIn(SECRET_USER, blob, path)
            self.assertNotIn("METEOMATICS_", blob, path)
        for path in ["/.env", "/%2Eenv", "/api/../.env", "/.git/config", "/.venv/pyvenv.cfg"]:
            self.assertEqual((await self.client.get(path)).status_code, 404, path)


@unittest.skipUnless((ROOT / "data/weather.json").exists(), "no real point cache")
class RealPointCacheTests(unittest.IsolatedAsyncioTestCase):
    """Validates the real Meteomatics snapshot already on disk; no network."""

    async def test_real_bizerte_values_plausible(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            result = await client.get("/weather?area=bizerte")
        self.assertEqual(result.status_code, 200)
        body = result.json()
        self.assertAlmostEqual(body["lat"], 37.27)
        self.assertAlmostEqual(body["lon"], 9.87)
        self.assertEqual(len(body["hourly"]), 72)
        valid = [r for r in body["hourly"] if r["temperature_c"] is not None]
        self.assertGreater(len(valid), 60, "real forecast should be mostly populated")
        for row in valid:
            self.assertTrue(-20 < row["temperature_c"] < 55)
            self.assertTrue(0 <= row["relative_humidity_pct"] <= 100)
            self.assertTrue(0 <= row["wind_speed_ms"] < 60)
            self.assertGreaterEqual(row["wind_gust_ms"], row["wind_speed_ms"] - 0.5)
            self.assertGreaterEqual(row["precip_1h_mm"], 0)
        times = [datetime.fromisoformat(r["valid_time_utc"].replace("Z", "+00:00")) for r in body["hourly"]]
        self.assertTrue(all(b - a == timedelta(hours=1) for a, b in zip(times, times[1:])))


if __name__ == "__main__":
    unittest.main()
