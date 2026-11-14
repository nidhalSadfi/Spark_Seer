"""`python -m worker` failure modes and the API/worker import boundary (subprocesses, no database)."""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
WORKER_VARS = ("DATABASE_URL", "FIRMS_MAP_KEY", "FIRMS_MAP_KEY_1", "FIRMS_POLL_INTERVAL_HOURS", "FIRMS_BBOX",
               "FIRMS_MAX_BACKFILL_DAYS", "FIRMS_RAW_DIR", "WORKER_LEASE_SECONDS", "WORKER_TICK_SECONDS",
               "RECONCILE_DAY_OF_MONTH")


def run_worker(*args, **env):
    clean = {k: v for k, v in os.environ.items() if k not in WORKER_VARS}
    return subprocess.run([sys.executable, "-m", "worker", "--once", *args], cwd=BACKEND, env={**clean, **env},
                          capture_output=True, text=True, timeout=60)


def test_missing_database_url_is_a_clear_error():
    r = run_worker()
    assert r.returncode == 2
    assert "DATABASE_URL is not set" in r.stdout and "Traceback" not in r.stdout + r.stderr


def test_unreachable_database_is_a_clear_error_without_the_password():
    # Port 1 on localhost refuses at once; no network involved.
    r = run_worker(DATABASE_URL="postgresql+psycopg://emberguard:s3cret@127.0.0.1:1/emberguard",
                   FIRMS_MAP_KEY="SECRETKEY123")
    assert r.returncode == 1
    out = r.stdout + r.stderr
    assert "cannot connect to the database" in out and "Traceback" not in out
    assert "s3cret" not in out and "SECRETKEY123" not in out


def test_invalid_setting_is_a_clear_error():
    r = run_worker(FIRMS_POLL_INTERVAL_HOURS="0")
    assert r.returncode == 2 and "FIRMS_POLL_INTERVAL_HOURS must be a number > 0" in r.stdout


def test_api_does_not_import_the_worker():
    # Invariant I1: the API process never imports worker, crewai or the database layer.
    code = ("import sys, api.main; bad = sorted(m for m in sys.modules "
            "if m.split('.')[0] in ('worker', 'crewai', 'sqlalchemy', 'psycopg')); assert not bad, bad")
    r = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
