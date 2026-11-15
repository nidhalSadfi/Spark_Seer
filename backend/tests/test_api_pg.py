"""The API against a real PostgreSQL (TEST_DATABASE_URL, one fresh schema per test; spec §10):
/calendar from the approved cell_month table, /processing/*, /fires/live, /reports, migration 0003
and I1 (GETs run READ ONLY; the API process imports no worker, crewai or LLM client).

All data is SYNTHETIC: conftest.make_dataset for the bootstrap, tests/fixtures/firms through the
fake FIRMS session, llm_stub models, and detections and reports written by the tests themselves.
No socket other than the database is opened (no_network from test_worker_processing_pg).
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("sqlalchemy")
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from api import core, database  # noqa: E402
from api.main import app  # noqa: E402
from conftest import YEARS, make_dataset  # noqa: E402
from llm_stub import Script, crewai_storage  # noqa: E402,F401 - fixture
from processing.inter_viirs import FIT_KIND  # noqa: E402
from test_worker_pg import BACKEND, T0, FakeClock, boot, firms, pg, pg_url, settings  # noqa: E402,F401
from test_worker_processing_pg import (NOV_KEYS, no_network, review_settings, run_poll,  # noqa: E402,F401
                                       snapshot)
from worker.bootstrap import bootstrap  # noqa: E402

pytestmark = pytest.mark.pg

AREAS = ["bizerte", "kabylie", "ne_algeria", "petite_kabylie"]
REGION = "-2.5,34,11.5,38"
ALL_TABLES = ("schedules", "processing_jobs", "job_steps", "firms_fetches", "firms_availability", "raw_detections",
              "llm_attempts", "dataset_versions", "calibration_versions", "calibration_k", "cell_month",
              "cell_month_staging", "cell_month_history", "sensor_day_coverage")


def api_url(pg) -> str:
    """DATABASE_URL for the API process: the test schema (and its application_name, so the pg
    fixture's teardown ends these sessions too)."""
    url = make_url(pg.url).update_query_dict({"options": f"-csearch_path={pg.schema}", "application_name": pg.schema})
    return url.render_as_string(hide_password=False)


@pytest.fixture
def api(pg, monkeypatch):
    """The app with DATABASE_URL on the test schema and the version re-checked on every request."""
    monkeypatch.setenv("DATABASE_URL", api_url(pg))
    monkeypatch.setenv("API_VERSION_CHECK_S", "0")
    return TestClient(app)


def boot_parquet(engine, folder: Path, optional_cols=False) -> str:
    """Calibration 1 + dataset 1 from a SYNTHETIC parquet; returns the parquet path."""
    folder.mkdir(parents=True, exist_ok=True)
    parquet = make_dataset(folder, optional_cols=optional_cols)
    fit = folder / "inter_viirs_fit.json"
    fit.write_text(json.dumps({"kind": FIT_KIND, "approved_r": 0.98, "r_global": 0.98, "status": "synthetic"}))
    result = bootstrap(engine, parquet=Path(parquet), validation=folder / "harmonized_validation.json",
                       inter_viirs=fit, mask=None, now=T0 - timedelta(days=1))
    assert result.created
    return parquet


def table_digest(engine) -> dict:
    """Row count and md5 of every processing / approved table (unchanged = nothing was written)."""
    with engine.connect() as c:
        return {t: tuple(c.execute(text(f"SELECT count(*), md5(coalesce(string_agg(x::text, '|' ORDER BY x::text), "
                                        f"'')) FROM {t} AS x")).one()) for t in ALL_TABLES}


CALENDAR_QUERIES = ([f"area={a}" for a in AREAS] + [f"area={a}&start=2008&end=2015" for a in AREAS]
                    + [f"bbox={REGION}", f"bbox={REGION}&start=2011&end=2012", "bbox=8.3,36.0,9.8,37.2&start=2012",
                       "bbox=20,10,21,11"])


def calendars(client) -> dict:
    return {q: client.get(f"/calendar?{q}").json() for q in CALENDAR_QUERIES}


COUNT_FIELDS = ("year", "month", "modis_raw", "viirs_raw", "harmonized", "support", "MODIS_days", "VIIRS_days")


def assert_database_matches_parquet(from_db: dict, from_parquet: dict) -> None:
    """The database calendar against the parquet one for CALENDAR_QUERIES (spec §10, I7).

    The synthetic dataset only has the years YEARS. The parquet calls every month "full" (as before
    `coverage` existed); the database calls the months outside YEARS "none", keeps their zero-filled
    counts, and leaves them out of the anomaly baseline. So a query inside YEARS is identical, and
    any query has the same counts, with z, flag and critical_months as the parquet over YEARS only.
    """
    for q in CALENDAR_QUERIES:
        db, pq = from_db[q], from_parquet[q]
        if not pq["months"]:
            assert db == pq, q
            continue
        assert {m["coverage"] for m in pq["months"]} == {"full"}, q
        assert [m["coverage"] for m in db["months"]] == \
            ["full" if m["year"] in YEARS else "none" for m in db["months"]], q
        counts = lambda body: [{k: m[k] for k in COUNT_FIELDS if k in m} for m in body["months"]]  # noqa: E731
        assert counts(db) == counts(pq), q
        years = {m["year"] for m in pq["months"]}
        if years <= set(YEARS):
            assert db == pq, q
    for area in AREAS:  # outside YEARS nothing enters the baseline: the anomaly scores of the covered years
        db, pq = from_db[f"area={area}"], from_parquet[f"area={area}&start=2008&end=2015"]
        if not pq["months"]:
            continue
        covered = [m for m in db["months"] if m["coverage"] == "full"]
        assert covered == pq["months"], area
        assert db["meta"] == pq["meta"], area


# -- /calendar from cell_month ---------------------------------------------------------------------

def test_calendar_from_the_bootstrapped_database_equals_the_parquet(api, pg, tmp_path, monkeypatch):
    parquet = boot_parquet(pg.engine, tmp_path / "boot")
    monkeypatch.setenv("EMBER_DATA", parquet)
    health = api.get("/health").json()
    assert (health["data_source"], health["dataset_version_id"]) == ("database", 1)
    from_db = calendars(api)
    # The same frame. The synthetic parquet is not sorted by key and stores int64 keys and int counts;
    # the real one is sorted, with calibrate.py's int32/int16/int8 keys and float counts, as the database frame.
    expected = pd.read_parquet(parquet).sort_values(["cy", "cx", "year", "month"], ignore_index=True)
    pd.testing.assert_frame_equal(core.get_data(), expected, check_dtype=False)

    monkeypatch.delenv("DATABASE_URL")
    assert api.get("/health").json()["data_source"] == "parquet"
    from_parquet = calendars(api)
    assert_database_matches_parquet(from_db, from_parquet)
    # Spec §15: VIIRS is NULL before 2012 in the database, and viirs_raw stays 0 there.
    months = from_db[f"bbox={REGION}&start=2011&end=2012"]["months"]
    assert all(m["viirs_raw"] == 0 for m in months if m["year"] == 2011)
    assert any(m["viirs_raw"] > 0 for m in months if m["year"] == 2012)
    assert health["rows"] == len(pd.read_parquet(parquet))


def test_calendar_day_columns_come_from_the_database_too(api, pg, tmp_path, monkeypatch):
    """A parquet with MODIS_days/VIIRS_days: the database has them (not low/high, which it does not store)."""
    parquet = boot_parquet(pg.engine, tmp_path / "boot", optional_cols=True)
    monkeypatch.setenv("EMBER_DATA", parquet)
    from_db = calendars(api)
    monkeypatch.delenv("DATABASE_URL")
    from_parquet = calendars(api)
    for q in CALENDAR_QUERIES:
        for m in from_parquet[q]["months"]:
            m.pop("low", None), m.pop("high", None)
    assert_database_matches_parquet(from_db, from_parquet)
    assert "VIIRS_days" in from_db["area=bizerte"]["months"][0]


def test_calendar_follows_a_new_approved_version_after_the_check_interval(api, pg, tmp_path, monkeypatch):
    parquet = boot_parquet(pg.engine, tmp_path / "boot")
    monkeypatch.setenv("EMBER_DATA", parquet)
    monkeypatch.setenv("API_VERSION_CHECK_S", "30")
    now = [5000.0]
    monkeypatch.setattr(core, "_clock", lambda: now[0])
    loads = []
    real = core.frame_from_rows
    monkeypatch.setattr(core, "frame_from_rows", lambda rows: loads.append(1) or real(rows))
    query = "/calendar?area=bizerte&start=2014&end=2014"
    before = api.get(query).json()
    march = next(m for m in before["months"] if m["month"] == 3)

    with pg.engine.begin() as c:  # what a promotion does, by hand: a new approved version changes one row
        c.execute(text("UPDATE dataset_versions SET status = 'superseded' WHERE id = 1"))
        c.execute(text("INSERT INTO dataset_versions (parent_id, calibration_version_id, status, created_at, "
                       "approved_at) VALUES (1, 1, 'approved', now(), now())"))
        c.execute(text("UPDATE cell_month SET harmonized = harmonized + 1000, dataset_version_id = 2 "
                       "WHERE cy = 73 AND cx = 18 AND year = 2014 AND month = 3"))
    now[0] += 29
    assert api.get(query).json() == before  # not re-checked yet
    now[0] += 2
    after = api.get(query).json()
    assert next(m for m in after["months"] if m["month"] == 3)["harmonized"] == march["harmonized"] + 1000
    assert api.get("/health").json()["dataset_version_id"] == 2
    now[0] += 31
    api.get(query)
    assert len(loads) == 2  # re-checked, same version: the cached frame is reused


def add_cell_months(engine, rows) -> None:
    """Approved cell_month rows of dataset version 1, by hand (cy, cx, year, month, harmonized, is_partial)."""
    with engine.begin() as c:
        for cy, cx, year, month, harmonized, partial in rows:
            c.execute(text("""
                INSERT INTO cell_month (cy, cx, year, month, modis, viirs_ref, harmonized, k, support, reference,
                                        is_partial, calibration_version_id, dataset_version_id)
                VALUES (:cy, :cx, :y, :m, 0, :h, :h, 3.0, 200, 'NOAA20', :p, 1, 1)"""),
                {"cy": cy, "cx": cx, "y": year, "m": month, "h": harmonized, "p": partial})


def test_calendar_coverage_marks_partial_and_missing_months(api, pg, tmp_path, monkeypatch):
    """I7: a year-month the approved dataset has no row for, or after the current UTC month, is "none"
    (its zero-filled counts kept, but outside the baseline); one with a partial row is "partial"."""
    parquet = boot_parquet(pg.engine, tmp_path / "boot")
    monkeypatch.setenv("EMBER_DATA", parquet)
    # 2016: March complete in one bizerte cell, May partial in a kabylie cell only (the dataset as a
    # whole decides, so bizerte's May is "partial" too); every other 2016 month has no row at all.
    add_cell_months(pg.engine, [(73, 18, 2016, 3, 40.0, False), (72, 8, 2016, 5, 12.0, True),
                                (72, 8, 2016, 6, 7.0, False), (73, 18, 2016, 6, 3.0, True)])
    monkeypatch.setattr(core, "_utcnow", lambda: datetime(2016, 6, 20, tzinfo=timezone.utc))
    health = api.get("/health").json()
    assert (health["data_source"], health["data_end_year"]) == ("database", 2025)  # never before 2025

    body = api.get("/calendar?area=bizerte&start=2015&end=2016").json()
    months = {(m["year"], m["month"]): m for m in body["months"]}
    assert {months[(2015, mo)]["coverage"] for mo in range(1, 13)} == {"full"}
    assert {mo: months[(2016, mo)]["coverage"] for mo in range(1, 13)} == {
        1: "none", 2: "none", 3: "full", 4: "none", 5: "partial", 6: "partial",
        7: "none", 8: "none", 9: "none", 10: "none", 11: "none", 12: "none"}  # 7-12: after June 2016
    assert months[(2016, 3)]["harmonized"] == 40.0 and months[(2016, 6)]["harmonized"] == 3.0
    none = months[(2016, 1)]
    assert (none["modis_raw"], none["viirs_raw"], none["harmonized"]) == (0, 0, 0)  # zero-filled, as before
    assert none["support"] == months[(2015, 1)]["support"]

    # The baseline (z, flag, critical_months) leaves "none" and "partial" months out: with 2016 in the
    # request, 2008-2015 score exactly as without it, except March, whose baseline gained a full 2016.
    score = lambda q: {(m["year"], m["month"]): (m["z"], m["flag"])  # noqa: E731
                       for m in api.get(f"/calendar?area=bizerte&{q}").json()["months"]}
    with_2016, alone = score("start=2008&end=2016"), score("start=2008&end=2015")
    assert {k: v for k, v in alone.items() if k[1] != 3} == \
        {k: v for k, v in with_2016.items() if k[0] <= 2015 and k[1] != 3}
    assert [v for k, v in with_2016.items() if k[1] == 3 and k[0] <= 2015] != [v for k, v in alone.items() if k[1] == 3]
    assert api.get("/calendar?area=bizerte&start=2008&end=2016").json()["meta"]["critical_months"] == \
        api.get("/calendar?area=bizerte&start=2008&end=2015").json()["meta"]["critical_months"]

    # The default end is the last year with data; a 2026 row moves it (and /health's data_end_year).
    with pg.engine.begin() as c:  # a new approved version, so the API reloads the frame
        c.execute(text("UPDATE dataset_versions SET status = 'superseded' WHERE id = 1"))
        c.execute(text("INSERT INTO dataset_versions (parent_id, calibration_version_id, status, created_at, "
                       "approved_at) VALUES (1, 1, 'approved', now(), now())"))
    add_cell_months(pg.engine, [(73, 18, 2026, 10, 9.0, True)])
    monkeypatch.setattr(core, "_utcnow", lambda: datetime(2026, 11, 15, 4, tzinfo=timezone.utc))
    assert api.get("/health").json()["data_end_year"] == 2026
    body = api.get("/calendar?area=bizerte").json()
    assert (body["months"][0]["year"], body["months"][-1]["year"]) == (2001, 2026)
    cov2026 = [m["coverage"] for m in body["months"] if m["year"] == 2026]
    assert cov2026 == ["none"] * 9 + ["partial", "none", "none"]  # November: no row; December: future
    assert api.get("/calendar?area=bizerte&end=2025").json()["months"][-1]["year"] == 2025  # explicit end kept


def test_calendar_uses_the_parquet_without_an_approved_version(api, pg, tmp_path, monkeypatch):
    monkeypatch.setenv("EMBER_DATA", make_dataset(tmp_path))  # migrated, never bootstrapped
    assert api.get("/health").json()["data_source"] == "parquet"
    assert api.get("/calendar?area=bizerte").status_code == 200


# -- /processing/* on real worker output -----------------------------------------------------------

def test_processing_endpoints_show_a_held_and_an_approved_run(boot, api, settings, firms):
    pg = boot
    flagged = [73, 8, 2026, 11]
    hold = Script({"stub/strict": "ok"}, verdicts={"stub/strict": {
        "decision": "hold", "reasons": ["the reference is unavailable for this month"], "flagged_keys": [flagged]}})
    clock = FakeClock()
    held = run_poll(pg, review_settings(settings, "stub/strict"), clock, firms, hold)
    clock.advance(hours=3)
    approved = run_poll(pg, review_settings(settings, "stub/down", "stub/ok"), clock, firms,
                        Script({"stub/down": "timeout", "stub/ok": "ok"}))
    before = table_digest(pg.engine)

    d = api.get(f"/processing/jobs/{held}").json()
    assert (d["id"], d["kind"], d["status"], d["trigger"], d["decision"], d["ai_review"]) == \
        (held, "firms_poll", "held", "schedule", "hold", "completed")
    assert d["decision_basis"] == "verdict" and d["dataset_version_id"] is None and d["dataset_version"] is None
    assert d["counts"] == {"fetched": 11, "new": 11, "modified": 0, "unchanged": 0, "pending": 11,
                           "affected": len(NOV_KEYS), "detections": 11, "staged": len(NOV_KEYS)}
    assert d["created_at"].endswith("Z") and d["duration_s"] >= 0
    assert d["gates"]["passed"] is True
    assert [g["name"] for g in d["gates"]["results"]] == ["G1_schema_range", "G2_conservation",
                                                          "G3_missing_not_zero", "G4_frozen_calibration"]
    assert all(g["passed"] is True and isinstance(g["detail"], dict) for g in d["gates"]["results"])
    assert {f["flag"] for f in d["soft_flags"]} >= {"partial", "mixed_reference"}
    assert d["verdict"] == {"decision": "hold", "reasons": ["the reference is unavailable for this month"],
                            "flagged_keys": [flagged]}
    assert d["review"] == {"model": "stub/strict", "attempts": 1, "reason": None}
    names = [s["name"] for s in d["steps"]]
    assert names[:2] == ["availability", "planning"] and names[-5:] == ["drift", "review", "decide", "stage", "publish"]
    publish = d["steps"][-1]
    assert publish["status"] == "skipped" and "stay pending" in publish["summary"]["note"]
    assert all(isinstance(s["summary"], dict) and s["started_at"].endswith("Z") for s in d["steps"])
    [attempt] = d["llm_attempts"]
    assert (attempt["step"], attempt["attempt_no"], attempt["model"], attempt["outcome"], attempt["error_class"]) == \
        ("review", 1, "stub/strict", "ok", None)
    assert attempt["latency_ms"] >= 0 and set(attempt["tokens"]) == {"prompt", "completion", "total"}
    assert attempt["tokens"]["total"] > 0

    d2 = api.get(f"/processing/jobs/{approved}").json()
    assert (d2["status"], d2["decision"], d2["dataset_version_id"]) == ("succeeded", "approve", 2)
    assert d2["dataset_version"]["id"] == 2 and d2["dataset_version"]["status"] == "approved"
    assert [(a["model"], a["outcome"], a["error_class"]) for a in d2["llm_attempts"]] == [
        ("stub/down", "timeout", "APITimeoutError"), ("stub/ok", "ok", None)]
    assert d2["steps"][-1]["summary"]["dataset_version_id"] == 2

    page = api.get("/processing/jobs").json()
    assert (page["total"], page["limit"], page["offset"]) == (2, 50, 0)
    assert [j["id"] for j in page["jobs"]] == [approved, held]  # newest first
    assert page["jobs"][1] == {k: d[k] for k in page["jobs"][1]}  # list items are the detail's head
    assert [j["id"] for j in api.get("/processing/jobs?status=held").json()["jobs"]] == [held]
    assert api.get("/processing/jobs?status=held,succeeded&kind=firms_poll").json()["total"] == 2
    assert api.get("/processing/jobs?kind=monthly_reconcile").json() == {"jobs": [], "total": 0, "limit": 50,
                                                                         "offset": 0}
    assert [j["id"] for j in api.get("/processing/jobs?limit=1&offset=1").json()["jobs"]] == [held]
    assert api.get("/processing/jobs/999999").status_code == 404

    st = api.get("/processing/status").json()
    kinds = {k["kind"]: k for k in st["scheduler"]["kinds"]}
    assert set(kinds) == {"firms_poll", "monthly_reconcile"}
    poll = kinds["firms_poll"]
    assert poll["enabled"] is True and poll["interval_seconds"] == 3 * 3600 and poll["next_run_at"].endswith("Z")
    assert poll["last_run"]["id"] == approved and poll["last_success"]["job_id"] == approved
    assert poll["active_job"] is None and poll["last_error"] is None and poll["last_job_id"] == approved
    assert kinds["monthly_reconcile"]["enabled"] is False and kinds["monthly_reconcile"]["last_run"] is None
    assert st["approved"]["dataset_version"]["id"] == 2 and st["approved"]["calibration_version"]["id"] == 1
    assert st["approved"]["calibration_version"]["r_global"] == 0.98
    assert st["data"] == {"raw_detections": 11, "pending_detections": 0,
                          "approved_cell_months": st["data"]["approved_cell_months"]}
    assert st["last_error"] is None

    ds = api.get("/processing/datasets").json()
    assert ds["total"] == 2 and [v["id"] for v in ds["datasets"]] == [2, 1]
    assert [(v["status"], v["parent_id"], v["job_id"]) for v in ds["datasets"]] == [
        ("approved", 1, approved), ("superseded", None, None)]
    assert ds["datasets"][0]["current_rows"] == len(NOV_KEYS) and ds["datasets"][0]["summary"]["inserted"] == 7
    assert ds["datasets"][1]["summary"]["source"] == "bootstrap"
    assert [c["id"] for c in ds["calibrations"]] == [1]

    # /calendar now serves dataset version 2 (the new November 2026 rows are in the frame).
    assert api.get("/health").json()["dataset_version_id"] == 2
    assert ((core.get_data().year == 2026).sum()) == len(NOV_KEYS)
    # The near-real-time November is published partial; the default end follows it to 2026.
    assert api.get("/health").json()["data_end_year"] == 2026
    nov = next(m for m in api.get("/calendar?area=bizerte").json()["months"] if (m["year"], m["month"]) == (2026, 11))
    assert nov["coverage"] == "partial"

    # I1: none of these GETs wrote anything.
    assert table_digest(pg.engine) == before


def test_status_reports_a_failed_job_as_the_last_error(api, pg):
    with pg.engine.begin() as c:
        c.execute(text("INSERT INTO processing_jobs (kind, status, trigger, created_at, started_at, finished_at, "
                       "error) VALUES ('firms_poll', 'failed', 'manual', now(), now(), now(), 'FIRMS answered 500')"))
    st = api.get("/processing/status").json()
    assert st["last_error"]["error"] == "FIRMS answered 500" and st["last_error"]["status"] == "failed"
    assert st["approved"] == {"dataset_version": None, "calibration_version": None}
    j = api.get("/processing/jobs").json()["jobs"][0]
    assert (j["trigger"], j["decision"], j["error"]) == ("manual", None, "FIRMS answered 500")
    d = api.get(f"/processing/jobs/{j['id']}").json()
    assert (d["gates"], d["soft_flags"], d["verdict"], d["review"], d["steps"], d["llm_attempts"]) == \
        ({"passed": None, "results": []}, [], None, None, [], [])


def test_get_transactions_are_read_only(api, pg):
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="read-only transaction"):
        with database.read() as c:
            c.execute(text("INSERT INTO processing_jobs (kind, status, trigger, created_at) "
                           "VALUES ('firms_poll', 'failed', 'manual', now())"))
    with pg.engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM processing_jobs")).scalar() == 0


