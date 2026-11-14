import json
from datetime import timedelta
from pathlib import Path

import pytest

from firms_tool.area_api import bbox_tiles
from settings import DEFAULT_REGION_BBOX, Settings, SettingsError

ENV_VARS = ("DATABASE_URL", "FIRMS_MAP_KEY", "FIRMS_MAP_KEY_1", "FIRMS_POLL_INTERVAL_HOURS", "FIRMS_BBOX",
            "FIRMS_MAX_BACKFILL_DAYS", "FIRMS_RAW_DIR", "WORKER_LEASE_SECONDS", "WORKER_TICK_SECONDS",
            "RECONCILE_DAY_OF_MONTH", "COUNTRY_BOUNDARY_FILE", "STATIC_MASK_FILE", "DRIFT_WARN_PCT")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults():
    s = Settings.from_env()
    assert s.database_url is None and s.firms_map_key is None
    assert s.firms_poll_interval_hours == 3 and s.firms_poll_interval == timedelta(hours=3)
    assert s.firms_max_backfill_days == 30
    assert s.firms_raw_dir == Path("data/raw/firms")
    assert (s.worker_lease_seconds, s.worker_tick_seconds, s.reconcile_day_of_month) == (300, 60, 5)
    # Processing: no boundary or mask file by default (the rules are then reported as disabled).
    assert (s.country_boundary_file, s.static_mask_file, s.drift_warn_pct) == (None, None, 40.0)


def test_default_bbox_is_the_whole_region():
    # Spec §1: Tunisia + Algeria north of 34N, padded to whole 0.5-degree cells.
    assert Settings.from_env().firms_bbox == DEFAULT_REGION_BBOX == (-2.5, 34.0, 11.5, 38.0)
    assert Settings().firms_bbox_arg == "-2.5,34,11.5,38"
    assert all(v / 0.5 == int(v / 0.5) for v in DEFAULT_REGION_BBOX)


def test_default_bbox_costs_one_firms_tile():
    assert bbox_tiles(Settings().firms_bbox_arg) == 1


def test_default_bbox_contains_every_demo_area():
    w, s, e, n = DEFAULT_REGION_BBOX
    areas = json.loads((Path(__file__).resolve().parent.parent / "data" / "areas.json").read_text())
    assert areas and all(w <= a["bbox"][0] and s <= a["bbox"][1] and e >= a["bbox"][2] and n >= a["bbox"][3]
                         for a in areas)


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setenv("FIRMS_MAP_KEY", "fallback")
    monkeypatch.setenv("FIRMS_MAP_KEY_1", "primary")
    monkeypatch.setenv("FIRMS_POLL_INTERVAL_HOURS", "0.5")
    monkeypatch.setenv("FIRMS_BBOX", " 8, 35.5 ,11,37.5 ")
    monkeypatch.setenv("FIRMS_MAX_BACKFILL_DAYS", "7")
    monkeypatch.setenv("FIRMS_RAW_DIR", "/var/firms")
    monkeypatch.setenv("WORKER_LEASE_SECONDS", "120")
    monkeypatch.setenv("WORKER_TICK_SECONDS", "15")
    monkeypatch.setenv("RECONCILE_DAY_OF_MONTH", "28")
    monkeypatch.setenv("COUNTRY_BOUNDARY_FILE", "data/boundary.geojson")
    monkeypatch.setenv("STATIC_MASK_FILE", "data/mask.json")
    monkeypatch.setenv("DRIFT_WARN_PCT", "25.5")
    s = Settings.from_env()
    assert s.database_url.endswith("/db") and s.firms_map_key == "primary"
    assert s.firms_poll_interval == timedelta(minutes=30)
    assert s.firms_bbox == (8.0, 35.5, 11.0, 37.5)
    assert (s.firms_max_backfill_days, s.firms_raw_dir) == (7, Path("/var/firms"))
    assert (s.worker_lease_seconds, s.worker_tick_seconds, s.reconcile_day_of_month) == (120, 15, 28)
    assert s.country_boundary_file == Path("data/boundary.geojson") and s.static_mask_file == Path("data/mask.json")
    assert s.drift_warn_pct == 25.5


def test_empty_values_fall_back_to_defaults(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.setenv(name, "")
    assert Settings.from_env() == Settings()


def test_secrets_not_in_repr(monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", "SECRETKEY123")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:hunter2@h/db")
    text = repr(Settings.from_env())
    assert "SECRETKEY123" not in text and "hunter2" not in text


@pytest.mark.parametrize("name,value", [
    ("FIRMS_POLL_INTERVAL_HOURS", "0"),
    ("FIRMS_POLL_INTERVAL_HOURS", "-3"),
    ("FIRMS_POLL_INTERVAL_HOURS", "three"),
    ("FIRMS_POLL_INTERVAL_HOURS", "inf"),
    ("FIRMS_BBOX", "1,2,3"),
    ("FIRMS_BBOX", "10,36,8,37"),
    ("FIRMS_BBOX", "a,b,c,d"),
    ("FIRMS_BBOX", "0,0,200,10"),
    ("FIRMS_MAX_BACKFILL_DAYS", "0"),
    ("FIRMS_MAX_BACKFILL_DAYS", "2.5"),
    ("WORKER_LEASE_SECONDS", "0"),
    ("WORKER_TICK_SECONDS", "-1"),
    ("RECONCILE_DAY_OF_MONTH", "0"),
    ("RECONCILE_DAY_OF_MONTH", "29"),
    ("DRIFT_WARN_PCT", "0"),
    ("DRIFT_WARN_PCT", "forty"),
    ("DRIFT_WARN_PCT", "nan"),
])
def test_invalid_values_name_the_variable(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(SettingsError, match=name):
        Settings.from_env()
