import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from api.main import app
from api import weather_router_example as routes
from pipeline.grid import make_grid
from pipeline.weather_map import refresh
from pipeline.weather import build_output
from test_weather_map import START, payload


class PollingEventLoopPolicy(asyncio.DefaultEventLoopPolicy):
    """Keep worker completion observable where the sandbox restricts socket wakeups."""

    def new_event_loop(self):
        loop = super().new_event_loop()
        def tick():
            loop.call_later(.01, tick)
        loop.call_soon(tick)
        return loop


asyncio.set_event_loop_policy(PollingEventLoopPolicy())


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.patch = patch.object(routes, "GRID_PATH", self.root / "weather_grid.json")
        self.patch.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.patch.stop()
        self.directory.cleanup()

    async def test_missing_cache_and_invalid_window(self):
        self.assertEqual((await self.client.get("/weather/map")).status_code, 503)
        self.assertEqual((await self.client.get("/weather/map?window=3")).status_code, 422)
        self.assertEqual((await self.client.get("/weather/grid-meta")).json()["cell_count"], 35)

    async def test_map_cell_and_legacy_contract(self):
        def fetch(url):
            areas = {str(index): dict(zip(["lat", "lon"], map(float, point.split(","))))
                     for index, point in enumerate(url.split("/")[-2].split("+"))}
            return payload(areas)
        refresh(make_grid(), routes.GRID_PATH, now=START, fetch=fetch)
        result = await self.client.get("/weather/map?window=2")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["features"][0]["properties"]["period"], "48-72h")
        cell = (await self.client.get("/weather/cell/tn_37.0_9.5")).json()
        self.assertEqual(len(cell["hourly"]), 72)
        self.assertEqual(len(cell["daily"]), 3)
        self.assertEqual((await self.client.get("/weather/cell/unknown")).status_code, 404)
        with patch.object(routes, "DATA_PATH", self.root / "legacy.json"):
            areas = {"bizerte": {"lat": 37.27, "lon": 9.87}}
            (self.root / "legacy.json").write_text(json.dumps(build_output(payload(areas), areas, START)))
            self.assertEqual((await self.client.get("/weather")).json()["area"], "bizerte")
            self.assertEqual((await self.client.get("/weather?area=unknown")).status_code, 404)

    async def test_corrupt_cache_is_unavailable(self):
        routes.GRID_PATH.write_text("{")
        self.assertEqual((await self.client.get("/weather/map")).status_code, 503)
        for invalid in [{"areas": {}, "retrieved_at_utc": 123},
                        {"areas": {"point": {}}, "provider": "meteomatics",
                         "retrieved_at_utc": "2026-11-14T15:00:00Z"}]:
            routes.GRID_PATH.write_text(json.dumps(invalid))
            self.assertEqual((await self.client.get("/weather/cell/point")).status_code, 503)

    async def test_secrets_are_not_public_files(self):
        self.assertNotEqual((await self.client.get("/.env")).status_code, 200)

    async def test_fire_calendar_import_and_cell_filter(self):
        path = self.root / "history.csv"
        path.write_text("latitude,longitude,acq_date\n37.27,9.87,2024-07-01\n")
        with patch.dict("os.environ", {"NASA_FIRMS_CSV": str(path)}):
            result = await self.client.get("/fires/calendar?grid_id=tn_37.0_9.5&day=2024-07-01")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(len(result.json()["features"]), 1)
            self.assertEqual((await self.client.get("/fires/calendar?day=invalid")).status_code, 422)
            self.assertEqual((await self.client.get("/fires/calendar?grid_id=unknown")).status_code, 404)
