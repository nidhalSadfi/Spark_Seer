"""The API's database features without a database (spec §10): 503s, the parquet fallback, input
validation, the reviewer token, the cell_month -> calendar frame mapping and I1 in a fresh
interpreter. PostgreSQL behaviour is in test_api_pg.py. Data is SYNTHETIC (conftest)."""
import math
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import core, database
from api import reports as reports_api
from api.main import app
from settings import ApiSettings, SettingsError

BACKEND = Path(__file__).resolve().parent.parent
UNREACHABLE = "postgresql+psycopg://emberguard:s3cret@127.0.0.1:1/emberguard"  # nothing listens on port 1
DB_GETS = ["/processing/status", "/processing/jobs", "/processing/jobs/1", "/processing/datasets",
           "/fires/live", "/reports"]
REPORT_ID = "0b6c3f5e-8a7d-4a0e-9a59-7d3c2f1e0a11"
BIZERTE = "8.3,36.0,9.8,37.2"
# I1: modules the API process must never load (the worker, CrewAI and the LLM clients it uses).
FORBIDDEN = ("worker", "crewai", "crewai_core", "openai", "litellm")


def db_requests(c):
    yield from (c.get(path) for path in DB_GETS)
    yield c.post("/reports", json={"lat": 36.9, "lon": 9.1, "observed": "smoke", "note": "seen from the road"})
    yield c.patch(f"/reports/{REPORT_ID}", json={"status": "confirmed"})
    yield c.delete(f"/reports/{REPORT_ID}")


def test_without_database_url_new_endpoints_503_and_the_calendar_uses_the_parquet(client):
    for r in db_requests(client):
        assert r.status_code == 503, r.request.url
        assert "DATABASE_URL is not set" in r.json()["detail"]
    health = client.get("/health").json()
    assert health["status"] == "ok" and health["rows"] > 0
    assert (health["data_source"], health["dataset_version_id"]) == ("parquet", None)
    assert client.get(f"/calendar?bbox={BIZERTE}").status_code == 200


def test_unreachable_database_503s_without_leaking_the_password_and_the_calendar_falls_back(client, monkeypatch):
    expected = client.get(f"/calendar?bbox={BIZERTE}&start=2008&end=2015").json()  # parquet, no DATABASE_URL
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)
    for r in db_requests(client):
        assert r.status_code == 503, r.request.url
        detail = r.json()["detail"]
        assert detail.startswith("database unavailable at") and "s3cret" not in detail and "***" in detail
    assert client.get(f"/calendar?bbox={BIZERTE}&start=2008&end=2015").json() == expected
    assert client.get("/health").json()["data_source"] == "parquet"


def test_the_approved_version_is_checked_at_most_every_interval(client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)
    monkeypatch.setenv("API_VERSION_CHECK_S", "30")
    now = [1000.0]
    monkeypatch.setattr(core, "_clock", lambda: now[0])
    calls = []
    real_read = database.read
    monkeypatch.setattr(database, "read", lambda *a, **kw: calls.append(1) or real_read(*a, **kw))
    for _ in range(3):
        assert client.get(f"/calendar?bbox={BIZERTE}").status_code == 200
    assert len(calls) == 1  # the failed check is cached too: one connect timeout per interval
    now[0] += 29.9
    client.get(f"/calendar?bbox={BIZERTE}")
    assert len(calls) == 1
    now[0] += 0.2
    client.get(f"/calendar?bbox={BIZERTE}")
    assert len(calls) == 2


@pytest.mark.parametrize("query", ["hours=0", "hours=169", "hours=abc", "hours=2.5", "bbox=1,2,3", "bbox=a,b,c,d",
                                   "bbox=170,-50,-170,-40", "bbox=0,10,10,0", "bbox=nan,0,1,1",
                                   "source=VIIRS;DROP"])
def test_fires_live_rejects_bad_input_with_400_before_touching_the_database(client, query):
    r = client.get(f"/fires/live?{query}")
    assert r.status_code == 400, r.json()


def test_processing_jobs_rejects_bad_filters_and_pages(client):
    for query in ["status=done", "kind=DROP TABLE", "limit=0", "limit=201", "offset=-1"]:
        assert client.get(f"/processing/jobs?{query}").status_code == 400, query
    assert client.get("/processing/datasets?limit=0").status_code == 400


@pytest.mark.parametrize("body", [
    {"lat": 91, "lon": 9, "observed": "smoke"},
    {"lat": 36, "lon": -180.5, "observed": "smoke"},
    {"lat": "36", "lon": 9, "observed": "smoke"},          # numbers, not strings
    {"lat": True, "lon": 9, "observed": "smoke"},
    {"lat": 36, "lon": 9, "observed": "fire"},
    {"lon": 9, "observed": "smoke"},
    {"lat": 36, "lon": 9, "observed": "smoke", "note": "x" * 281},
    {"lat": 36, "lon": 9, "observed": "smoke", "note": 5},
])
def test_post_reports_validates_the_body(client, body):
    assert client.post("/reports", json=body).status_code == 422


def test_post_reports_trims_the_note_before_the_length_check(client):
    body = {"lat": 36.9, "lon": 9.1, "observed": "both", "note": "  " + "x" * 280 + "\n "}
    assert reports_api.ReportIn(**body).note == "x" * 280
    assert client.post("/reports", json=body).status_code == 503  # valid; only the database is missing


def test_patch_rejects_an_unknown_status(client):
    assert client.patch(f"/reports/{REPORT_ID}", json={"status": "done"}).status_code == 422


