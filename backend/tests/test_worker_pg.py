"""Scheduler and firms_poll against a real PostgreSQL (TEST_DATABASE_URL). FIRMS data is SYNTHETIC.

Each test gets its own schema, migrated with Alembic to head and dropped afterwards. The whole module
skips with the reason when TEST_DATABASE_URL is unset or the server is unreachable; there is no
SQLite fallback, because SKIP LOCKED, advisory locks and partial indexes are what is under test.
"""
import gzip
import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from conftest import make_dataset  # noqa: E402
from db import make_engine, safe_url  # noqa: E402
from firms_fake import FakeFirmsSession, client_factory  # noqa: E402
from processing.inter_viirs import FIT_KIND  # noqa: E402
from settings import Settings  # noqa: E402
from worker.bootstrap import bootstrap  # noqa: E402
from worker.handlers import HANDLERS  # noqa: E402
from worker.scheduler import Scheduler  # noqa: E402
from worker.schedules import FIRMS_POLL, MONTHLY_RECONCILE, lock_key  # noqa: E402

pytestmark = pytest.mark.pg

BACKEND = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
SOURCES = ("MODIS_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")  # S-NPP is past its coverage_end on T0
# firms_poll steps when it processes (review disabled: OPENROUTER_MODELS empty).
PROCESSING_STEPS = ["prepare", "filter", "aggregate", "harmonize", "completeness", "gates", "drift", "review",
                    "decide", "stage", "publish"]


# -- fixtures ------------------------------------------------------------------------------------

@pytest.fixture(scope="session")
def pg_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set: PostgreSQL tests need a reachable database "
                    "(docker compose up -d db; see .env.example)")
    engine = make_engine(url, connect_timeout=3)
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:
        reason = str(getattr(e, "orig", None) or e).strip().splitlines()[0]
        pytest.skip(f"PostgreSQL at {safe_url(url)} is unreachable: {reason}")
    finally:
        engine.dispose()
    return url


@pytest.fixture
def pg(pg_url):
    """A fresh schema migrated to head; every engine from new_engine() uses it. Dropped afterwards."""
    schema = f"t_{uuid.uuid4().hex[:16]}"
    admin = make_engine(pg_url)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    engines = []

    def new_engine():
        e = make_engine(pg_url, connect_args={"options": f"-c search_path={schema} -c lock_timeout=10s",
                                              "application_name": schema})
        engines.append(e)
        return e

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["configure_logger"] = False

    def migrate(revision="head", down=False):
        with engines[0].begin() as conn:
            cfg.attributes["connection"] = conn
            (command.downgrade if down else command.upgrade)(cfg, revision)

    engine = new_engine()
    migrate()
    try:
        yield SimpleNamespace(engine=engine, new_engine=new_engine, schema=schema, migrate=migrate,
                              url=pg_url)
    finally:
        for e in engines:
            e.dispose()
        with admin.begin() as c:
            # A failed test may leave a connection (and its locks) open: end those sessions first.
            c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                           "WHERE application_name = :app AND pid <> pg_backend_pid()"), {"app": schema})
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


class FakeClock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


def bootstrap_synthetic(engine, folder: Path, r: float = 0.98, mask=None):
    """Calibration 1 + dataset 1 from a SYNTHETIC parquet (conftest.make_dataset) and fit file."""
    folder.mkdir(parents=True, exist_ok=True)
    parquet = Path(make_dataset(folder, optional_cols=True))
    fit = folder / "inter_viirs_fit.json"
    fit.write_text(json.dumps({"kind": FIT_KIND, "approved_r": r, "r_global": r, "status": "synthetic"}))
    return bootstrap(engine, parquet=parquet, validation=folder / "harmonized_validation.json", inter_viirs=fit,
                     mask=mask, now=T0 - timedelta(days=1))


@pytest.fixture
def boot(pg, tmp_path):
    """pg with approved calibration 1 and dataset 1 loaded from synthetic files."""
    result = bootstrap_synthetic(pg.engine, tmp_path / "boot")
    assert result.created
    return pg


@pytest.fixture
def settings(tmp_path):
    return Settings(firms_map_key="TESTKEY123", firms_raw_dir=tmp_path / "raw", firms_max_backfill_days=2)