# -- /fires/live -----------------------------------------------------------------------------------

def add_detections(engine, dets, fetched_at=datetime(2026, 11, 15, 3, 31, tzinfo=timezone.utc)):
    """Store detections the way the worker does (only the columns /fires/live reads matter)."""
    with engine.begin() as c:
        job = c.execute(text("INSERT INTO processing_jobs (kind, status, trigger, created_at) "
                             "VALUES ('firms_poll', 'succeeded', 'schedule', now()) RETURNING id")).scalar()
        fetch = c.execute(text("INSERT INTO firms_fetches (job_id, source, bbox, window_start, window_days, fetched_at, "
                               "sha256, row_count, raw_path) VALUES (:j, 'MODIS_NRT', :b, '2026-11-14', 2, :f, 'x', 0, "
                               "'x') RETURNING id"), {"j": job, "b": REGION, "f": fetched_at}).scalar()
        for d in dets:
            at = d["at"]
            c.execute(text("""
                INSERT INTO raw_detections (source, satellite, latitude_raw, longitude_raw, acq_date_raw, acq_time_raw,
                    content_hash, product, latitude, longitude, acq_date, acq_time, instrument, confidence, frp,
                    daynight, delivered, first_seen_fetch_id, last_seen_fetch_id, changed_in_job_id)
                VALUES (:source, :sat, :lat_raw, :lon_raw, :d_raw, :t_raw, 'h', :product, :lat, :lon, :d, :t,
                    :instrument, :conf, :frp, :dn, '{}'::jsonb, :f, :f, :j)"""),
                {"source": d["source"], "sat": d["sat"], "lat_raw": str(d["lat"]), "lon_raw": str(d["lon"]),
                 "lat": d["lat"], "lon": d["lon"], "d_raw": at.date().isoformat(), "t_raw": at.strftime("%H%M"),
                 "d": at.date(), "t": at.time().replace(tzinfo=None),
                 "product": d.get("product", "NRT"), "instrument": "MODIS" if d["source"].startswith("MODIS") else "VIIRS",
                 "conf": d["conf"], "frp": d.get("frp"), "dn": d.get("dn", "D"), "f": fetch, "j": job})


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


