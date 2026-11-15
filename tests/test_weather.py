import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import passport as pp
from api import weather as wx
from tests.test_passport import AREA, make_calendar

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "open_meteo_sample.json").read_text())


@pytest.fixture
def client(monkeypatch, tmp_path):
    areas = tmp_path / "areas.json"
    areas.write_text(json.dumps([AREA]))
    monkeypatch.setattr(pp, "AREAS_PATH", areas)
    monkeypatch.setattr(pp, "calendar_for_bbox", lambda bbox, s, e: make_calendar())
    monkeypatch.setattr(wx, "_cache", {})
    app = FastAPI()
    app.include_router(wx.router)
    return TestClient(app)


def test_summarise_fixture():
    days = wx.summarise_daily(FIXTURE)
    assert [d["date"] for d in days] == ["2026-11-14", "2026-11-15", "2026-11-16"]
    assert days[0] == {"date": "2026-11-14", "temp_max_c": 21.0, "humidity_min_pct": 48,
                       "gust_max_kmh": 27.5, "rain_total_mm": 0.0}
    assert days[2]["rain_total_mm"] == 2.0
    assert days[2]["gust_max_kmh"] == 47.5


def test_weather_endpoint_and_cache(client, monkeypatch):
    calls = []

    def fake_fetch(lat, lon):
        calls.append((lat, lon))
        return FIXTURE
    monkeypatch.setattr(wx, "fetch_forecast", fake_fetch)

    r = client.get("/weather", params={"area": "cap_bon"})
    assert r.status_code == 200
    body = r.json()
    assert calls == [(36.7, 10.8)]  # bbox centre
    assert body["label"] == wx.LABEL
    assert body["source"] == "Open-Meteo forecast API"
    assert body["forecast_issue_time"] is None and "issue time" in body["forecast_issue_time_note"]
    assert body["fetched_at"].endswith("+00:00")
    assert len(body["days"]) == 3
    hist = body["historical_context"]
    assert hist["available"] is True and "median_harmonized_same_month" in hist
    assert isinstance(hist["is_critical_month"], bool)

    client.get("/weather", params={"area": "cap_bon"})
    assert len(calls) == 1  # served from cache


def test_weather_503_on_network_failure(client, monkeypatch):
    def fail(lat, lon):
        raise httpx.ConnectError("no network")
    monkeypatch.setattr(wx, "fetch_forecast", fail)
    r = client.get("/weather", params={"area": "cap_bon"})
    assert r.status_code == 503
    assert "unavailable" in r.json()["detail"]


def test_weather_unknown_area(client):
    assert client.get("/weather", params={"area": "nope"}).status_code == 404


# ----------------------------------------------------------------------------- by point (lat + lon)

def test_weather_by_point_inside_named_area(client, monkeypatch):
    calls = []

    def fake_fetch(lat, lon):
        calls.append((lat, lon))
        return FIXTURE
    monkeypatch.setattr(wx, "fetch_forecast", fake_fetch)

    r = client.get("/weather", params={"lat": 36.7012, "lon": 10.8049})
    assert r.status_code == 200
    body = r.json()
    assert calls == [(36.7, 10.8)]  # rounded to ~1 km, so nearby lookups share the cache
    assert body["scope"] == "point"
    assert body["point"] == {"latitude": 36.7, "longitude": 10.8}
    assert body["area"]["id"] == "cap_bon"  # the point sits inside the test area's bbox
    assert len(body["days"]) == 3
    assert body["historical_context"]["available"] is True

    client.get("/weather", params={"lat": 36.699, "lon": 10.801})
    assert len(calls) == 1  # same rounded point: served from cache


def test_weather_by_point_outside_every_area(client, monkeypatch):
    monkeypatch.setattr(wx, "fetch_forecast", lambda lat, lon: FIXTURE)
    r = client.get("/weather", params={"lat": 36.0, "lon": 2.0})
    assert r.status_code == 200
    assert r.json()["area"] is None  # no named area here; the forecast still works


def test_weather_by_point_validation(client):
    assert client.get("/weather").status_code == 400                                    # neither
    assert client.get("/weather", params={"area": "cap_bon", "lat": 1, "lon": 1}).status_code == 400  # both
    assert client.get("/weather", params={"lat": 36.7}).status_code == 400              # lon missing
    assert client.get("/weather", params={"lat": 91, "lon": 10}).status_code == 422     # out of range
    assert client.get("/weather", params={"lat": 36.7, "lon": 181}).status_code == 422