@pytest.fixture
def firms():
    return FakeFirmsSession()


def make_scheduler(engine, settings, clock, firms=None, worker_id="worker-1", handlers=None):
    return Scheduler(engine, settings, worker_id, HANDLERS if handlers is None else handlers, clock,
                     client_factory=client_factory(firms or FakeFirmsSession()))


def rows(engine, sql, **params):
    with engine.connect() as c:
        return c.execute(text(sql), params).all()


def one(engine, sql, **params):
    with engine.connect() as c:
        return c.execute(text(sql), params).one()


def job(engine, job_id):
    return one(engine, "SELECT * FROM processing_jobs WHERE id = :id", id=job_id)


def steps(engine, job_id):
    return {r.name: r for r in rows(engine, "SELECT * FROM job_steps WHERE job_id = :id ORDER BY id", id=job_id)}


def done(status):
    return lambda ctx: status


# -- schema --------------------------------------------------------------------------------------

def test_migration_creates_tables_and_the_one_active_job_index(pg):
    tables = {r.tablename for r in rows(pg.engine, "SELECT tablename FROM pg_tables WHERE schemaname = :s",
                                        s=pg.schema)}
    assert {"schedules", "processing_jobs", "job_steps", "firms_fetches", "firms_availability",
            "raw_detections"} <= tables
    assert {"calibration_versions", "calibration_k", "dataset_versions", "cell_month", "cell_month_staging",
            "cell_month_history", "llm_attempts", "sensor_day_coverage"} <= tables  # Phase 4 (0002)
    indexdef = one(pg.engine, "SELECT indexdef FROM pg_indexes WHERE schemaname = :s "
                              "AND indexname = 'processing_jobs_one_active_per_kind'", s=pg.schema).indexdef
    assert "UNIQUE" in indexdef and "queued" in indexdef and "running" in indexdef
    insert = text("INSERT INTO processing_jobs (kind, status, trigger, created_at) "
                  "VALUES ('firms_poll', :status, 'schedule', now())")
    with pg.engine.begin() as c:
        for status in ("succeeded", "failed", "running"):
            c.execute(insert, {"status": status})
    with pytest.raises(IntegrityError, match="processing_jobs_one_active_per_kind"):
        with pg.engine.begin() as c:
            c.execute(insert, {"status": "queued"})
    types = {r.column_name: r.data_type for r in rows(
        pg.engine, "SELECT column_name, data_type FROM information_schema.columns "
                   "WHERE table_schema = :s AND data_type LIKE 'timestamp%'", s=pg.schema)}
    assert types and set(types.values()) == {"timestamp with time zone"}


def test_migration_downgrades_and_upgrades_again(pg):
    pg.migrate("base", down=True)
    assert not rows(pg.engine, "SELECT 1 FROM pg_tables WHERE schemaname = :s AND tablename = 'raw_detections'",
                    s=pg.schema)
    pg.migrate()
    assert rows(pg.engine, "SELECT 1 FROM pg_tables WHERE schemaname = :s AND tablename = 'raw_detections'",
                s=pg.schema)


# -- ingestion -----------------------------------------------------------------------------------

