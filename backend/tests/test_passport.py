import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import passport as pp

VALIDATION = {
    "error_pct_uncorrected": 41.2,
    "error_pct_global_ratio": 18.7,
    "error_pct_cell_month_ratio": 11.3,
    "k_global": 2.35,
    "train_years": [2012, 2018],
    "test_years": [2019, 2020],
}


def make_calendar(target_flag="high"):
    months = []
    for year in range(2001, 2026):
        for month in range(1, 13):
            harm = float(year - 2000) if month == 8 else 1.0
            months.append({
                "year": year, "month": month,
                "modis_raw": 4 if year < 2012 else 5,
                "viirs_raw": None if year < 2012 else 12,
                "harmonized": harm, "support": 30, "z": 0.1, "flag": "normal",
            })
    # target rows
    for m in months:
        if m["month"] == 8 and m["year"] in (2007, 2021):
            m.update(harmonized=123.456, z=2.345, flag=target_flag, low=100.0, high=150.0)
    return {"months": months, "meta": {"cells": 42, "critical_months": [7, 8, 9]}}


AREA = {"id": "cap_bon", "name": "Cap Bon", "bbox": [10.5, 36.3, 11.1, 37.1]}


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def boom(*a, **k):
        raise AssertionError("network call in tests")
    monkeypatch.setattr(pp, "_call_llm", boom)


@pytest.fixture
def client(monkeypatch, tmp_path):
    areas = tmp_path / "areas.json"
    areas.write_text(json.dumps([AREA]))
    monkeypatch.setattr(pp, "AREAS_PATH", areas)
    monkeypatch.setattr(pp, "calendar_for_bbox", lambda bbox, s, e: make_calendar())
    monkeypatch.setattr(pp, "get_validation", lambda: VALIDATION)
    app = FastAPI()
    app.include_router(pp.router)
    return TestClient(app)


def test_passport_post2012():
    p = pp.build_passport(AREA, 2021, 8, make_calendar(), VALIDATION)
    assert p["observations"]["source"] == "VIIRS"
    assert p["observations"]["harmonized"] == 123.5
    assert p["observations"]["low"] == 100.0 and p["observations"]["high"] == 150.0
    assert p["seasonal"]["median_same_month"] == 14.0  # median of 1..25 with 7 and 21 replaced by 123.456
    assert p["seasonal"]["z"] == 2.35
    assert p["seasonal"]["critical_months"] == [7, 8, 9]
    assert p["data_support"] == {"support": 30, "cells": 42}
    assert p["model_quality"] == VALIDATION
    assert len(p["sources"]) == 2
    assert not any("lower bound" in w for w in p["quality_warnings"])
    assert any("34°N" in w for w in p["quality_warnings"])
    assert p["ai_text"] is None and p["ai_checked"] is False
    assert "123.5" in p["summary_template"]


def test_passport_pre2012_lower_bound():
    p = pp.build_passport(AREA, 2007, 8, make_calendar(), VALIDATION)
    assert p["observations"]["source"] == "MODIS*k"
    assert p["observations"]["k_global"] == 2.35
    assert any("lower bound" in w for w in p["quality_warnings"])
    assert "lower bound" in p["summary_template"]


def test_low_confidence_wording():
    p = pp.build_passport(AREA, 2021, 8, make_calendar("low_confidence"), VALIDATION)
    assert "Not enough data to interpret" in p["quality_warnings"][0]
    assert p["confidence_statement"].startswith("Not enough data to interpret")
    assert "Not enough data to interpret" in p["summary_template"]


def test_number_check_rejects_invented_and_accepts_rounded():
    p = pp.build_passport(AREA, 2021, 8, make_calendar(), VALIDATION)
    ok, _ = pp.check_numbers("August 2021 had 124 detections, around 123.5, z 2.4.", p)
    assert ok
    ok, bad = pp.check_numbers("August 2021 had 777 detections.", p)
    assert not ok and bad == [777.0]


def test_ai_accepted_when_numbers_match(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(pp, "_call_llm", lambda s, u: "Cap Bon had 123.5 hotspot detections in August 2021.")
    p = pp.add_ai_wording(pp.build_passport(AREA, 2021, 8, make_calendar(), VALIDATION))
    assert p["ai_checked"] is True and p["ai_text"].startswith("Cap Bon")


def test_ai_rejected_on_invented_number(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(pp, "_call_llm", lambda s, u: "There is a 64% chance of fire.")
    p = pp.add_ai_wording(pp.build_passport(AREA, 2021, 8, make_calendar(), VALIDATION))
    assert p["ai_text"] is None and p["ai_checked"] is False
    assert "64" in p["ai_reason"]


def test_ai_failure_or_timeout(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def fail(s, u):
        raise TimeoutError()
    monkeypatch.setattr(pp, "_call_llm", fail)
    p = pp.add_ai_wording(pp.build_passport(AREA, 2021, 8, make_calendar(), VALIDATION))
    assert p["ai_text"] is None and p["ai_checked"] is False
    assert "TimeoutError" in p["ai_reason"]


def test_endpoint_area_and_bbox(client):
    r = client.post("/investigate", json={"area": "cap_bon", "year": 2007, "month": 8})
    assert r.status_code == 200
    body = r.json()
    assert body["area"]["name"] == "Cap Bon"
    assert body["observations"]["source"] == "MODIS*k"
    assert body["ai_text"] is None

    r = client.post("/investigate", json={"bbox": [10, 36, 11, 37], "year": 2021, "month": 8})
    assert r.status_code == 200


def test_endpoint_errors(client):
    assert client.post("/investigate", json={"area": "nope", "year": 2021, "month": 8}).status_code == 404
    assert client.post("/investigate", json={"year": 2021, "month": 8}).status_code == 422
    assert client.post("/investigate", json={"area": "cap_bon", "year": 1999, "month": 8}).status_code == 422
    assert client.post("/investigate", json={"bbox": [11, 36, 10, 37], "year": 2021, "month": 8}).status_code == 422