FIRE_DETECTIONS = [  # newest NRT detection: 2026-11-15 00:58 UTC
    {"id": 1, "source": "VIIRS_NOAA21_NRT", "sat": "N21", "lat": 36.951, "lon": 9.101, "at": utc(2026, 11, 15, 0, 58),
     "conf": "h", "frp": 5.2, "dn": "N"},
    {"id": 2, "source": "MODIS_NRT", "sat": "Terra", "lat": 36.952, "lon": 9.123, "at": utc(2026, 11, 14, 10, 15),
     "conf": "72", "frp": 14.7},
    {"id": 3, "source": "VIIRS_NOAA20_NRT", "sat": "N20", "lat": 36.41, "lon": 8.71, "at": utc(2026, 11, 13, 0, 58),
     "conf": "n", "frp": None, "dn": "N"},                                   # exactly 48 h before: inside
    {"id": 4, "source": "VIIRS_NOAA20_NRT", "sat": "N20", "lat": 36.41, "lon": 8.71, "at": utc(2026, 11, 13, 0, 57),
     "conf": "l", "frp": 1.0, "dn": "N"},                                   # 48 h 1 min before: outside
    {"id": 5, "source": "MODIS_SP", "sat": "Aqua", "lat": 36.9, "lon": 9.1, "at": utc(2026, 11, 15, 5, 0),
     "conf": "90", "product": "SP"},                                        # SP: never shown, never the anchor
    {"id": 6, "source": "VIIRS_NOAA21_NRT", "sat": "N21", "lat": 30.5, "lon": 0.5, "at": utc(2026, 11, 14, 12, 0),
     "conf": "n", "frp": 3.0},                                              # outside the bbox below
    {"id": 7, "source": "MODIS_NRT", "sat": "Aqua", "lat": 36.7, "lon": 4.3, "at": utc(2026, 11, 9, 1, 50),
     "conf": "51", "frp": 9.4, "dn": "N"},                                  # only with hours=168
]