def test_first_poll_fetches_stores_and_classifies(boot, settings, firms):
    pg = boot
    s = make_scheduler(pg.engine, settings, FakeClock(), firms)
    s.startup()
    [job_id] = s.tick()
    j = job(pg.engine, job_id)
    # Fixture rows dated 2026-11-13..14: MODIS 4, NOAA-20 4, NOAA-21 3.
    assert (j.status, j.fetched_count, j.new_count, j.modified_count, j.unchanged_count, j.pending_count) == \
        ("succeeded", 11, 11, 0, 0, 11)
    assert j.error is None and j.finished_at is not None and j.lease_owner == "worker-1"
    assert sorted(firms.windows) == sorted((src, date(2026, 11, 13), 2) for src in SOURCES)
    assert all("TESTKEY123" in u for u in firms.urls)  # the key went to (fake) FIRMS ...

    st = steps(pg.engine, job_id)
    assert list(st) == ["availability", "planning", "fetch:MODIS_NRT", "fetch:VIIRS_NOAA20_NRT",
                        "fetch:VIIRS_NOAA21_NRT", "storage"] + PROCESSING_STEPS
    assert all(r.status == "succeeded" for r in st.values())
    assert "coverage_end" in st["planning"].summary["sources"]["VIIRS_SNPP_NRT"]["skipped"]
    assert st["storage"].summary["per_source"]["MODIS_NRT"]["new"] == 4
    assert st["storage"].summary["totals"]["fetched"] == 11
    assert st["fetch:VIIRS_NOAA21_NRT"].summary["counts"]["new"] == 3

    avail = {r.source: (r.min_date, r.max_date) for r in rows(
        pg.engine, "SELECT * FROM firms_availability WHERE job_id = :id", id=job_id)}
    assert len(avail) == 9 and avail["VIIRS_SNPP_NRT"] == (date(2026, 8, 1), date(2026, 11, 2))

    fetches = rows(pg.engine, "SELECT * FROM firms_fetches WHERE job_id = :id ORDER BY id", id=job_id)
    assert [f.source for f in fetches] == list(SOURCES) and [f.row_count for f in fetches] == [4, 4, 3]
    for f in fetches:
        path = Path(f.raw_path)
        assert path == settings.firms_raw_dir / f.source / "2026-11-14" / f"{f.id}.csv.gz"
        body = gzip.decompress(path.read_bytes())
        assert hashlib.sha256(body).hexdigest() == f.sha256
        assert (f.window_start, f.window_days, f.bbox) == (date(2026, 11, 13), 2, "-2.5,34,11.5,38")
        assert f.availability_max_date == date(2026, 11, 14)
    assert "TESTKEY123" not in str(st)  # ... but never into job records

    det = rows(pg.engine, "SELECT * FROM raw_detections ORDER BY id")
    assert len(det) == 11
    # Processed and published in the same job: the promotion cleared needs_processing (§6).
    assert all(not d.needs_processing and d.product == "NRT" and d.type is None for d in det)
    assert all(d.changed_in_job_id == job_id and d.first_seen_fetch_id == d.last_seen_fetch_id for d in det)
    modis = [d for d in det if d.source == "MODIS_NRT"]
    assert {(d.acq_date, d.satellite) for d in modis} == {(date(2026, 11, 13), "Aqua"), (date(2026, 11, 13), "Terra"),
                                                          (date(2026, 11, 14), "Aqua"), (date(2026, 11, 14), "Terra")}
    assert all(d.delivered["latitude"] == d.latitude_raw for d in det)


def test_same_data_twice_is_unchanged_and_no_change_needs_no_pending_rows(boot, settings, firms):
    pg = boot
    clock = FakeClock()
    s = make_scheduler(pg.engine, settings, clock, firms)
    s.startup()
    [first] = s.tick()
    assert job(pg.engine, first).status == "succeeded"
    # The first run's promotion cleared the pending flags: the same data again is no_change (I3).
    clock.advance(hours=3)
    [second] = s.tick()
    j = job(pg.engine, second)
    assert (j.status, j.fetched_count, j.new_count, j.modified_count, j.unchanged_count, j.pending_count) == \
        ("no_change", 11, 0, 0, 11, 0)
    assert not set(PROCESSING_STEPS) & set(steps(pg.engine, second))
    det = rows(pg.engine, "SELECT * FROM raw_detections")
    assert len(det) == 11 and all(d.changed_in_job_id == first and not d.needs_processing for d in det)
    assert all(d.last_seen_fetch_id != d.first_seen_fetch_id for d in det)

    # A row still pending (e.g. from a held run) is processed even with no new or modified rows.
    with pg.engine.begin() as c:
        c.execute(text("UPDATE raw_detections SET needs_processing = true WHERE source = 'MODIS_NRT' "
                       "AND acq_time_raw = '1240'"))
    clock.advance(hours=3)
    [third] = s.tick()
    j = job(pg.engine, third)
    assert (j.status, j.new_count, j.modified_count, j.unchanged_count, j.pending_count, j.affected_count) == \
        ("succeeded", 0, 0, 11, 1, 1)
    assert list(steps(pg.engine, third))[-len(PROCESSING_STEPS):] == PROCESSING_STEPS
    assert one(pg.engine, "SELECT count(*) AS n FROM raw_detections WHERE needs_processing").n == 0


