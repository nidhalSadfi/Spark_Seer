"""Teammate routers (api/passport.py, api/weather.py, meteomatics-weather/) against the REAL app.

Their own tests replace calendar_for_bbox with a stub; these check the real wiring, on fake data.
No network (Open-Meteo and Meteomatics are stubbed) and no API key (the AI wording stays off).
"""
import json
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import make_fake_data
from api import weather as wx
import api.main

OPEN_METEO = json.loads((Path(__file__).parent / "fixtures" / "open_meteo_sample.json").read_text())


@pytest.fixture
def client(tmp_path, monkeypatch):
    make_fake_data.main(tmp_path)
    monkeypatch.setenv("EMBER_DATA", str(tmp_path / "harmonized.parquet"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # never call the real API from tests
    monkeypatch.setattr(wx, "fetch_forecast", lambda lat, lon: OPEN_METEO)
    monkeypatch.setattr(wx, "_cache", {})
    return TestClient(api.main.app)  # at call time: test_api.py reloads api.main


def bizerte_months(client):
    return client.get("/calendar?area=bizerte").json()["months"]


def test_teammate_routes_are_mounted(client):
    paths = api.main.app.openapi()["paths"]
    assert "post" in paths["/investigate"] and "get" in paths["/weather"]


def test_investigate_matches_calendar(client):
    # passport.py passes CAL_START='2001-01' / CAL_END='2025-12'; core reads the year.
    r = client.post("/investigate", json={"area": "bizerte", "year": 2008, "month": 7})
    assert r.status_code == 200
    p = r.json()
    row = next(m for m in bizerte_months(client) if (m["year"], m["month"]) == (2008, 7))
    assert p["observations"]["harmonized"] == row["harmonized"]
    assert p["seasonal"]["flag"] == row["flag"] == "high"  # planted anomaly in the fake data
    assert p["model_quality"]["synthetic"] is True and p["ai_text"] is None


def test_weather_historical_context_uses_calendar(client):
    body = client.get("/weather?area=bizerte").json()
    hist = body["historical_context"]
    assert hist["available"] is True, hist
    same_month = [m["harmonized"] for m in bizerte_months(client) if m["month"] == hist["month"]]
    assert hist["median_harmonized_same_month"] == round(statistics.median(same_month), 1)


def fake_meteomatics_batch(url):
    """A Meteomatics-shaped answer for every location in the request URL (made-up values)."""
    start = datetime.fromisoformat(url.split("/")[3].split("--")[0].replace("Z", "+00:00"))
    values = {"t_2m:C": 31, "relative_humidity_2m:p": 25, "wind_speed_10m:ms": 6,
              "wind_gusts_10m_1h:ms": 12, "precip_1h:mm": 0}
    points = [tuple(map(float, p.split(","))) for p in url.split("/")[-2].split("+")]
    return {"status": "OK", "data": [{"parameter": param, "coordinates": [
        {"lat": lat, "lon": lon, "dates": [
            {"date": (start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ"), "value": v}
            for h in range(72)]} for lat, lon in points]} for param, v in values.items()]}


def test_meteomatics_map_serves_its_snapshot(client, tmp_path, monkeypatch):
    from pipeline.grid import make_grid          # meteomatics-weather/pipeline, put on the path
    from pipeline.weather_map import refresh     # by api.main.load_meteomatics()
    snapshot = tmp_path / "weather_grid.json"
    monkeypatch.setattr(api.main.meteomatics, "GRID_PATH", snapshot)
    assert client.get("/meteomatics/weather/map").status_code == 503  # no snapshot yet

    refresh(make_grid(), snapshot, now=datetime.now(timezone.utc), fetch=fake_meteomatics_batch)
    body = client.get("/meteomatics/weather/map?window=0&variable=temperature").json()
    assert len(body["features"]) == 35 and body["stale"] is False
    assert {f["properties"]["value"] for f in body["features"]} == {31}
    cell = client.get("/meteomatics/weather/point?lat=37.27&lon=9.87").json()
    assert cell["grid_id"] == "tn_37.0_9.5" and len(cell["hourly"]) == 72
    assert "days" in client.get("/weather?area=bizerte").json()  # /weather is still api/weather.py