def fire_ids(body) -> list[int]:
    """Detection ids back to FIRE_DETECTIONS ids (database ids follow insert order)."""
    return [d["id"] for d in body["detections"]]


def test_fires_live_window_bbox_source_and_shape(api, pg):
    add_detections(pg.engine, FIRE_DETECTIONS)
    body = api.get("/fires/live").json()
    assert body["window"] == {"start": "2026-11-13T00:58:00Z", "end": "2026-11-15T00:58:00Z"}
    assert body["snapshot"] == "2026-11-15T03:31:00Z"  # the latest stored FIRMS fetch
    assert fire_ids(body) == [3, 2, 6, 1]  # oldest first
    # The original meta fields, plus the grid thresholds (spec §16); a small result stays "detections".
    assert {k: body["meta"][k] for k in ("count", "product", "hours", "bbox")} == \
        {"count": 4, "product": "NRT", "hours": 48, "bbox": None}
    assert body["mode"] == "detections" and body["meta"]["resolution"] is None and "cells" not in body
    assert body["sources"] == [{"source": "MODIS_NRT", "max_acq_date": "2026-11-14"},
                               {"source": "VIIRS_NOAA20_NRT", "max_acq_date": "2026-11-13"},
                               {"source": "VIIRS_NOAA21_NRT", "max_acq_date": "2026-11-15"}]
    first = body["detections"][-1]
    assert first == {"id": 1, "lat": 36.951, "lon": 9.101, "acq_time": "2026-11-15T00:58:00Z",
                     "source": "VIIRS_NOAA21_NRT", "satellite": "N21", "sensor": "viirs", "confidence": "h",
                     "frp": 5.2, "daynight": "N"}
    modis = body["detections"][1]
    assert (modis["sensor"], modis["confidence"], modis["satellite"], modis["acq_time"]) == \
        ("modis", "72", "Terra", "2026-11-14T10:15:00Z")  # confidence as FIRMS delivered it (text)
    assert body["detections"][0]["frp"] is None

    boxed = api.get("/fires/live?bbox=8,36,10,37.5&hours=48").json()
    assert fire_ids(boxed) == [3, 2, 1] and boxed["meta"]["bbox"] == [8.0, 36.0, 10.0, 37.5]
    assert boxed["window"] == body["window"]  # the bbox never moves the window
    assert fire_ids(api.get("/fires/live?hours=1").json()) == [1]
    assert fire_ids(api.get("/fires/live?hours=168").json()) == [7, 4, 3, 2, 6, 1]
    # A source filter anchors the window at that source's newest detection.
    modis_only = api.get("/fires/live?source=MODIS_NRT&hours=168").json()
    assert fire_ids(modis_only) == [7, 2] and modis_only["window"]["end"] == "2026-11-14T10:15:00Z"
    assert modis_only["sources"] == [{"source": "MODIS_NRT", "max_acq_date": "2026-11-14"}]
    assert fire_ids(api.get("/fires/live?source=modis_nrt,VIIRS_NOAA20_NRT").json()) == [4, 3, 2]


