"""Phase 4 on a real PostgreSQL (TEST_DATABASE_URL): migration 0002, bootstrap, firms_poll processing
with promotion / hold / reject, I2 (forced failure mid-promotion), I3, llm_attempts.

FIRMS data and the bootstrap parquet are SYNTHETIC (tests/fixtures/firms, conftest.make_dataset).
FIRMS is the fake session, every LLM is a llm_stub model and sockets are blocked (`no_network`
below: llm_stub's block, except that the TEST_DATABASE_URL host name may be resolved, because
psycopg resolves it in Python before libpq connects; libpq's own socket is not Python's).
"""
import json
import os
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timezone

import pandas as pd
import pytest

pytest.importorskip("sqlalchemy")
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

import worker.llm  # noqa: E402
import worker.process_pending  # noqa: E402
import worker.review  # noqa: E402
import worker.store  # noqa: E402
from conftest import make_dataset  # noqa: E402
from firms_fake import client_factory  # noqa: E402
import llm_stub  # noqa: E402
from llm_stub import FAKE_KEY, Script, crewai_storage  # noqa: E402,F401 - fixture
from processing.common import REF_MODIS, REF_SNPP  # noqa: E402
from processing.inter_viirs import FIT_KIND  # noqa: E402
from test_worker_pg import (BACKEND, PROCESSING_STEPS, T0, FakeClock, boot, bootstrap_synthetic, firms,  # noqa: E402,F401
                            job, one, pg, pg_url, rows, settings, steps)
from worker.handlers import HANDLERS  # noqa: E402
from worker.scheduler import Scheduler  # noqa: E402
from worker.store import load_approved_calibration  # noqa: E402

pytestmark = pytest.mark.pg

PHASE4_TABLES = {"calibration_versions", "calibration_k", "dataset_versions", "cell_month", "cell_month_staging",
                 "cell_month_history", "llm_attempts", "sensor_day_coverage"}
