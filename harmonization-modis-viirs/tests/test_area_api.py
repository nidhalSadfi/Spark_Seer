import csv
import io
from datetime import date
from pathlib import Path

import pytest
import requests

from firms_tool import area_api
from firms_tool.area_api import Client, Window, plan_sensor, windows_for
from firms_tool.cli import main
from firms_tool.sensors import get_sensor

HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight"


class FakeResponse:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text

    def json(self):
        import json
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, params=None, timeout=None):
        self.urls.append(url)
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(area_api.time, "sleep", lambda s: None)


def test_resolve_map_key_prefers_suffixed_var(monkeypatch):
    monkeypatch.setattr(area_api, "ENV_FILE", Path("no-such-env-file"))
    monkeypatch.setenv("FIRMS_MAP_KEY", "fallback")
    monkeypatch.setenv("FIRMS_MAP_KEY_1", "primary")
    assert area_api.resolve_map_key() == "primary"
    monkeypatch.delenv("FIRMS_MAP_KEY_1")
    assert area_api.resolve_map_key() == "fallback"
    assert area_api.resolve_map_key("cli") == "cli"


def test_resolve_map_key_reads_env_file_when_unset(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nexport FIRMS_MAP_KEY_1=\"fromfile\"\nOTHER=ignored\nFIRMS_MAP_KEY=filefallback\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(area_api, "ENV_FILE", env_file)
    monkeypatch.delenv("FIRMS_MAP_KEY_1", raising=False)
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    assert area_api.resolve_map_key() == "fromfile"
    monkeypatch.setenv("FIRMS_MAP_KEY_1", "already")
    assert area_api.resolve_map_key() == "already"


def test_windows_are_at_most_five_days():
    ws = windows_for("MODIS_SP", date(2020, 1, 1), date(2020, 12, 31))
    assert len(ws) == 74 and all(w.days <= 5 for w in ws)
    assert ws[-1].end == date(2020, 12, 31)


def test_auto_mode_switches_from_sp_to_nrt():
    avail = {"VIIRS_NOAA20_SP": (date(2018, 4, 1), date(2026, 2, 28)),
             "VIIRS_NOAA20_NRT": (date(2026, 1, 1), date(2026, 11, 13))}
    ws = plan_sensor(get_sensor("viirs_noaa20"), date(2026, 2, 20), date(2026, 3, 10), "auto", avail)
    assert {w.source for w in ws} == {"VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT"}
    assert max(w.end for w in ws if w.source.endswith("SP")) == date(2026, 2, 28)
    assert min(w.start for w in ws if w.source.endswith("NRT")) == date(2026, 3, 1)


def test_plan_clips_to_sensor_coverage():
    ws = plan_sensor(get_sensor("viirs_noaa21"), date(2020, 1, 1), date(2023, 3, 3), "sp", None)
    assert ws[0].start == date(2023, 3, 1)


def test_retry_then_success_and_key_redacted_in_errors():
    s = FakeSession([FakeResponse(503, "busy"), FakeResponse(200, HEADER + "\n1,2,3,4,5,2020-01-01,0100,N,VIIRS,n,2,6,7,D\n")])
    c = Client("SECRETKEY", session=s, log=lambda m: None)
    assert c.fetch_window(Window("VIIRS_SNPP_SP", date(2020, 1, 1), 1), "0,0,1,1").count("\n") == 2

    bad = Client("SECRETKEY", session=FakeSession([FakeResponse(400, "Invalid MAP_KEY.")]), log=lambda m: None)
    with pytest.raises(area_api.ApiError) as e:
        bad.fetch_window(Window("VIIRS_SNPP_SP", date(2020, 1, 1), 1), "0,0,1,1")
    assert "SECRETKEY" not in str(e.value)


def test_rate_limit_waits_for_budget_then_retries():
    s = FakeSession([
        FakeResponse(200, "Exceeding allowed transaction limit."),
        FakeResponse(200, '{"transaction_limit": 5000, "current_transactions": 4990}'),
        FakeResponse(200, '{"transaction_limit": 5000, "current_transactions": 10}'),
        FakeResponse(200, HEADER + "\n"),
    ])
    c = Client("K", session=s, log=lambda m: None)
    assert c.fetch_window(Window("MODIS_SP", date(2020, 1, 1), 5), "world").startswith("latitude")
    assert sum("mapkey_status" in u for u in s.urls) == 2


STATUS_OK = '{"transaction_limit": 5000, "current_transactions": 0}'


def test_call_cost_is_two_per_day_per_60_degree_tile():
    one_day = Window("VIIRS_SNPP_SP", date(2025, 1, 1), 1)
    five_days = Window("VIIRS_SNPP_SP", date(2025, 1, 1), 5)
    assert area_api.call_cost(one_day, "145,-38,146,-37") == 2
    assert area_api.call_cost(five_days, "112,-44,154,-10") == 10
    assert area_api.call_cost(one_day, "0,0,60,60") == 2
    assert area_api.call_cost(one_day, "0,0,61,60") == 4
    assert area_api.call_cost(one_day, "0,0,90,90") == 8
    assert area_api.call_cost(one_day, "-180,-90,0,90") == 18
    assert area_api.call_cost(one_day, "world") == 36
    assert area_api.call_cost(five_days, "world") == 180


def test_download_waits_when_next_call_would_exceed_budget(tmp_path):
    ws = windows_for("MODIS_SP", date(2020, 1, 1), date(2020, 1, 10))
    s = FakeSession([
        FakeResponse(200, '{"transaction_limit": 5000, "current_transactions": 4600}'),
        FakeResponse(200, HEADER + "\n"),
        FakeResponse(200, '{"transaction_limit": 5000, "current_transactions": 4800}'),
        FakeResponse(200, '{"transaction_limit": 5000, "current_transactions": 0}'),
        FakeResponse(200, HEADER + "\n"),
    ])
    c = Client("K", session=s, log=lambda m: None)
    list(area_api.download(c, ws, "world", tmp_path, delay=0, reserve=200))
    assert sum("mapkey_status" in u for u in s.urls) == 3


def test_merge_unions_sp_and_nrt_headers(tmp_path):
    sp = tmp_path / "VIIRS_NOAA20_SP" / "b" / "a.csv"
    nrt = tmp_path / "VIIRS_NOAA20_NRT" / "b" / "b.csv"
    for p in (sp, nrt):
        p.parent.mkdir(parents=True)
    row = "1,2,3,4,5,2026-07-31,0100,N20,VIIRS,n,2,6,7,D"
    sp.write_text(f"{HEADER},type\n{row},0\n")
    nrt.write_text(f"{HEADER}\n{row.replace(',2,6', ',2.0NRT,6')}\n")
    assert area_api.merge([sp, nrt], tmp_path / "out.csv") == 2
    lines = (tmp_path / "out.csv").read_text().splitlines()
    assert lines[0] == f"{HEADER},type,source"
    assert lines[1].endswith(",0,VIIRS_NOAA20_SP") and lines[2].endswith("D,,VIIRS_NOAA20_NRT")


def _fail(message: str) -> None:
    """Fail without interpolating a URL or key into the message."""
    raise AssertionError(message)


def _isolate_map_key(monkeypatch, present: bool) -> None:
    monkeypatch.setattr(area_api, "ENV_FILE", Path("no-such-env-file"))
    monkeypatch.setattr(area_api, "_env_file_cache", None)
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    if present:
        monkeypatch.setenv("FIRMS_MAP_KEY_1", "FAKEKEY")
    else:
        monkeypatch.delenv("FIRMS_MAP_KEY_1", raising=False)


def _path_after_key(url: str, marker: str) -> str:
    prefix = f"https://firms.modaps.eosdis.nasa.gov/{marker}/"
    if not url.startswith(prefix):
        _fail(f"expected keyed {marker} request")
    key, sep, tail = url[len(prefix):].partition("/")
    if sep != "/" or key != "FAKEKEY":
        _fail(f"keyed {marker} request did not use the fake FIRMS_MAP_KEY_1")
    return tail


def _install_fake_client(monkeypatch, responses):
    _isolate_map_key(monkeypatch, present=True)
    fake = FakeSession(responses)

    def factory(map_key, session=None, log=None):
        if map_key != "FAKEKEY":
            _fail("query_viewport did not resolve FIRMS_MAP_KEY_1 to the fake key")
        return Client(map_key, session=fake, log=lambda m: None)

    monkeypatch.setattr(area_api, "Client", factory)
    return fake


AVAIL = (
    "data_id,min_date,max_date\n"
    "VIIRS_SNPP_SP,2012-01-20,2026-02-28\n"
    "VIIRS_SNPP_NRT,2026-01-01,2026-11-13\n"
)
ROW = "1,2,3,4,5,2020-01-01,0100,N,VIIRS,n,2,6,7,D\n"


def test_query_viewport_requests_area_with_fake_key(monkeypatch):
    fake = _install_fake_client(monkeypatch, [
        FakeResponse(200, AVAIL),
        FakeResponse(200, HEADER + "\n" + ROW),
    ])
    text = area_api.query_viewport(
        (0, 0, 1, 1), "viirs_snpp", date(2020, 1, 1), date(2020, 1, 1),
    )
    if len(fake.urls) != 2:
        _fail(f"expected availability plus one area call, got {len(fake.urls)}")
    if _path_after_key(fake.urls[0], "api/data_availability/csv") != "ALL":
        _fail("availability call was not /api/data_availability/csv/<MAP_KEY>/ALL")
    tail = _path_after_key(fake.urls[1], "api/area/csv")
    if tail != "VIIRS_SNPP_SP/0,0,1,1/1/2020-01-01":
        _fail("area call was not /api/area/csv/<MAP_KEY>/VIIRS_SNPP_SP/0,0,1,1/1/2020-01-01")
    shown = Client("FAKEKEY").redact(
        "https://firms.modaps.eosdis.nasa.gov/api/area/csv/FAKEKEY/" + tail
    )
    if shown != "https://firms.modaps.eosdis.nasa.gov/api/area/csv/<MAP_KEY>/" + tail:
        _fail("redacted area URL did not keep <MAP_KEY>")
    if "FAKEKEY" in shown or "FAKEKEY" in text:
        _fail("viewport result or redacted URL still contains the fake key")
    rows = list(csv.DictReader(io.StringIO(text)))
    if len(rows) != 1 or rows[0].get("source") != "VIIRS_SNPP_SP":
        _fail("viewport CSV did not return the area row tagged with its source")


def test_query_viewport_splits_at_five_days(monkeypatch):
    fake = _install_fake_client(monkeypatch, [
        FakeResponse(200, HEADER + "\n" + ROW),
        FakeResponse(200, HEADER + "\n" + ROW),
    ])
    text = area_api.query_viewport(
        "0,0,1,1", "viirs_snpp", date(2020, 1, 1), date(2020, 1, 6), mode="sp",
    )
    if len(fake.urls) != 2:
        _fail(f"expected two area calls, got {len(fake.urls)}")
    tails = [_path_after_key(url, "api/area/csv") for url in fake.urls]
    if tails != [
        "VIIRS_SNPP_SP/0,0,1,1/5/2020-01-01",
        "VIIRS_SNPP_SP/0,0,1,1/1/2020-01-06",
    ]:
        _fail("day range was not split into a 5-day window and a 1-day window")
    if len(list(csv.DictReader(io.StringIO(text)))) != 2:
        _fail("merged viewport CSV did not keep both windows")


def test_query_viewport_requires_map_key(monkeypatch):
    _isolate_map_key(monkeypatch, present=False)

    def _no_client(*_args, **_kwargs):
        _fail("missing key must not build a FIRMS client")

    monkeypatch.setattr(area_api, "Client", _no_client)
    with pytest.raises(area_api.ApiError, match="FIRMS_MAP_KEY_1"):
        area_api.query_viewport((0, 0, 1, 1), "viirs_snpp", date(2020, 1, 1), date(2020, 1, 1), mode="sp")


def test_query_viewport_rejects_world(monkeypatch):
    _isolate_map_key(monkeypatch, present=False)

    def _no_client(*_args, **_kwargs):
        _fail("world extent must not call FIRMS")

    monkeypatch.setattr(area_api, "Client", _no_client)
    for bbox in ("world", (-180, -90, 180, 90)):
        with pytest.raises(ValueError, match="public /data/country archive"):
            area_api.query_viewport(bbox, "viirs_snpp", date(2020, 1, 1), date(2020, 1, 1))


def test_status_errors_redact_key():
    denied = Client(
        "FAKEKEY",
        session=FakeSession([FakeResponse(500, "bad FAKEKEY")]),
        log=lambda m: None,
    )
    with pytest.raises(area_api.ApiError) as exc:
        denied.status()
    if "FAKEKEY" in str(exc.value) or "<MAP_KEY>" not in str(exc.value):
        _fail("mapkey_status error text was not redacted")

    class Boom:
        def get(self, url, params=None, timeout=None):
            raise requests.ConnectionError("failed MAP_KEY=FAKEKEY")

    broken = Client("FAKEKEY", session=Boom(), log=lambda m: None)
    with pytest.raises(area_api.ApiError) as exc2:
        broken.status()
    if "FAKEKEY" in str(exc2.value) or "<MAP_KEY>" not in str(exc2.value):
        _fail("mapkey_status connection error was not redacted")


def test_viewport_dry_run_prints_redacted_url(capsys, monkeypatch):
    _isolate_map_key(monkeypatch, present=True)
    main([
        "viewport", "--sensor", "viirs_snpp", "--bbox", "0,0,1,1",
        "--start", "2020-01-01", "--end", "2020-01-01", "--mode", "sp", "--dry-run",
    ])
    captured = capsys.readouterr()
    if "FAKEKEY" in captured.out or "FAKEKEY" in captured.err:
        _fail("viewport dry-run printed the fake key")
    expected = (
        "https://firms.modaps.eosdis.nasa.gov/api/area/csv/<MAP_KEY>/"
        "VIIRS_SNPP_SP/0,0,1,1/1/2020-01-01"
    )
    if captured.out.strip() != expected:
        _fail("viewport dry-run did not print the redacted /api/area URL")


def test_viewport_help_documents_integration_key(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["viewport", "--help"])
    if exc.value.code not in (0, None):
        _fail("viewport --help should exit successfully")
    text = capsys.readouterr().out
    if "FIRMS_MAP_KEY_1" not in text or "never printed" not in text or "<MAP_KEY>" not in text:
        _fail("viewport help does not document FIRMS_MAP_KEY_1 and redaction")
    if "final integration" not in text.lower() and "Final integration" not in text:
        _fail("viewport help does not name the final integration")


def test_viewport_command_rejects_world(monkeypatch):
    _isolate_map_key(monkeypatch, present=True)
    with pytest.raises(SystemExit, match="public /data/country archive"):
        main([
            "viewport", "--sensor", "viirs_snpp", "--bbox", "world",
            "--start", "2020-01-01", "--end", "2020-01-01", "--dry-run",
        ])


def test_download_resumes_and_merges(tmp_path):
    ws = windows_for("VIIRS_SNPP_SP", date(2020, 1, 1), date(2020, 1, 10))
    row = "1,2,3,4,5,2020-01-01,0100,N,VIIRS,n,2,6,7,D"
    c1 = Client("K", session=FakeSession([FakeResponse(200, STATUS_OK), FakeResponse(200, f"{HEADER}\n{row}\n")]),
                log=lambda m: None)
    gen = area_api.download(c1, ws, "0,0,1,1", tmp_path, delay=0)
    next(gen)  # first window fetched, then "crash"

    c2 = Client("K", session=FakeSession([FakeResponse(200, STATUS_OK), FakeResponse(200, f"{HEADER}\n{row}\n{row}\n")]),
                log=lambda m: None)
    results = list(area_api.download(c2, ws, "0,0,1,1", tmp_path, delay=0))
    assert [cached for *_, cached in results] == [True, False]
    total = area_api.merge([p for _, p, _, _ in results], tmp_path / "out.csv")
    assert total == 3
    lines = (tmp_path / "out.csv").read_text().splitlines()
    assert lines[0].endswith(",source") and lines[1].endswith(",VIIRS_SNPP_SP")