def test_one_changed_field_is_one_modified_row(boot, settings, firms):
    pg = boot
    clock = FakeClock()
    s = make_scheduler(pg.engine, settings, clock, firms)
    s.startup()
    [first] = s.tick()
    assert one(pg.engine, "SELECT count(*) AS n FROM raw_detections WHERE needs_processing").n == 0
    before = one(pg.engine, "SELECT content_hash FROM raw_detections WHERE source = 'MODIS_NRT' "
                            "AND acq_date_raw = '2026-11-14' AND acq_time_raw = '1050'").content_hash
    row = next(r for r in firms.tables["MODIS_NRT"] if r["acq_date"] == "2026-11-14" and r["acq_time"] == "1050")
    row["confidence"] = "95"  # FIRMS revised one detection
    clock.advance(hours=3)
    [second] = s.tick()
    j = job(pg.engine, second)
    assert (j.status, j.new_count, j.modified_count, j.unchanged_count, j.pending_count) == ("succeeded", 0, 1, 10, 1)
    changed = rows(pg.engine, "SELECT * FROM raw_detections WHERE changed_in_job_id = :id", id=second)
    assert len(changed) == 1
    d = changed[0]
    assert (d.source, d.acq_time_raw, d.confidence, d.delivered["confidence"]) == ("MODIS_NRT", "1050", "95", "95")
    assert not d.needs_processing and d.content_hash != before  # processed and published by this job
    others = rows(pg.engine, "SELECT * FROM raw_detections WHERE id <> :id", id=d.id)
    assert all(o.changed_in_job_id == first and not o.needs_processing for o in others)


def test_backfill_starts_from_the_watermark_and_respects_the_cap(pg, settings, firms):
    clock = FakeClock(datetime(2026, 11, 10, 12, tzinfo=timezone.utc))
    cfg = replace(settings, firms_max_backfill_days=10)
    make_scheduler(pg.engine, cfg, clock, firms).startup()
    make_scheduler(pg.engine, cfg, clock, firms).tick()      # first poll: no watermark -> 10 days
    assert {w for w in firms.windows if w[0] == "MODIS_NRT"} == {
        ("MODIS_NRT", date(2026, 11, 1), 5), ("MODIS_NRT", date(2026, 11, 6), 5)}
    firms.windows.clear()
    clock.now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    make_scheduler(pg.engine, cfg, clock, firms).tick()      # watermark 11-10 -> 11-10..11-14
    assert {w for w in firms.windows if w[0] == "MODIS_NRT"} == {("MODIS_NRT", date(2026, 11, 10), 5)}
    firms.windows.clear()
    clock.now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    [job_id] = make_scheduler(pg.engine, replace(cfg, firms_max_backfill_days=2), clock, firms).tick()
    assert {w for w in firms.windows if w[0] == "MODIS_NRT"} == {("MODIS_NRT", date(2026, 11, 19), 2)}
    notes = steps(pg.engine, job_id)["planning"].summary["sources"]["MODIS_NRT"]["notes"]
    assert any("capped" in n for n in notes)


def test_a_new_bbox_is_backfilled_not_started_from_the_old_watermark(pg, settings, firms):
    # Switching FIRMS_BBOX (e.g. to the world, spec §16): the old area's fetches do not count.
    clock = FakeClock(datetime(2026, 11, 10, 12, tzinfo=timezone.utc))
    cfg = replace(settings, firms_max_backfill_days=10)
    make_scheduler(pg.engine, cfg, clock, firms).startup()
    make_scheduler(pg.engine, cfg, clock, firms).tick()
    firms.windows.clear()
    clock.now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    make_scheduler(pg.engine, replace(cfg, firms_bbox=(-10.0, 30.0, 20.0, 40.0)), clock, firms).tick()
    assert {w for w in firms.windows if w[0] == "MODIS_NRT"} == {
        ("MODIS_NRT", date(2026, 11, 5), 5), ("MODIS_NRT", date(2026, 11, 10), 5)}


