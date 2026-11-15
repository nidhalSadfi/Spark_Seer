import json
from datetime import timedelta
from pathlib import Path

import pytest

from firms_tool.area_api import bbox_tiles
from settings import DEFAULT_REGION_BBOX, Settings, SettingsError

ENV_VARS = ("DATABASE_URL", "FIRMS_MAP_KEY", "FIRMS_MAP_KEY_1", "FIRMS_POLL_INTERVAL_HOURS", "FIRMS_BBOX",
            "FIRMS_MAX_BACKFILL_DAYS", "FIRMS_RAW_DIR", "WORKER_LEASE_SECONDS", "WORKER_TICK_SECONDS",
            "RECONCILE_DAY_OF_MONTH", "COUNTRY_BOUNDARY_FILE", "STATIC_MASK_FILE", "DRIFT_WARN_PCT",
            "DRIFT_MIN_COUNT", "OPENROUTER_API_KEY", "OPENROUTER_MODELS", "OPENROUTER_BASE_URL",
            "OPENROUTER_TIMEOUT_S", "OPENROUTER_RETRIES", "OPENROUTER_QUOTA_CHECK", "REVIEW_DEADLINE_S",
            "REVIEW_SUMMARY_MAX_CHARS", "PROCESSING_MIN_LAT")