def test_reviewer_token_401_without_403_wrong_and_constant_time(client, monkeypatch):
    monkeypatch.setenv("REPORTS_ADMIN_TOKEN", "letmein")
    compared = []
    real = reports_api.hmac.compare_digest
    monkeypatch.setattr(reports_api.hmac, "compare_digest", lambda a, b: compared.append((a, b)) or real(a, b))
    for send in (lambda **kw: client.patch(f"/reports/{REPORT_ID}", json={"status": "false"}, **kw),
                 lambda **kw: client.delete(f"/reports/{REPORT_ID}", **kw)):
        assert send().status_code == 401
        assert send(headers={"X-Admin-Token": ""}).status_code == 401
        assert send(headers={"X-Admin-Token": "letmeout"}).status_code == 403
        assert send(headers={"X-Admin-Token": "letmein"}).status_code == 503  # accepted; no database here
    assert (b"letmeout", b"letmein") in compared
    # the token is checked before the body, so a bad body without a token is still a 401
    assert client.patch(f"/reports/{REPORT_ID}", json={"status": "nope"}).status_code == 401
    # POST is always open
    assert client.post("/reports", json={"lat": 1, "lon": 2, "observed": "unsure"}).status_code == 503


def test_api_settings():
    s = ApiSettings()
    assert (s.database_url, s.api_version_check_s, s.reports_admin_token) == (None, 30.0, None)
    assert "token" not in repr(ApiSettings(reports_admin_token="token"))
    with pytest.raises(SettingsError, match="API_VERSION_CHECK_S"):
        ApiSettings(api_version_check_s=-1)


def test_api_settings_from_env(monkeypatch):
    monkeypatch.setenv("API_VERSION_CHECK_S", "5")
    monkeypatch.setenv("REPORTS_ADMIN_TOKEN", " tok ")
    monkeypatch.setenv("OPENROUTER_MODELS", "a/b")  # a worker-only error never stops the API
    s = ApiSettings.from_env()
    assert (s.api_version_check_s, s.reports_admin_token) == (5.0, "tok")
    monkeypatch.setenv("API_VERSION_CHECK_S", "soon")
    with pytest.raises(SettingsError, match="API_VERSION_CHECK_S must be a number"):
        ApiSettings.from_env()


def cm_row(year, month=7, viirs=None, viirs_days=None, modis_days=None, reference="SNPP", **kw):
    return {"cy": 73, "cx": 18, "year": year, "month": month, "modis": 4, "viirs_ref": viirs, "harmonized": 12.0,
            "k": 3.0, "support": 200.0, "modis_days": modis_days, "viirs_days": viirs_days, "reference": reference,
            "calibration_version_id": 1, "calibration_k_month": {"7": 3.2}, **kw}


def test_frame_from_rows_has_the_parquet_shape_and_viirs_zero_before_2012():
    df = core.frame_from_rows([cm_row(2011, reference="MODIS*k"), cm_row(2012, viirs=12.0),
                               cm_row(2026, viirs=None, reference="mixed", harmonized=None)])
    assert list(df.columns) == ["cy", "cx", "year", "month", "MODIS", "VIIRS", "k", "k_month", "support",
                                "harmonized", "source"]
    assert [str(t) for t in df.dtypes[:4]] == ["int32", "int32", "int16", "int8"]
    assert df.VIIRS.tolist()[:2] == [0.0, 12.0] and math.isnan(df.VIIRS[2])  # NULL from 2012 stays unknown
    assert math.isnan(df.harmonized[2]) and df.k_month.tolist() == [3.2, 3.2, 3.2]
    assert df.source.tolist() == ["MODIS*k", "VIIRS", "VIIRS"]


def test_frame_from_rows_adds_day_columns_only_when_every_row_has_them():
    rows = [cm_row(2011, modis_days=3, reference="MODIS*k"), cm_row(2013, viirs=5.0, modis_days=2, viirs_days=4)]
    df = core.frame_from_rows(rows)
    assert df.MODIS_days.tolist() == [3, 2] and df.VIIRS_days.tolist() == [0, 4]  # VIIRS_days 0 before 2012
    assert str(df.MODIS_days.dtype) == "int64"
    # A bootstrap without day counts plus a run's new row with them: no day columns (no new /calendar field).
    df = core.frame_from_rows([cm_row(2011, reference="MODIS*k"), cm_row(2026, viirs=5.0, modis_days=2, viirs_days=4)])
    assert "MODIS_days" not in df and "VIIRS_days" not in df


def run_api_process(env_extra: dict) -> subprocess.CompletedProcess:
    code = f"""
import sys
from fastapi.testclient import TestClient
import api.main
c = TestClient(api.main.app)
statuses = [c.get(p).status_code for p in {DB_GETS!r} + ["/calendar?area=bizerte", "/health"]]
statuses.append(c.post("/reports", json={{"lat": 1, "lon": 2, "observed": "smoke"}}).status_code)
print(statuses)
bad = sorted(m for m in sys.modules if m.split(".")[0] in {FORBIDDEN!r})
assert not bad, bad
"""
    import os
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "REPORTS_ADMIN_TOKEN")}
    return subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=120,
                          env={**env, **env_extra})


def test_i1_api_process_never_imports_worker_crewai_or_llm_clients(client):
    """I1 in fresh interpreters, after serving every endpoint, without and with an (unreachable) database."""
    import os
    data = {"EMBER_DATA": os.environ["EMBER_DATA"]}  # the client fixture's synthetic parquet
    r = run_api_process(data)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str([503] * 6 + [200, 200, 503])
    r = run_api_process({**data, "DATABASE_URL": UNREACHABLE})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str([503] * 6 + [200, 200, 503])