def test_fires_live_with_no_detections(api, pg):
    body = api.get("/fires/live?bbox=8,36,10,37").json()
    assert body == {"mode": "detections", "snapshot": None, "window": {"start": None, "end": None},
                    "detections": [], "sources": [],
                    "meta": {"count": 0, "product": "NRT", "hours": 48, "bbox": [8.0, 36.0, 10.0, 37.0],
                             "resolution": None, "max_detections": 20000, "grid_span_deg": 30.0,
                             "fine_grid_max_span_deg": 90.0}}


def test_fires_live_grid_mode(api, pg, monkeypatch):
    """Spec §16: a grid of cells instead of detections for a wide bbox, above the detection cap, or
    with mode=grid; 0.25 degrees up to a 90-degree span, 0.5 beyond (no bbox = the world)."""
    add_detections(pg.engine, FIRE_DETECTIONS)
    world = api.get("/fires/live?mode=grid").json()
    assert world["mode"] == "grid" and world["detections"] == [] and world["meta"]["resolution"] == 0.5
    assert world["window"] == {"start": "2026-11-13T00:58:00Z", "end": "2026-11-15T00:58:00Z"}  # unchanged
    assert world["cells"] == [
        {"lat": 30.75, "lon": 0.75, "count": 1, "frp_sum": 3.0, "frp_max": 3.0, "modis": 0, "viirs": 1,
         "latest": "2026-11-14T12:00:00Z"},
        {"lat": 36.25, "lon": 8.75, "count": 1, "frp_sum": None, "frp_max": None, "modis": 0, "viirs": 1,
         "latest": "2026-11-13T00:58:00Z"},
        {"lat": 36.75, "lon": 9.25, "count": 2, "frp_sum": 19.9, "frp_max": 14.7, "modis": 1, "viirs": 1,
         "latest": "2026-11-15T00:58:00Z"}]
    assert world["meta"]["count"] == 4 == sum(c["count"] for c in world["cells"]) and world["meta"]["cells"] == 3

    wide = api.get("/fires/live?bbox=-10,20,30,45").json()  # 40 degrees wide > FIRES_LIVE_GRID_SPAN_DEG (30)
    assert wide["mode"] == "grid" and wide["meta"]["resolution"] == 0.25
    assert [(c["lat"], c["lon"], c["count"]) for c in wide["cells"]] == \
        [(30.625, 0.625, 1), (36.375, 8.625, 1), (36.875, 9.125, 2)]
    assert api.get("/fires/live?bbox=0,30,29,45").json()["mode"] == "detections"  # 29 degrees: detections

    monkeypatch.setenv("FIRES_LIVE_MAX_DETECTIONS", "3")  # 4 detections in the window: over the cap
    capped = api.get("/fires/live").json()
    assert capped["mode"] == "grid" and capped["meta"]["max_detections"] == 3 and capped["meta"]["count"] == 4
    assert api.get("/fires/live?hours=1").json()["mode"] == "detections"  # 1 detection: under the cap
    assert api.get("/fires/live?mode=bogus").status_code == 400


