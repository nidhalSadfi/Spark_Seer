from conftest import YEARS

REQUIRED = {"year", "month", "modis_raw", "viirs_raw", "harmonized", "support", "z", "flag"}
FLAGS = {"high", "low", "normal", "low_confidence"}
BIZERTE = "9.0,36.8,10.3,37.4"


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


def test_calendar_empty_area(client):
    body = client.get("/calendar?bbox=20,10,21,11").json()
    assert body == {"months": [], "meta": {"cells": 0, "note": "no detections in this area"}}


def test_calendar_bad_bbox(client):
    assert client.get("/calendar?bbox=1,2,3").status_code == 400