FAKE_OPENROUTER_KEY = "sk-or-v1-0000000000000000000000000000000000000000000000000000000000fake"


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
    assert (s.country_boundary_file, s.static_mask_file) == (None, None)
    assert (s.drift_warn_pct, s.drift_min_count) == (40.0, 50.0)


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
    areas = [a for a in areas if a["group"] == "north_africa"]  # the world regions need FIRMS_BBOX=world
    assert len(areas) == 4 and all(w <= a["bbox"][0] and s <= a["bbox"][1] and e >= a["bbox"][2]
                                   and n >= a["bbox"][3] for a in areas)


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
    monkeypatch.setenv("DRIFT_MIN_COUNT", "0")
    s = Settings.from_env()
    assert s.database_url.endswith("/db") and s.firms_map_key == "primary"
    assert s.firms_poll_interval == timedelta(minutes=30)
    assert s.firms_bbox == (8.0, 35.5, 11.0, 37.5)
    assert (s.firms_max_backfill_days, s.firms_raw_dir) == (7, Path("/var/firms"))
    assert (s.worker_lease_seconds, s.worker_tick_seconds, s.reconcile_day_of_month) == (120, 15, 28)
    assert s.country_boundary_file == Path("data/boundary.geojson") and s.static_mask_file == Path("data/mask.json")
    assert s.drift_warn_pct == 25.5 and s.drift_min_count == 0


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
    ("DRIFT_MIN_COUNT", "-1"),
    ("DRIFT_MIN_COUNT", "fifty"),
])
def test_invalid_values_name_the_variable(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(SettingsError, match=name):
        Settings.from_env()


def test_review_defaults():
    s = Settings.from_env()
    # No model list by default (spec D5): the AI review is disabled and crewai never imported.
    assert s.openrouter_api_key is None and s.openrouter_models == ()
    assert s.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert (s.openrouter_timeout_s, s.openrouter_retries) == (60.0, 1)
    assert (s.review_deadline_s, s.review_summary_max_chars) == (300.0, 8000)
    assert s.openrouter_quota_check is True   # free tier by default (spec §7)


@pytest.mark.parametrize("raw,value", [("true", True), ("false", False), (" FALSE ", False), ("True", True)])
def test_quota_check_parses_true_or_false(monkeypatch, raw, value):
    monkeypatch.setenv("OPENROUTER_QUOTA_CHECK", raw)
    assert Settings.from_env().openrouter_quota_check is value


@pytest.mark.parametrize("raw", ["yes", "no", "1", "0", "on", "off", "flase"])
def test_quota_check_rejects_anything_else(monkeypatch, raw):
    monkeypatch.setenv("OPENROUTER_QUOTA_CHECK", raw)
    with pytest.raises(SettingsError, match="OPENROUTER_QUOTA_CHECK must be true or false"):
        Settings.from_env()
    with pytest.raises(SettingsError, match="OPENROUTER_QUOTA_CHECK"):
        Settings(openrouter_quota_check="false")


def test_review_env_overrides(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER_KEY)
    monkeypatch.setenv("OPENROUTER_MODELS", " vendor/a , ,vendor/b:free,, ")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://localhost:8080/v1")
    monkeypatch.setenv("OPENROUTER_TIMEOUT_S", "12.5")
    monkeypatch.setenv("OPENROUTER_RETRIES", "0")
    monkeypatch.setenv("REVIEW_DEADLINE_S", "90")
    monkeypatch.setenv("REVIEW_SUMMARY_MAX_CHARS", "1000")
    s = Settings.from_env()
    assert s.openrouter_models == ("vendor/a", "vendor/b:free")   # order kept, blanks dropped
    assert s.openrouter_api_key == FAKE_OPENROUTER_KEY and s.openrouter_base_url == "http://localhost:8080/v1"
    assert (s.openrouter_timeout_s, s.openrouter_retries, s.review_deadline_s, s.review_summary_max_chars) == \
        (12.5, 0, 90.0, 1000)


def test_only_commas_means_no_models(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODELS", " , ,")
    assert Settings.from_env().openrouter_models == ()


def test_openrouter_key_not_in_repr(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_OPENROUTER_KEY)
    monkeypatch.setenv("OPENROUTER_MODELS", "vendor/a")
    assert FAKE_OPENROUTER_KEY not in repr(Settings.from_env())


@pytest.mark.parametrize("name,value", [
    ("OPENROUTER_TIMEOUT_S", "0"),
    ("OPENROUTER_TIMEOUT_S", "inf"),
    ("OPENROUTER_TIMEOUT_S", "soon"),
    ("OPENROUTER_RETRIES", "-1"),
    ("OPENROUTER_RETRIES", "1.5"),
    ("OPENROUTER_BASE_URL", "openrouter.ai/api/v1"),
    ("REVIEW_DEADLINE_S", "0"),
    ("REVIEW_DEADLINE_S", "nan"),
    ("REVIEW_DEADLINE_S", "-5"),
    ("REVIEW_SUMMARY_MAX_CHARS", "999"),
    ("REVIEW_SUMMARY_MAX_CHARS", "8k"),
])
def test_invalid_review_values_name_the_variable(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(SettingsError, match=name):
        Settings.from_env()


def test_models_without_a_key_are_a_clear_error(monkeypatch):
    # Otherwise every attempt would fail and every run would publish unreviewed (D3).
    monkeypatch.setenv("OPENROUTER_MODELS", "vendor/a")
    with pytest.raises(SettingsError, match="OPENROUTER_API_KEY"):
        Settings.from_env()


# -- world coverage (spec §16) -------------------------------------------------------------------

def test_firms_bbox_world(monkeypatch):
    from firms_tool.area_api import Window, call_cost
    from datetime import date

    monkeypatch.setenv("FIRMS_BBOX", " World ")
    s = Settings.from_env()
    assert s.firms_bbox == "world" and s.firms_bbox_arg == "world" and s.firms_is_world
    assert s.firms_bbox_extent == (-180.0, -90.0, 180.0, 90.0)
    # 6 x 3 tiles of 60 degrees: 36 transactions per day, ~290 for a 2-day poll of 4 sources.
    assert bbox_tiles(s.firms_bbox_arg) == 18
    assert 4 * call_cost(Window("MODIS_NRT", date(2026, 11, 14), 2), s.firms_bbox_arg) == 288
    assert not Settings().firms_is_world and Settings().firms_bbox_extent == DEFAULT_REGION_BBOX
    with pytest.raises(SettingsError, match="FIRMS_BBOX"):
        Settings(firms_bbox="worlds")
    monkeypatch.setenv("FIRMS_BBOX", "earth")
    with pytest.raises(SettingsError, match="FIRMS_BBOX must be 'world' or four numbers"):
        Settings.from_env()


def test_processing_min_lat(monkeypatch):
    assert Settings.from_env().processing_min_lat == 34.0  # the North Africa default is unchanged
    monkeypatch.setenv("PROCESSING_MIN_LAT", "None")
    assert Settings.from_env().processing_min_lat is None
    monkeypatch.setenv("PROCESSING_MIN_LAT", "-10.5")
    assert Settings.from_env().processing_min_lat == -10.5
    monkeypatch.setenv("PROCESSING_MIN_LAT", "north")
    with pytest.raises(SettingsError, match="PROCESSING_MIN_LAT"):
        Settings.from_env()
    with pytest.raises(SettingsError, match="PROCESSING_MIN_LAT"):
        Settings(processing_min_lat=95.0)