# -- /reports --------------------------------------------------------------------------------------

def test_reports_create_list_review_delete(api, pg, monkeypatch):
    before = table_digest(pg.engine)
    a = api.post("/reports", json={"lat": 36.95, "lon": 9.1, "observed": "smoke", "note": "  smoke over the hill  ",
                                   "status": "confirmed", "id": "mine"})
    assert a.status_code == 201
    a = a.json()
    assert (a["lat"], a["lon"], a["observed"], a["note"], a["status"], a["reviewedAt"]) == \
        (36.95, 9.1, "smoke", "smoke over the hill", "pending", None)  # status and id are the server's
    assert a["id"] != "mine" and len(a["id"]) == 36 and a["createdAt"].endswith("Z")
    b = api.post("/reports", json={"lat": -10, "lon": 170, "observed": "unsure"}).json()
    assert b["note"] == ""
    assert [r["id"] for r in api.get("/reports").json()["reports"]] == [b["id"], a["id"]]  # newest first

    confirmed = api.patch(f"/reports/{a['id']}", json={"status": "confirmed"})
    assert confirmed.status_code == 200
    confirmed = confirmed.json()
    assert confirmed["status"] == "confirmed" and confirmed["reviewedAt"].endswith("Z")
    assert {k: v for k, v in confirmed.items() if k not in ("status", "reviewedAt")} == \
        {k: v for k, v in a.items() if k not in ("status", "reviewedAt")}
    assert api.patch(f"/reports/{a['id']}", json={"status": "false"}).json()["reviewedAt"] is not None
    reopened = api.patch(f"/reports/{a['id']}", json={"status": "pending"}).json()
    assert (reopened["status"], reopened["reviewedAt"]) == ("pending", None)

    assert api.delete(f"/reports/{b['id']}").status_code == 204
    assert api.delete(f"/reports/{b['id']}").status_code == 404
    assert api.patch(f"/reports/{b['id']}", json={"status": "confirmed"}).status_code == 404
    assert api.patch("/reports/not-a-uuid", json={"status": "confirmed"}).status_code == 404
    assert [r["id"] for r in api.get("/reports").json()["reports"]] == [a["id"]]

    # With REPORTS_ADMIN_TOKEN set, PATCH and DELETE need it; POST and GET stay open.
    monkeypatch.setenv("REPORTS_ADMIN_TOKEN", "s3cret-token")
    assert api.patch(f"/reports/{a['id']}", json={"status": "confirmed"}).status_code == 401
    assert api.delete(f"/reports/{a['id']}", headers={"X-Admin-Token": "nope"}).status_code == 403
    ok = api.patch(f"/reports/{a['id']}", json={"status": "confirmed"}, headers={"X-Admin-Token": "s3cret-token"})
    assert ok.status_code == 200 and ok.json()["status"] == "confirmed"
    assert api.post("/reports", json={"lat": 1, "lon": 1, "observed": "flames"}).status_code == 201
    assert len(api.get("/reports").json()["reports"]) == 2
    assert api.delete(f"/reports/{a['id']}", headers={"X-Admin-Token": "s3cret-token"}).status_code == 204

    # I1 amendment: report writes touch community_reports only.
    assert table_digest(pg.engine) == before


