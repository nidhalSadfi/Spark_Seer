"""Teammate routers (api/passport.py, api/weather.py) against the REAL api/core.py, on fake data.

Their own tests replace calendar_for_bbox with a stub; these check the real wiring. No network
(Open-Meteo is stubbed) and no API key (the AI wording stays off).
"""
import json
import statistics
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import make_fake_data
from api import weather as wx
from api.main import app

CORE_INTERFACE_BUG = (
    "api/passport.py passes CAL_START='2001-01' / CAL_END='2025-12' to core.calendar_for_bbox, "
    "which takes int years (TypeError). Fix in passport.py: CAL_START = BASELINE_START_YEAR and "
    "CAL_END = BASELINE_END_YEAR. Then delete this xfail marker.")
OPEN_METEO = json.loads((Path(__file__).parent / "fixtures" / "open_meteo_sample.json").read_text())


@pytest.fixture
def client(tmp_path, monkeypatch):
    make_fake_data.main(tmp_path)
    monkeypatch.setenv("EMBER_DATA", str(tmp_path / "harmonized.parquet"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # never call the real API from tests
    monkeypatch.setattr(wx, "fetch_forecast", lambda lat, lon: OPEN_METEO)
    monkeypatch.setattr(wx, "_cache", {})
    return TestClient(app)


def bizerte_months(client):
    return client.get("/calendar?area=bizerte").json()["months"]


def test_teammate_routes_are_mounted(client):
    paths = app.openapi()["paths"]
    assert "post" in paths["/investigate"] and "get" in paths["/weather"]


@pytest.mark.xfail(strict=True, reason=CORE_INTERFACE_BUG)
def test_investigate_matches_calendar(client):
    r = client.post("/investigate", json={"area": "bizerte", "year": 2008, "month": 7})
    assert r.status_code == 200
    p = r.json()
    row = next(m for m in bizerte_months(client) if (m["year"], m["month"]) == (2008, 7))
    assert p["observations"]["harmonized"] == row["harmonized"]
    assert p["seasonal"]["flag"] == row["flag"] == "high"  # planted anomaly in the fake data
    assert p["model_quality"]["synthetic"] is True and p["ai_text"] is None


@pytest.mark.xfail(strict=True, reason=CORE_INTERFACE_BUG)
def test_weather_historical_context_uses_calendar(client):
    body = client.get("/weather?area=bizerte").json()
    hist = body["historical_context"]
    assert hist["available"] is True, hist
    same_month = [m["harmonized"] for m in bizerte_months(client) if m["month"] == hist["month"]]
    assert hist["median_harmonized_same_month"] == round(statistics.median(same_month), 1)
