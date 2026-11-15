"""Schedule arithmetic, advisory-lock keys and log redaction (pure, no database)."""
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

import pytest

from firms_tool.area_api import Client
from worker.log import JsonFormatter
from worker.schedules import lock_key, next_monthly, next_run

UTC = timezone.utc


def dt(*args):
    return datetime(*args, tzinfo=UTC)


def test_interval_next_run_is_claim_time_plus_interval():
    now = dt(2026, 11, 14, 12, 0, 7)
    assert next_run("interval", now, interval_seconds=3 * 3600) == now + timedelta(hours=3)


@pytest.mark.parametrize("now,expected", [
    (dt(2026, 11, 14, 12), dt(2026, 12, 5, 3)),        # after this month's run
    (dt(2026, 11, 5, 2, 59), dt(2026, 11, 5, 3)),      # same day, before 03:00
    (dt(2026, 11, 5, 3), dt(2026, 12, 5, 3)),          # exactly at 03:00: strictly after
    (dt(2026, 12, 20), dt(2027, 1, 5, 3)),             # year rollover
    (dt(2026, 1, 31, 23), dt(2026, 2, 5, 3)),
])
def test_monthly_next_run(now, expected):
    assert next_run("monthly", now, day_of_month=5) == expected


def test_monthly_handles_day_28_in_february_and_other_time_zones():
    assert next_monthly(dt(2027, 2, 1), 28) == dt(2027, 2, 28, 3)
    tunis = timezone(timedelta(hours=1))
    assert next_monthly(datetime(2026, 11, 5, 3, 30, tzinfo=tunis), 5) == dt(2026, 11, 5, 3)  # 02:30 UTC


def test_unknown_cadence():
    with pytest.raises(ValueError):
        next_run("weekly", dt(2026, 11, 14))


def test_lock_key_is_stable_signed_64_bit_and_distinct():
    assert lock_key("firms_poll") == lock_key("firms_poll")
    assert lock_key("firms_poll") != lock_key("monthly_reconcile")
    assert all(-2**63 <= lock_key(k) < 2**63 for k in ("firms_poll", "monthly_reconcile"))


def test_json_log_lines_redact_the_map_key():
    fmt = JsonFormatter(redact=Client("SECRETKEY123", log=lambda m: None).redact, static={"worker_id": "w1"})
    try:
        raise RuntimeError("GET https://firms/api/area/csv/SECRETKEY123/MODIS_NRT failed")
    except RuntimeError:
        record = logging.LogRecord("worker", logging.ERROR, __file__, 1, "fetch %s failed for key %s",
                                   ("MODIS_NRT", "SECRETKEY123"), exc_info=sys.exc_info())
    record.job_id = 7
    line = fmt.format(record)
    assert "SECRETKEY123" not in line
    out = json.loads(line)
    assert out["msg"] == "fetch MODIS_NRT failed for key <MAP_KEY>"
    assert (out["level"], out["worker_id"], out["job_id"]) == ("ERROR", "w1", 7)
    assert "<MAP_KEY>" in out["exc"]