def test_reports_list_is_capped_at_500(api, pg):
    with pg.engine.begin() as c:
        c.execute(text("INSERT INTO community_reports (id, lat, lon, observed, note, status, created_at) "
                       "SELECT gen_random_uuid(), 36, 9, 'smoke', '', 'pending', now() - make_interval(secs => g) "
                       "FROM generate_series(1, 501) AS g"))
    reports = api.get("/reports").json()["reports"]
    assert len(reports) == 500
    assert reports == sorted(reports, key=lambda r: r["createdAt"], reverse=True)


def test_database_constraints_back_the_report_rules(pg):
    from sqlalchemy.exc import IntegrityError

    bad = ["(gen_random_uuid(), 95, 9, 'smoke', '', 'pending', now(), NULL)",
           "(gen_random_uuid(), 36, 9, 'fire', '', 'pending', now(), NULL)",
           f"(gen_random_uuid(), 36, 9, 'smoke', '{'x' * 281}', 'pending', now(), NULL)",
           "(gen_random_uuid(), 36, 9, 'smoke', '', 'confirmed', now(), NULL)",
           "(gen_random_uuid(), 36, 9, 'smoke', '', 'pending', now(), now())"]
    for values in bad:
        with pytest.raises(IntegrityError):
            with pg.engine.begin() as c:
                c.execute(text(f"INSERT INTO community_reports (id, lat, lon, observed, note, status, created_at, "
                               f"reviewed_at) VALUES {values}"))