APPROVED = ("cell_month", "dataset_versions", "cell_month_history", "calibration_versions", "calibration_k")
# Affected cell-months of the 11 fixture rows fetched on T0 (windows 2026-11-13..14, see firms_fake):
# MODIS (72,17) (73,8)x2 (73,15); NOAA-20 (74,19) (73,17) (73,10, low confidence: dropped) (73,16);
# NOAA-21 (72,17) (73,15)x2.
NOV_KEYS = {(72, 17, 2026, 11), (73, 8, 2026, 11), (73, 15, 2026, 11), (74, 19, 2026, 11), (73, 17, 2026, 11),
            (73, 10, 2026, 11), (73, 16, 2026, 11)}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """llm_stub.no_network, but the test database's host name still resolves (and nothing else)."""
    db_host = make_url(os.environ.get("TEST_DATABASE_URL") or "postgresql://localhost/x").host or "localhost"
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        if host == db_host:
            return real_getaddrinfo(host, *args, **kwargs)
        return llm_stub._blocked(host, *args)

    monkeypatch.setattr(socket.socket, "connect", llm_stub._blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", llm_stub._blocked)
    monkeypatch.setattr(socket, "create_connection", llm_stub._blocked)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def test_sockets_are_blocked_except_resolving_the_database_host():
    with pytest.raises(llm_stub.NetworkBlocked):
        socket.create_connection(("openrouter.ai", 443))
    with pytest.raises(llm_stub.NetworkBlocked):
        socket.getaddrinfo("openrouter.ai", 443)


def review_settings(settings, *models):
    return replace(settings, openrouter_models=models, openrouter_api_key=FAKE_KEY if models else None,
                   openrouter_quota_check=False)


def scheduler(engine, settings, clock, firms, script=None, worker_id="worker-1", **kw):
    return Scheduler(engine, settings, worker_id, HANDLERS, clock, client_factory=client_factory(firms),
                     llm_factory=script.factory if script else None, **kw)


def run_poll(pg, settings, clock, firms, script=None):
    s = scheduler(pg.engine, settings, clock, firms, script)
    s.startup()
    [job_id] = s.tick()
    return job_id


def snapshot(engine) -> dict:
    """Row count and md5 of every approved / version table, in key order (I2: byte-identical)."""
    order = {"cell_month": "cy, cx, year, month", "calibration_k": "calibration_version_id, cy, cx, month"}
    out = {}
    with engine.connect() as c:
        for table in APPROVED:
            out[table] = tuple(c.execute(text(
                f"SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY {order.get(table, 'id')}), '')) "
                f"FROM {table} AS t")).one())
    return out


def pending(engine) -> int:
    return one(engine, "SELECT count(*) AS n FROM raw_detections WHERE needs_processing").n


def cell(engine, cy, cx, year, month):
    return one(engine, "SELECT * FROM cell_month WHERE cy = :cy AND cx = :cx AND year = :y AND month = :m",
               cy=cy, cx=cx, y=year, m=month)


# -- migration 0002 ------------------------------------------------------------------------------

def test_migration_0002_up_and_down(pg):
    def tables():
        return {r.tablename for r in rows(pg.engine, "SELECT tablename FROM pg_tables WHERE schemaname = :s",
                                          s=pg.schema)}

    def job_columns():
        return {r.column_name for r in rows(pg.engine, "SELECT column_name FROM information_schema.columns "
                                                       "WHERE table_schema = :s AND table_name = 'processing_jobs'",
                                            s=pg.schema)}

    assert PHASE4_TABLES <= tables()
    assert {"decision", "calibration_version_id", "detections_count", "staged_count", "dataset_version_id",
            "ai_review", "affected_count"} <= job_columns()
    assert "community_reports" not in tables()  # owned by another phase's migration
    fks = {r.conname for r in rows(pg.engine, "SELECT conname FROM pg_constraint c JOIN pg_namespace n "
                                              "ON n.oid = c.connamespace WHERE n.nspname = :s AND contype = 'f'",
                                   s=pg.schema)}
    assert "processing_jobs_dataset_version_id_fkey" in fks
    for table in ("dataset_versions", "calibration_versions"):  # at most one approved version
        indexdef = one(pg.engine, "SELECT indexdef FROM pg_indexes WHERE schemaname = :s AND indexname = :i",
                       s=pg.schema, i=f"{table}_one_approved").indexdef
        assert "UNIQUE" in indexdef and "approved" in indexdef

    pg.migrate("0001", down=True)
    assert not PHASE4_TABLES & tables()
    assert {"raw_detections", "processing_jobs"} <= tables()
    assert not {"decision", "calibration_version_id", "detections_count", "staged_count"} & job_columns()
    assert {"dataset_version_id", "ai_review"} <= job_columns()  # 0001's columns stay
    pg.migrate()
    assert PHASE4_TABLES <= tables()


# -- bootstrap -----------------------------------------------------------------------------------

def test_bootstrap_equals_the_parquet(pg, tmp_path):
    result = bootstrap_synthetic(pg.engine, tmp_path / "boot")
    assert result.created and (result.calibration_version_id, result.dataset_version_id) == (1, 1)
    df = pd.read_parquet(tmp_path / "boot" / "harmonized.parquet")
    db = pd.DataFrame(rows(pg.engine, "SELECT * FROM cell_month ORDER BY cy, cx, year, month"))
    assert len(db) == len(df) == result.rows > 0
    m = df.merge(db, on=["cy", "cx", "year", "month"], how="outer", indicator=True, validate="one_to_one")
    assert (m["_merge"] == "both").all()
    pre = m.year < 2012
    assert pre.any() and (~pre).any()
    assert (m.modis == m.MODIS).all() and (m.harmonized_x == m.harmonized_y).all()
    assert (m.k_x == m.k_y).all() and (m.support_x == m.support_y).all()
    # Spec §15: VIIRS = 0 before 2012 in the parquet becomes NULL (I7); from 2012 it is the S-NPP count.
    assert m.loc[pre, "viirs_ref"].isna().all() and m.loc[pre, "n_snpp"].isna().all()
    assert (df.loc[df.year < 2012, "VIIRS"] == 0).all()
    assert (m.loc[~pre, "viirs_ref"] == m.loc[~pre, "VIIRS"]).all()
    assert (m.loc[~pre, "n_snpp"] == m.loc[~pre, "VIIRS"]).all()
    assert set(m.loc[pre, "reference"]) == {REF_MODIS} and set(m.loc[~pre, "reference"]) == {REF_SNPP}
    assert (m.loc[pre, "days"] == m.loc[pre, "MODIS_days"]).all()
    assert (m.loc[~pre, "days"] == m.loc[~pre, "VIIRS_days"]).all()
    assert m.n_noaa20.isna().all() and m.n_noaa21.isna().all()
    assert (~m.is_partial).all() and all(f == [] for f in m.quality_flags)
    assert set(m.dataset_version_id) == {1} and set(m.calibration_version_id) == {1}

    ds = one(pg.engine, "SELECT * FROM dataset_versions")
    assert (ds.id, ds.status, ds.parent_id, ds.job_id, ds.calibration_version_id) == (1, "approved", None, None, 1)
    assert ds.summary["rows"] == len(df) and ds.summary["viirs_null_before_2012"] == int(pre.sum())
    cal = one(pg.engine, "SELECT * FROM calibration_versions")
    assert (cal.status, cal.r_global, cal.mask_version, cal.snpp_end) == ("approved", 0.98, None, date(2026, 11, 2))
    assert cal.approved_at is not None and cal.validation_report["harmonized_validation"]["k_global"] == 3.04
    assert cal.validation_report["inter_viirs_fit"]["approved_r"] == 0.98
    # The calibration reads back intact (fingerprint) and gives every bootstrapped row its own k.
    with pg.engine.connect() as c:
        cv = load_approved_calibration(c)
    assert cv.id == 1 and cv.calibration.r_global == 0.98 and set(cv.calibration.r.values()) == {0.98}
    looked_up = cv.calibration.lookup(df[["cy", "cx", "month"]])
    assert (looked_up.k.to_numpy() == df.k.to_numpy()).all()
    assert (looked_up.support.to_numpy() == df.support.to_numpy()).all()


def test_bootstrap_is_idempotent_and_records_the_mask(pg, tmp_path):
    from processing.mask import build_static_mask
    from processing_synth import det, frame

    mask_path = tmp_path / "mask.json"
    build_static_mask(frame([det("VIIRS_SNPP_SP", 36.05, 3.05, f"2019-0{m}-01", type=2) for m in (1, 2, 3)])).save(mask_path)
    first = bootstrap_synthetic(pg.engine, tmp_path / "a", mask=mask_path)
    assert first.created
    before = snapshot(pg.engine)
    assert one(pg.engine, "SELECT mask_version FROM calibration_versions").mask_version.startswith("mask-")
    again = bootstrap_synthetic(pg.engine, tmp_path / "b", r=1.5)  # different inputs: still nothing done
    assert not again.created and again.dataset_version_id == 1 and "already bootstrapped" in again.message
    assert snapshot(pg.engine) == before

    # The CLI, run twice against the test schema: the second run is a no-op with exit code 0.
    url = make_url(pg.url).update_query_dict({"options": f"-csearch_path={pg.schema}"})
    env = {k: v for k, v in os.environ.items() if k not in ("STATIC_MASK_FILE",)}
    env["DATABASE_URL"] = url.render_as_string(hide_password=False)
    folder = tmp_path / "a"
    cmd = [sys.executable, "-m", "worker.bootstrap", "--parquet", str(folder / "harmonized.parquet"),
           "--inter-viirs", str(folder / "inter_viirs_fit.json")]
    for _ in range(2):
        r = subprocess.run(cmd, cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "already bootstrapped" in r.stdout + r.stderr
    assert snapshot(pg.engine) == before


def test_bootstrap_cli_on_an_empty_schema(pg, tmp_path):
    folder = tmp_path / "cli"
    folder.mkdir()
    parquet = make_dataset(folder)
    fit = folder / "inter_viirs_fit.json"
    fit.write_text(json.dumps({"kind": FIT_KIND, "approved_r": 0.97}))
    url = make_url(pg.url).update_query_dict({"options": f"-csearch_path={pg.schema}"})
    env = {k: v for k, v in os.environ.items() if k != "STATIC_MASK_FILE"}
    env["DATABASE_URL"] = url.render_as_string(hide_password=False)
    r = subprocess.run([sys.executable, "-m", "worker.bootstrap", "--parquet", parquet, "--inter-viirs", str(fit)],
                       cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "bootstrapped calibration version 1 and dataset version 1" in r.stdout + r.stderr
    assert one(pg.engine, "SELECT count(*) AS n FROM cell_month").n == len(pd.read_parquet(parquet))


def test_firms_poll_without_bootstrap_fails_clearly(pg, settings, firms):
    job_id = run_poll(pg, settings, FakeClock(), firms)
    j = job(pg.engine, job_id)
    assert j.status == "failed" and "python -m worker.bootstrap" in j.error
    assert pending(pg.engine) == 11  # nothing was processed: rows stay pending


# -- firms_poll processing -----------------------------------------------------------------------

def test_firms_poll_end_to_end_approved_by_the_review(boot, settings, firms):
    pg = boot
    script = Script({"stub/down": "timeout", "stub/ok": "ok"})
    cfg = review_settings(settings, "stub/down", "stub/ok")
    clock = FakeClock()
    v1 = snapshot(pg.engine)["cell_month"]
    job_id = run_poll(pg, cfg, clock, firms, script)
    j = job(pg.engine, job_id)
    assert (j.status, j.ai_review, j.error) == ("succeeded", "completed", None)
    assert (j.new_count, j.pending_count, j.affected_count, j.staged_count, j.calibration_version_id) == \
        (11, 11, len(NOV_KEYS), len(NOV_KEYS), 1)
    assert j.detections_count == 11  # every stored detection of the affected cell-months
    assert j.dataset_version_id == 2 and j.finished_at is not None

    # Steps: one row per flow step with its summary; the decision is on the job for the API.
    st = steps(pg.engine, job_id)
    assert list(st)[-len(PROCESSING_STEPS):] == PROCESSING_STEPS and all(r.status == "succeeded" for r in st.values())
    assert st["filter"].summary["kept_rows"] == 10 and st["filter"].summary["dropped"]["low_confidence"] == {"NOAA20": 1}
    assert st["review"].summary["ai_review"] == "completed" and st["review"].summary["model"] == "stub/ok"
    assert st["publish"].summary["dataset_version_id"] == 2 and st["publish"].summary["inserted"] == len(NOV_KEYS)
    d = j.decision
    assert (d["decision"], d["basis"], d["ai_review"], d["gates_passed"]) == ("approve", "verdict", "completed", True)
    assert d["verdict"] == {"decision": "approve", "reasons": ["all hard gates passed"], "flagged_keys": []}
    assert [g["name"] for g in d["gates"]] == ["G1_schema_range", "G2_conservation", "G3_missing_not_zero",
                                               "G4_frozen_calibration"]
    assert {f["flag"] for f in d["soft_flags"]} >= {"partial", "mixed_reference"}

    # Every LLM attempt persisted, in order, without the key.
    att = rows(pg.engine, "SELECT * FROM llm_attempts WHERE job_id = :id ORDER BY attempt_no", id=job_id)
    assert [(a.step, a.attempt_no, a.model, a.outcome) for a in att] == [
        ("review", 1, "stub/down", "timeout"), ("review", 2, "stub/ok", "ok")]
    assert att[0].error_class == "APITimeoutError" and att[1].error is None
    assert att[1].total_tokens and att[1].latency_ms >= 0 and att[0].started_at is not None
    assert FAKE_KEY not in str([tuple(a) for a in att]) + json.dumps(d) + str([tuple(s) for s in st.values()])

    # Promotion: new keys inserted with the new version; the bootstrapped rows untouched.
    versions = rows(pg.engine, "SELECT * FROM dataset_versions ORDER BY id")
    assert [(v.id, v.status, v.parent_id, v.job_id) for v in versions] == [
        (1, "superseded", None, None), (2, "approved", 1, job_id)]
    assert versions[1].summary["inserted"] == len(NOV_KEYS) and versions[1].summary["cleared_pending"] == 11
    new = rows(pg.engine, "SELECT * FROM cell_month WHERE dataset_version_id = 2")
    assert {(r.cy, r.cx, r.year, r.month) for r in new} == NOV_KEYS
    assert one(pg.engine, "SELECT count(*) AS n FROM cell_month").n == v1[0] + len(NOV_KEYS)
    assert one(pg.engine, "SELECT count(*) AS n FROM cell_month_history").n == 0
    assert pending(pg.engine) == 0
    # Hand-checked rows. November 2026 contains snpp_end (11-02) and S-NPP was not fetched: the
    # reference is unknown, so harmonized is NULL with a flag, never 0 (I7); the month is partial.
    r = cell(pg.engine, 73, 8, 2026, 11)          # two MODIS detections on 11-13 and 11-14
    assert (r.modis, r.modis_days, r.n_noaa20, r.n_noaa21, r.n_snpp) == (2, 2, 0, 0, None)
    assert (r.harmonized, r.viirs_ref, r.reference, r.is_partial) == (None, None, "mixed", True)
    assert {"partial", "reference_unavailable", "mixed_reference", "modis_partial"} <= set(r.quality_flags)
    assert (r.calibration_version_id, r.k) == (1, cell_k(pg, 73, 8, 11))
    r = cell(pg.engine, 73, 15, 2026, 11)         # one MODIS, two NOAA-21 (never part of the reference)
    assert (r.modis, r.n_noaa20, r.n_noaa21, r.harmonized) == (1, 0, 2, None)
    r = cell(pg.engine, 73, 10, 2026, 11)         # its only detection (low confidence) was dropped
    assert (r.modis, r.n_noaa20, r.n_noaa21) == (0, 0, 0)
    staged = one(pg.engine, "SELECT count(*) AS n FROM cell_month_staging WHERE job_id = :id", id=job_id).n
    assert staged == len(NOV_KEYS)
    cov = rows(pg.engine, "SELECT * FROM sensor_day_coverage ORDER BY source, day")
    assert {(c.source, c.day, c.status) for c in cov} == {
        (s, d, "available") for s in ("MODIS_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")
        for d in (date(2026, 11, 13), date(2026, 11, 14))}

    # A revised detection: only its cell-month is recomputed; the replaced row goes to history.
    row = next(x for x in firms.tables["MODIS_NRT"] if x["acq_date"] == "2026-11-14" and x["acq_time"] == "0150")
    row["confidence"] = "20"  # now below the MODIS threshold
    clock.advance(hours=3)
    second = run_poll(pg, cfg, clock, firms, script)
    j2 = job(pg.engine, second)
    assert (j2.status, j2.modified_count, j2.affected_count, j2.dataset_version_id) == ("succeeded", 1, 1, 3)
    r = cell(pg.engine, 73, 8, 2026, 11)
    assert (r.modis, r.modis_days, r.dataset_version_id) == (1, 1, 3)
    [h] = rows(pg.engine, "SELECT * FROM cell_month_history")
    assert (h.cy, h.cx, h.year, h.month, h.modis, h.dataset_version_id, h.replaced_by_version) == \
        (73, 8, 2026, 11, 2, 2, 3)
    assert one(pg.engine, "SELECT count(*) AS n FROM cell_month WHERE dataset_version_id = 2").n == len(NOV_KEYS) - 1
    assert [v.status for v in rows(pg.engine, "SELECT status FROM dataset_versions ORDER BY id")] == \
        ["superseded", "superseded", "approved"]
    assert pending(pg.engine) == 0


def cell_k(pg, cy, cx, month):
    with pg.engine.connect() as c:
        cal = load_approved_calibration(c).calibration
    return float(cal.lookup(pd.DataFrame({"cy": [cy], "cx": [cx], "month": [month]})).k.iloc[0])


@pytest.mark.parametrize("verdict", ["reject", "hold"])
def test_a_rejecting_or_holding_review_leaves_approved_data_untouched(boot, settings, firms, verdict):
    """I2: the verdict downgrades; approved tables byte-identical, staging kept, rows stay pending."""
    pg = boot
    flagged = [73, 8, 2026, 11]
    script = Script({"stub/strict": "ok"}, verdicts={"stub/strict": {
        "decision": verdict, "reasons": ["the reference is unavailable for this month"], "flagged_keys": [flagged]}})
    cfg = review_settings(settings, "stub/strict")
    before = snapshot(pg.engine)
    clock = FakeClock()
    job_id = run_poll(pg, cfg, clock, firms, script)
    j = job(pg.engine, job_id)
    status = {"reject": "rejected", "hold": "held"}[verdict]
    assert (j.status, j.ai_review, j.dataset_version_id, j.error) == (status, "completed", None, None)
    assert snapshot(pg.engine) == before
    assert pending(pg.engine) == 11
    assert one(pg.engine, "SELECT count(*) AS n FROM cell_month_staging WHERE job_id = :id", id=job_id).n == \
        len(NOV_KEYS) == j.staged_count
    d = j.decision
    assert (d["decision"], d["basis"], d["gates_passed"]) == (verdict, "verdict", True)
    assert d["verdict"]["reasons"] == ["the reference is unavailable for this month"]
    assert d["verdict"]["flagged_keys"] == [flagged]
    st = steps(pg.engine, job_id)
    assert st["publish"].status == "skipped" and "stay pending" in st["publish"].summary["note"]
    assert [a.outcome for a in rows(pg.engine, "SELECT outcome FROM llm_attempts WHERE job_id = :id", id=job_id)] == ["ok"]

    # The next poll brings nothing new, but the pending rows are processed again (§6 step 3).
    approve = Script({"stub/ok": "ok"})
    clock.advance(hours=3)
    second = run_poll(pg, review_settings(settings, "stub/ok"), clock, firms, approve)
    j2 = job(pg.engine, second)
    assert (j2.status, j2.new_count, j2.modified_count, j2.affected_count) == ("succeeded", 0, 0, len(NOV_KEYS))
    assert pending(pg.engine) == 0
    assert one(pg.engine, "SELECT count(*) AS n FROM cell_month_staging WHERE job_id = :id", id=job_id).n == \
        len(NOV_KEYS)  # the held / rejected run's staging rows are kept for inspection


def test_a_failed_hard_gate_rejects_even_without_review(boot, settings, firms, monkeypatch):
    pg = boot
    from processing import pipeline
    from processing.gates import GateResult

    real = pipeline.run_gates
    monkeypatch.setattr(pipeline, "run_gates", lambda *a, **kw: real(*a, **kw)[:3] + [
        GateResult("G4_frozen_calibration", False, {"failures": {"forced": {"rows": 1}}})])
    before = snapshot(pg.engine)
    job_id = run_poll(pg, settings, FakeClock(), firms)  # OPENROUTER_MODELS empty: review disabled
    j = job(pg.engine, job_id)
    assert (j.status, j.ai_review) == ("rejected", "disabled")
    assert (j.decision["basis"], j.decision["gates_passed"], j.decision["verdict"]) == ("hard_gate_failed", False, None)
    assert snapshot(pg.engine) == before and pending(pg.engine) == 11
    assert rows(pg.engine, "SELECT * FROM llm_attempts") == []


def test_every_model_failing_publishes_on_the_gates(boot, settings, firms):
    """D3: the gates passed and every model failed -> approve with ai_review = unavailable."""
    pg = boot
    script = Script({"stub/a": "server_error", "stub/b": "garbage"})
    job_id = run_poll(pg, review_settings(settings, "stub/a", "stub/b"), FakeClock(), firms, script)
    j = job(pg.engine, job_id)
    assert (j.status, j.ai_review, j.decision["basis"], j.decision["verdict"]) == \
        ("succeeded", "unavailable", "gates_only", None)
    assert [(a.model, a.outcome) for a in rows(pg.engine, "SELECT * FROM llm_attempts ORDER BY attempt_no")] == [
        ("stub/a", "http_error"), ("stub/b", "invalid_output")]


def test_forced_failure_mid_promotion_leaves_approved_data_identical(boot, settings, firms, monkeypatch):
    """I2: an exception after the history copy and the upsert rolls the whole promotion back."""
    pg = boot
    calls = []

    def boom(c, processed):
        # By now the history copy, the upsert, the new version and the job status are written
        # in this transaction; all of it must disappear.
        calls.append(c.scalar(text("SELECT count(*) FROM cell_month WHERE dataset_version_id = 2")))
        raise RuntimeError("simulated crash in the middle of the promotion")

    monkeypatch.setattr(worker.store, "_clear_pending", boom)
    before = snapshot(pg.engine)
    clock = FakeClock()
    job_id = run_poll(pg, settings, clock, firms)
    assert calls == [len(NOV_KEYS)]  # the upsert had happened inside the transaction
    j = job(pg.engine, job_id)
    assert j.status == "failed" and "simulated crash" in j.error and j.dataset_version_id is None
    assert snapshot(pg.engine) == before
    assert pending(pg.engine) == 11
    assert steps(pg.engine, job_id)["publish"].status == "failed"
    assert one(pg.engine, "SELECT count(*) AS n FROM cell_month_staging WHERE job_id = :id", id=job_id).n == \
        len(NOV_KEYS)

    monkeypatch.undo()  # the next run publishes what is still pending
    clock.advance(hours=3)
    again = run_poll(pg, settings, clock, firms)
    j = job(pg.engine, again)
    # The rolled-back insert used up identity value 2 (sequences are not transactional): no gap in data.
    approved = one(pg.engine, "SELECT * FROM dataset_versions WHERE status = 'approved'")
    assert (j.status, j.dataset_version_id, approved.id, approved.parent_id) == ("succeeded", 3, 3, 1)
    assert pending(pg.engine) == 0


def test_no_change_never_imports_crewai(boot, settings, firms, monkeypatch, tmp_path):
    """I3: with no new, modified or pending rows the job ends no_change without the processing
    module or crewai, even with a model list configured."""
    pg = boot
    cfg = review_settings(settings, "stub/ok")
    clock = FakeClock()
    first = run_poll(pg, cfg, clock, firms, Script({"stub/ok": "ok"}))
    assert job(pg.engine, first).status == "succeeded"

    def forbidden(*a, **kw):
        raise AssertionError("processing or crewai reached on a no_change run")

    monkeypatch.setattr(worker.process_pending, "process_pending", forbidden)
    monkeypatch.setattr(worker.review, "run_processing", forbidden)
    monkeypatch.setattr(worker.llm, "import_crewai", forbidden)
    monkeypatch.setattr(worker.review, "import_crewai", forbidden)
    clock.advance(hours=3)
    second = run_poll(pg, cfg, clock, firms, Script({"stub/ok": "ok"}))
    assert job(pg.engine, second).status == "no_change"

    # And in a fresh interpreter: a whole no_change tick leaves crewai out of sys.modules.
    url = make_url(pg.url).update_query_dict({"options": f"-csearch_path={pg.schema}"})
    code = f"""
import sys
from datetime import datetime, timedelta, timezone
sys.path[:0] = ["tests"]
from firms_fake import FakeFirmsSession, client_factory
from db import make_engine
from settings import Settings
from worker.handlers import HANDLERS
from worker.scheduler import Scheduler
now = datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)
cfg = Settings(firms_map_key="TESTKEY123", firms_raw_dir={str(tmp_path / 'raw2')!r}, firms_max_backfill_days=2,
               openrouter_models=("stub/ok",), openrouter_api_key="sk-or-v1-fake", openrouter_quota_check=False)
engine = make_engine({url.render_as_string(hide_password=False)!r})
s = Scheduler(engine, cfg, "subproc", HANDLERS, lambda: now, client_factory=client_factory(FakeFirmsSession()))
s.startup()
[job_id] = s.tick()
with engine.connect() as c:
    status = c.exec_driver_sql("SELECT status FROM processing_jobs WHERE id = %s", (job_id,)).scalar()
assert status == "no_change", status
bad = sorted(m for m in sys.modules if m.split(".")[0] in ("crewai", "crewai_core", "openai"))
assert not bad, bad
assert "worker.process_pending" not in sys.modules and "worker.review" not in sys.modules
print("ok")
"""
    r = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=120,
                       env={k: v for k, v in os.environ.items() if not k.startswith(("OPENROUTER_", "FIRMS_"))})
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr


def test_heartbeat_keeps_the_lease_alive_during_a_long_review(boot, settings, firms):
    pg = boot
    start = datetime.now(timezone.utc)

    def clock():  # real time, shifted to the fixtures' day
        return T0 + (datetime.now(timezone.utc) - start)

    script = Script({"stub/slow": "ok"})
    in_review = threading.Event()

    def slow_factory(model, settings, *, timeout_s=None):
        in_review.set()
        threading.Event().wait(2.0)  # the model takes longer than the 0.9 s lease
        return script.factory(model, settings, timeout_s=timeout_s)

    cfg = replace(review_settings(settings, "stub/slow"), worker_lease_seconds=0.9)
    worker_ = Scheduler(pg.engine, cfg, "worker-slow", HANDLERS, clock, client_factory=client_factory(firms),
                        llm_factory=slow_factory)
    watcher = Scheduler(pg.new_engine(), cfg, "worker-watch", {}, clock)
    worker_.startup()
    with ThreadPoolExecutor(1) as ex:
        future = ex.submit(worker_.tick)
        assert in_review.wait(30)
        threading.Event().wait(1.4)
        assert watcher.abandon_expired() == []
        assert one(pg.engine, "SELECT status FROM processing_jobs").status == "running"
        [job_id] = future.result(timeout=60)
    j = job(pg.engine, job_id)
    assert (j.status, j.ai_review) == ("succeeded", "completed")