def test_a_failing_source_fails_the_job_but_keeps_the_others(pg, settings, firms):
    firms.fail_sources.add("VIIRS_NOAA21_NRT")
    s = make_scheduler(pg.engine, settings, FakeClock(), firms)
    s.startup()
    [job_id] = s.tick()
    j = job(pg.engine, job_id)
    assert j.status == "failed" and "VIIRS_NOAA21_NRT" in j.error and "TESTKEY123" not in j.error
    assert (j.fetched_count, j.new_count) == (8, 8)
    st = steps(pg.engine, job_id)
    assert st["fetch:VIIRS_NOAA21_NRT"].status == "failed" and st["storage"].status == "succeeded"
    assert "TESTKEY123" not in str(st["fetch:VIIRS_NOAA21_NRT"].summary)
    assert {r.source for r in rows(pg.engine, "SELECT DISTINCT source FROM raw_detections")} == {
        "MODIS_NRT", "VIIRS_NOAA20_NRT"}


def test_missing_map_key_fails_the_job_and_the_scheduler_continues(boot, settings, firms):
    pg = boot
    clock = FakeClock()
    s = make_scheduler(pg.engine, replace(settings, firms_map_key=None), clock, firms)
    s.startup()
    [job_id] = s.tick()  # returns normally
    j = job(pg.engine, job_id)
    assert j.status == "failed" and "FIRMS_MAP_KEY" in j.error and j.finished_at is not None
    assert firms.urls == []  # never reached FIRMS
    # The lock was released and the schedule advanced: the next due run works once a key is set.
    clock.advance(hours=3)
    [again] = make_scheduler(pg.engine, settings, clock, firms).tick()
    assert job(pg.engine, again).status == "succeeded"


# -- scheduler -----------------------------------------------------------------------------------

def test_interval_comes_from_settings_and_a_restart_updates_it(pg, settings):
    clock = FakeClock()
    s = make_scheduler(pg.engine, settings, clock, handlers={FIRMS_POLL: done("no_change")})
    s.startup()
    r = one(pg.engine, "SELECT * FROM schedules WHERE kind = 'firms_poll'")
    assert (r.cadence, r.interval_seconds, r.enabled, r.next_run_at) == ("interval", 10800, True, T0)
    s.tick()
    assert one(pg.engine, "SELECT next_run_at FROM schedules WHERE kind = 'firms_poll'").next_run_at == \
        T0 + timedelta(hours=3)

    clock.advance(minutes=30)  # restart with a 1-hour interval: next run = last claim + 1 h
    make_scheduler(pg.engine, replace(settings, firms_poll_interval_hours=1), clock).startup()
    r = one(pg.engine, "SELECT * FROM schedules WHERE kind = 'firms_poll'")
    assert (r.interval_seconds, r.next_run_at) == (3600, T0 + timedelta(hours=1))
    make_scheduler(pg.engine, replace(settings, firms_poll_interval_hours=1), clock).startup()  # same: no change
    assert one(pg.engine, "SELECT next_run_at FROM schedules WHERE kind = 'firms_poll'").next_run_at == \
        T0 + timedelta(hours=1)

    quarter = replace(settings, firms_poll_interval_hours=0.25)  # already overdue under 15 min: runs now
    s2 = make_scheduler(pg.engine, quarter, clock, handlers={FIRMS_POLL: done("no_change")})
    s2.startup()
    assert len(s2.tick()) == 1
    assert one(pg.engine, "SELECT next_run_at FROM schedules WHERE kind = 'firms_poll'").next_run_at == \
        clock.now + timedelta(minutes=15)


