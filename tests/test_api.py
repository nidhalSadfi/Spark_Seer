import importlib
import sys
import types

from fastapi import APIRouter
from fastapi.testclient import TestClient

import api.main
from api.main import app
from conftest import VALIDATION, YEARS, make_dataset

REQUIRED = {"year", "month", "modis_raw", "viirs_raw", "harmonized", "support", "z", "flag"}
FLAGS = {"high", "low", "normal", "low_confidence"}
BIZERTE = "9.0,36.8,10.3,37.4"


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["rows"] > 0
    assert body["data_file"].endswith("harmonized.parquet")


def test_missing_data_is_503(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBER_DATA", str(tmp_path / "nope.parquet"))
    c = TestClient(app)
    for path in ["/health", "/validation", "/calendar?area=bizerte"]:
        assert c.get(path).status_code == 503, path


def check_calendar(body, start, end):
    months = body["months"]
    assert len(months) == 12 * (end - start + 1)
    for year in range(start, end + 1):
        assert [r["month"] for r in months if r["year"] == year] == list(range(1, 13))
    for r in months:
        assert REQUIRED <= r.keys()
        assert r["flag"] in FLAGS
    assert {"cells", "critical_months", "validation"} <= body["meta"].keys()


def test_calendar_by_bbox(client):
    start, end = YEARS.start, YEARS.stop - 1
    body = client.get(f"/calendar?bbox={BIZERTE}&start={start}&end={end}").json()
    check_calendar(body, start, end)
    assert body["meta"]["cells"] == 2
    flags = {r["flag"] for r in body["months"]}
    assert {"high", "low_confidence", "normal"} <= flags  # fixture has a spike and a sparse January
    assert not {"low", "high", "MODIS_days", "VIIRS_days"} & body["months"][0].keys()  # never invented


def test_areas(client):
    areas = client.get("/areas").json()["areas"]
    assert [a["id"] for a in areas] == ["bizerte", "nw_tunisia", "kabylie", "ne_algeria"]
    for a in areas:
        x0, y0, x1, y1 = a["bbox"]
        assert a["name"] and x0 < x1 and y0 < y1


def test_calendar_by_area_matches_bbox(client):
    by_area = client.get("/calendar?area=bizerte&start=2008&end=2015").json()
    check_calendar(by_area, 2008, 2015)
    assert by_area == client.get(f"/calendar?bbox={BIZERTE}&start=2008&end=2015").json()
    assert client.get("/calendar?area=kabylie&start=2008&end=2015").json()["meta"]["cells"] == 1


def test_calendar_400s(client):
    for query in ["", "?area=atlantis", f"?bbox={BIZERTE}&area=bizerte", "?bbox=1,2,3", "?bbox=a,b,c,d"]:
        assert client.get("/calendar" + query).status_code == 400, query


def test_validation_without_series(client):
    assert client.get("/validation").json() == VALIDATION


def test_validation_series_passthrough(tmp_path, monkeypatch):
    series = [{"year": 2020, "month": 8, "observed": 120, "predicted": 110}]
    monkeypatch.setenv("EMBER_DATA", make_dataset(tmp_path, series=series))
    c = TestClient(app)
    assert c.get("/validation").json() == dict(VALIDATION, series=series)
    # /calendar keeps its compact meta.validation
    assert c.get(f"/calendar?bbox={BIZERTE}").json()["meta"]["validation"] == VALIDATION


def test_calendar_empty_area(client):
    body = client.get("/calendar?bbox=20,10,21,11").json()
    assert body == {"months": [], "meta": {"cells": 0, "note": "no detections in this area"}}


def test_teammate_router_included_when_present(monkeypatch):
    router = APIRouter()
    router.get("/passport/ping")(lambda: {"ok": True})
    monkeypatch.setitem(sys.modules, "api.passport", types.SimpleNamespace(router=router))
    try:
        assert TestClient(importlib.reload(api.main).app).get("/passport/ping").json() == {"ok": True}
    finally:
        monkeypatch.delitem(sys.modules, "api.passport")
        importlib.reload(api.main)  # back to the app without the fake router


def test_cors_allows_get_and_post_from_any_origin(client):
    for method in ["GET", "POST"]:
        r = client.options("/calendar", headers={"Origin": "https://example.org",
                                                 "Access-Control-Request-Method": method})
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*"
    r = client.options("/calendar", headers={"Origin": "https://example.org",
                                             "Access-Control-Request-Method": "DELETE"})
    assert r.status_code == 400
