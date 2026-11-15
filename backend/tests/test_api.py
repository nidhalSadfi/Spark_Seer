import importlib
import sys

from fastapi.testclient import TestClient

import api.main
from api.main import app
from conftest import VALIDATION, YEARS, make_dataset

REQUIRED = {"year", "month", "modis_raw", "viirs_raw", "harmonized", "support", "z", "flag"}
FLAGS = {"high", "low", "normal", "low_confidence"}
BIZERTE = "8.3,36.0,9.8,37.2"


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["rows"] > 0
    assert body["data_file"].endswith("harmonized.parquet")


def test_missing_data_is_503(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBER_DATA", str(tmp_path / "nope.parquet"))
    c = TestClient(app)
    for path in ["/health", "/validation", "/calendar?area=bizerte"]:
        r = c.get(path)
        assert r.status_code == 503 and "nope" in r.json()["detail"], path


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
    assert [a["id"] for a in areas] == ["bizerte", "kabylie", "ne_algeria", "petite_kabylie"]
    for a in areas:
        x0, y0, x1, y1 = a["bbox"]
        assert a["name"] and x0 < x1 and y0 < y1


def test_calendar_by_area_matches_bbox(client):
    by_area = client.get("/calendar?area=bizerte&start=2008&end=2015").json()
    check_calendar(by_area, 2008, 2015)
    assert by_area == client.get(f"/calendar?bbox={BIZERTE}&start=2008&end=2015").json()
    assert client.get("/calendar?area=kabylie&start=2008&end=2015").json()["meta"]["cells"] == 1


def test_calendar_400s(client):
    for query in ["", "?area=atlantis", f"?bbox={BIZERTE}&area=bizerte", "?bbox=1,2,3", "?bbox=a,b,c,d",
                  "?bbox=170,-50,-170,-40", "?bbox=0,10,10,0", "?bbox=nan,0,1,1"]:  # across 180, swapped, NaN
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


def test_app_starts_without_teammate_routers(monkeypatch):
    monkeypatch.setitem(sys.modules, "api.passport", None)  # makes the import raise ImportError
    monkeypatch.setitem(sys.modules, "api.weather", None)
    try:
        paths = importlib.reload(api.main).app.openapi()["paths"]
        assert "/calendar" in paths and "/investigate" not in paths and "/weather" not in paths
    finally:
        monkeypatch.undo()
        importlib.reload(api.main)  # back to the app with the real routers


def test_cors_allows_the_frontend_methods_and_admin_header_from_any_origin(client):
    # The reports page sends PATCH/DELETE with X-Admin-Token (spec §10 amendment 2026-11-15).
    for method in ["GET", "POST", "PATCH", "DELETE"]:
        r = client.options("/reports/x", headers={"Origin": "https://example.org",
                                                  "Access-Control-Request-Method": method,
                                                  "Access-Control-Request-Headers": "x-admin-token,content-type"})
        assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*", method
        assert method in r.headers["access-control-allow-methods"]
        assert "x-admin-token" in r.headers["access-control-allow-headers"].lower()
    r = client.options("/calendar", headers={"Origin": "https://example.org",
                                             "Access-Control-Request-Method": "PUT"})
    assert r.status_code == 400


def test_optional_fields_passed_through_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBER_DATA", make_dataset(tmp_path, optional_cols=True))
    body = TestClient(app).get(f"/calendar?bbox={BIZERTE}&start=2008&end=2015").json()
    check_calendar(body, 2008, 2015)
    busy = [r for r in body["months"] if r["modis_raw"] or r["viirs_raw"]]
    assert all({"low", "high", "MODIS_days", "VIIRS_days"} <= r.keys() for r in body["months"])
    for r in busy:
        if r["year"] < 2012:
            assert abs(r["low"] - 0.8 * r["harmonized"]) < 0.2 and r["high"] >= r["harmonized"]
        else:
            assert r["low"] is None and r["high"] is None  # missing in the data: null, never 0
        assert isinstance(r["MODIS_days"], int)


def test_root_redirects_to_demo_page(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/web/demo/"
    page = client.get("/")
    assert page.status_code == 200 and "EmberLedger demo" in page.text
    for asset in ["app.js", "style.css"]:
        assert client.get(f"/web/demo/{asset}").status_code == 200, asset
    assert client.get("/docs").status_code == 200


def test_meteomatics_routes_mounted_under_prefix(client):
    paths = app.openapi()["paths"]
    assert {"/meteomatics/weather", "/meteomatics/weather/map", "/meteomatics/weather/grid-meta",
            "/meteomatics/weather/cell/{grid_id}", "/meteomatics/weather/point",
            "/meteomatics/fires/calendar"} <= paths.keys()
    assert client.get("/meteomatics/weather/grid-meta").json()["cell_count"] == 35  # needs no cache


def test_parquet_calendar_coverage_is_full_and_the_default_end_stays_2025(client, monkeypatch):
    """Without a database (the Render demo): every month is "full", as before `coverage` existed,
    and the default end is 2025 whatever the parquet's last year (the fixture's is 2015)."""
    from api import core
    from datetime import datetime, timezone

    monkeypatch.setattr(core, "_utcnow", lambda: datetime(2016, 1, 1, tzinfo=timezone.utc))
    health = client.get("/health").json()
    assert (health["data_source"], health["data_end_year"]) == ("parquet", 2025)
    body = client.get(f"/calendar?bbox={BIZERTE}").json()
    check_calendar(body, 2001, 2025)
    assert {r["coverage"] for r in body["months"]} == {"full"}  # even months after "now": the parquet's grid
    explicit = client.get(f"/calendar?bbox={BIZERTE}&start=2001&end=2025").json()
    assert body == explicit
    later = client.get(f"/calendar?bbox={BIZERTE}&start=2014&end=2027").json()
    check_calendar(later, 2014, 2027)
    assert {r["coverage"] for r in later["months"]} == {"full"}


def test_month_coverage():
    from api.core import month_coverage
    from datetime import datetime, timezone

    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    cov = {(2026, 10): "partial", (2026, 11): "partial", (2025, 12): "full"}
    years, months = [2025, 2026, 2026, 2026, 2026, 2027], [12, 1, 10, 11, 12, 1]
    assert month_coverage(years, months, cov, now) == ["full", "none", "partial", "partial", "none", "none"]
    assert month_coverage(years, months, None, now) == ["full"] * 6
    assert month_coverage(years, months, {(2026, 12): "full"}, now)[4] == "none"  # future: never data