def test_monthly_reconcile_schedule_exists_disabled(pg, settings):
    clock = FakeClock()
    make_scheduler(pg.engine, settings, clock).startup()
    r = one(pg.engine, "SELECT * FROM schedules WHERE kind = 'monthly_reconcile'")
    assert (r.cadence, r.day_of_month, r.enabled, r.next_run_at, r.interval_seconds) == \
        ("monthly", 5, False, datetime(2026, 12, 5, 3, tzinfo=timezone.utc), None)
    with pg.engine.begin() as c:
        c.execute(text("UPDATE schedules SET enabled = true WHERE kind = 'monthly_reconcile'"))
    make_scheduler(pg.engine, replace(settings, reconcile_day_of_month=7), clock).startup()
    r = one(pg.engine, "SELECT * FROM schedules WHERE kind = 'monthly_reconcile'")
    assert (r.day_of_month, r.enabled, r.next_run_at) == (7, True, datetime(2026, 12, 7, 3, tzinfo=timezone.utc))


def test_overdue_schedule_runs_once(pg, settings):
    clock = FakeClock()
    s = make_scheduler(pg.engine, settings, clock, handlers={FIRMS_POLL: done("no_change")})
    s.startup()
    with pg.engine.begin() as c:  # worker was down for 5 intervals
        c.execute(text("UPDATE schedules SET next_run_at = :t WHERE kind = 'firms_poll'"),
                  {"t": T0 - timedelta(hours=15)})
    assert len(s.tick()) == 1
    assert s.tick() == [] and s.tick() == []
    assert one(pg.engine, "SELECT count(*) AS n FROM processing_jobs").n == 1
    r = one(pg.engine, "SELECT * FROM schedules WHERE kind = 'firms_poll'")
    assert (r.next_run_at, r.last_claimed_at) == (T0 + timedelta(hours=3), T0)


def test_due_kind_without_handler_is_skipped(pg, settings, caplog):
    clock = FakeClock()
    s = make_scheduler(pg.engine, settings, clock, handlers={FIRMS_POLL: done("no_change")})
    s.startup()
    with pg.engine.begin() as c:
        c.execute(text("UPDATE schedules SET enabled = true, next_run_at = :t WHERE kind = 'monthly_reconcile'"),
                  {"t": T0 - timedelta(hours=1)})
    caplog.set_level(logging.WARNING, logger="worker.scheduler")
    s.tick()
    assert [r.kind for r in rows(pg.engine, "SELECT kind FROM processing_jobs")] == [FIRMS_POLL]
    assert one(pg.engine, "SELECT next_run_at FROM schedules WHERE kind = 'monthly_reconcile'").next_run_at == \
        T0 - timedelta(hours=1)
    assert any("monthly_reconcile" in r.getMessage() and "no handler" in r.getMessage() for r in caplog.records)


def test_two_workers_ticking_together_create_one_job(pg, settings):
    """I4: concurrent ticks on separate connections; one claims, the other neither fails nor duplicates."""
    clock = FakeClock()
    release, started = threading.Event(), threading.Event()

    def slow(ctx):
        started.set()
        assert release.wait(20)
        return "no_change"

    workers = [make_scheduler(pg.new_engine(), settings, clock, worker_id=f"worker-{n}",
                              handlers={FIRMS_POLL: slow}) for n in "ab"]
    workers[0].startup()
    barrier = threading.Barrier(2)

    def go(w):
        barrier.wait()
        return w.tick()

    with ThreadPoolExecutor(2) as ex:
        futures = [ex.submit(go, w) for w in workers]
        finished, _ = wait(futures, timeout=20, return_when=FIRST_COMPLETED)
        assert len(finished) == 1 and next(iter(finished)).result() == []  # the loser returns at once
        assert started.wait(20)
        running = rows(pg.engine, "SELECT * FROM processing_jobs")
        assert len(running) == 1 and running[0].status == "running"
        release.set()
        results = [f.result(timeout=20) for f in futures]
    assert sorted(len(r) for r in results) == [0, 1]
    assert [(r.kind, r.status) for r in rows(pg.engine, "SELECT * FROM processing_jobs")] == [(FIRMS_POLL, "no_change")]


def test_advisory_lock_held_elsewhere_blocks_the_claim(pg, settings):
    clock = FakeClock()
    s = make_scheduler(pg.engine, settings, clock, handlers={FIRMS_POLL: done("no_change")})
    s.startup()
    holder = pg.new_engine().connect()
    assert holder.scalar(text("SELECT pg_try_advisory_lock(CAST(:k AS bigint))"), {"k": lock_key(FIRMS_POLL)})
    assert s.tick() == []
    assert one(pg.engine, "SELECT count(*) AS n FROM processing_jobs").n == 0
    assert one(pg.engine, "SELECT next_run_at FROM schedules WHERE kind = 'firms_poll'").next_run_at == T0  # still due
    holder.execute(text("SELECT pg_advisory_unlock(CAST(:k AS bigint))"), {"k": lock_key(FIRMS_POLL)})
    holder.close()
    assert len(s.tick()) == 1