def test_migration_0003_up_and_down_and_a_503_on_an_old_schema(api, pg):
    def tables():
        with pg.engine.connect() as c:
            return {r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = :s"),
                                            {"s": pg.schema})}

    assert "community_reports" in tables()
    pg.migrate("0002", down=True)
    assert "community_reports" not in tables() and "cell_month" in tables()
    r = api.get("/reports")
    assert r.status_code == 503 and "alembic upgrade head" in r.json()["detail"]
    pg.migrate()
    assert "community_reports" in tables() and api.get("/reports").json() == {"reports": []}


# -- I1 in a fresh API process -------------------------------------------------------------------

def test_api_process_serves_postgres_without_importing_worker_crewai_or_llm_clients(boot, tmp_path):
    pg = boot
    add_detections(pg.engine, FIRE_DETECTIONS)
    code = """
import sys
from fastapi.testclient import TestClient
import api.main
c = TestClient(api.main.app)
gets = ["/health", "/calendar?area=bizerte", "/processing/status", "/processing/jobs", "/processing/datasets",
        "/fires/live", "/reports"]
codes = [c.get(p).status_code for p in gets]
codes.append(c.post("/reports", json={"lat": 36.9, "lon": 9.1, "observed": "smoke"}).status_code)
assert c.get("/health").json()["data_source"] == "database"
print(codes)
bad = sorted(m for m in sys.modules if m.split(".")[0] in ("worker", "crewai", "crewai_core", "openai", "litellm"))
assert not bad, bad
"""
    import os
    env = {k: v for k, v in os.environ.items() if k not in ("REPORTS_ADMIN_TOKEN",)}
    env.update(DATABASE_URL=api_url(pg), EMBER_DATA=make_dataset(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == str([200] * 7 + [201])