def test_active_job_row_blocks_the_claim_until_its_lease_expires(pg, settings):
    """The partial unique index path: a live-looking job of a dead worker is never duplicated."""
    clock = FakeClock()
    s = make_scheduler(pg.engine, settings, clock, handlers={FIRMS_POLL: done("no_change")})
    s.startup()
    with pg.engine.begin() as c:
        ghost = c.scalar(text(
            "INSERT INTO processing_jobs (kind, status, trigger, lease_owner, lease_expires_at, created_at) "
            "VALUES ('firms_poll', 'running', 'schedule', 'ghost:1', :lease, :now) RETURNING id"),
            {"lease": T0 + timedelta(minutes=2), "now": T0})
    assert s.tick() == []  # IntegrityError handled as "already running"
    assert one(pg.engine, "SELECT count(*) AS n FROM processing_jobs").n == 1
    clock.advance(minutes=3)
    [new] = s.tick()
    assert job(pg.engine, ghost).status == "abandoned" and job(pg.engine, new).status == "no_change"


def test_killed_worker_is_abandoned_and_a_restarted_worker_recovers(boot, settings, firms):
    """I5: a worker dies mid-job (no heartbeat, session gone); the next tick of a new worker recovers."""
    pg = boot
    clock = FakeClock()
    dead_engine = pg.new_engine()
    dead = make_scheduler(dead_engine, settings, clock, firms, worker_id="host:111")
    dead.startup()
    claim = dead.claim(FIRMS_POLL)
    assert claim is not None
    claim.conn.invalidate()  # the process is killed: its session (and advisory lock) ends ...
    dead_engine.dispose()    # ... and it never finishes or heartbeats
    stuck = job(pg.engine, claim.job_id)
    assert stuck.status == "running"
    assert one(pg.engine, "SELECT next_run_at FROM schedules WHERE kind = 'firms_poll'").next_run_at == \
        T0 + timedelta(hours=3)

    clock.advance(seconds=settings.worker_lease_seconds - 1)
    restarted = make_scheduler(pg.new_engine(), settings, clock, firms, worker_id="host:222")
    assert restarted.tick() == []  # lease not yet expired: nothing to do, no duplicate
    clock.advance(seconds=2)
    [job_id] = restarted.tick()    # tick sweeps the expired lease, the kind is due again, it runs
    old = job(pg.engine, claim.job_id)
    assert old.status == "abandoned" and "host:111" in old.error and old.finished_at == clock.now
    new = job(pg.engine, job_id)
    assert (new.status, new.lease_owner, new.new_count) == ("succeeded", "host:222", 11)


def test_startup_also_abandons_expired_leases(pg, settings):
    clock = FakeClock()
    make_scheduler(pg.engine, settings, clock).startup()
    with pg.engine.begin() as c:
        stale = c.scalar(text(
            "INSERT INTO processing_jobs (kind, status, trigger, lease_owner, lease_expires_at, created_at) "
            "VALUES ('firms_poll', 'running', 'schedule', 'old:1', :lease, :lease) RETURNING id"),
            {"lease": T0 - timedelta(seconds=1)})
        c.execute(text("UPDATE schedules SET next_run_at = :t WHERE kind = 'firms_poll'"), {"t": T0 + timedelta(hours=2)})
    make_scheduler(pg.engine, settings, clock, worker_id="new:2").startup()
    assert job(pg.engine, stale).status == "abandoned"
    assert one(pg.engine, "SELECT next_run_at FROM schedules WHERE kind = 'firms_poll'").next_run_at == T0


def test_heartbeat_keeps_a_long_job_alive(pg, settings):
    """Real clock: a job that outlives its lease (e.g. a FIRMS budget wait) keeps renewing it."""
    short = replace(settings, worker_lease_seconds=0.9)  # heartbeat every 0.3 s
    started = threading.Event()

    def long_job(ctx):
        started.set()
        threading.Event().wait(2.0)
        return "no_change"

    worker = Scheduler(pg.engine, short, "worker-long", {FIRMS_POLL: long_job})
    watcher = Scheduler(pg.new_engine(), short, "worker-watch", {})
    worker.startup()
    with ThreadPoolExecutor(1) as ex:
        future = ex.submit(worker.tick)
        assert started.wait(10)
        threading.Event().wait(1.4)            # past the initial 0.9 s lease
        assert watcher.abandon_expired() == []
        j = one(pg.engine, "SELECT * FROM processing_jobs")
        assert j.status == "running" and j.heartbeat_at > j.started_at
        assert j.lease_expires_at > j.started_at + timedelta(seconds=0.9)
        [job_id] = future.result(timeout=10)
    assert job(pg.engine, job_id).status == "no_change"


def test_worker_cli_once_against_postgres(pg):
    url = make_url(pg.url).update_query_dict({"options": f"-csearch_path={pg.schema}"})
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FIRMS_", "WORKER_", "RECONCILE_"))}
    env["DATABASE_URL"] = url.render_as_string(hide_password=False)
    r = subprocess.run([sys.executable, "-m", "worker", "--once", "--worker-id", "cli:1"], cwd=BACKEND, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    [j] = rows(pg.engine, "SELECT * FROM processing_jobs")
    assert (j.kind, j.status, j.lease_owner) == (FIRMS_POLL, "failed", "cli:1") and "FIRMS_MAP_KEY" in j.error
    assert {r.kind for r in rows(pg.engine, "SELECT kind FROM schedules")} == {FIRMS_POLL, MONTHLY_RECONCILE}


def test_world_poll_backfill_is_paced_against_the_key_budget(boot, settings, firms):
    """FIRMS_BBOX=world (spec §16): 36 transactions per day per call; a 30-day first backfill of 3
    sources is 3240, plus S-NPP's last 18 uncovered days up to its end (648), so the key's budget is
    checked before the first call and after every BUDGET_CHECK_EVERY transactions. The fetches
    are stored under bbox "world"."""
    from worker.firms_poll import BUDGET_CHECK_EVERY

    pg = boot
    world = replace(settings, firms_bbox="world", firms_max_backfill_days=30, processing_min_lat=None)
    s = make_scheduler(pg.engine, world, FakeClock(), firms)
    s.startup()
    [job_id] = s.tick()
    assert job(pg.engine, job_id).status == "succeeded"
    st = steps(pg.engine, job_id)
    plan = st["planning"].summary
    assert plan["bbox"] == "world" and plan["estimated_transactions"] == 3 * 30 * 36 + 18 * 36 == 3888
    assert plan["budget"] == {"tiles": 18, "transaction_limit": 5000, "limit_window_s": 600, "paced": True,
                              "check_every": BUDGET_CHECK_EVERY}
    costs = [36 * days for _, _, days in firms.windows]
    assert sum(costs) == 3888
    checks, since = 0, None
    for cost in costs:
        if since is None or since + cost > BUDGET_CHECK_EVERY:
            checks, since = checks + 1, 0
        since += cost
    assert st["storage"].summary["budget"]["checks"] == firms.status_calls == checks == 5
    assert all("/world/" in u for u in firms.urls if "/api/area/" in u)
    assert {f.bbox for f in rows(pg.engine, "SELECT bbox FROM firms_fetches")} == {"world"}
    assert st["filter"].summary["rules"]["min_lat"] is None


def test_normal_world_poll_is_not_paced(boot, settings, firms):
    pg = boot
    world = replace(settings, firms_bbox="world")  # 2-day windows: 3 x 72 = 216 transactions
    s = make_scheduler(pg.engine, world, FakeClock(), firms)
    s.startup()
    [job_id] = s.tick()
    plan = steps(pg.engine, job_id)["planning"].summary
    assert plan["estimated_transactions"] == 216 and plan["budget"]["paced"] is False
    assert firms.status_calls == 0
