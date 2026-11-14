"""processing.process end to end on SYNTHETIC detections: determinism, isolation (no DB, worker or
CrewAI imports), frozen calibration (I6), missing != zero (I7) and the JSON-ready summaries."""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from pipeline import calibrate
from processing.boundary import load_boundary
from processing.calibration import Calibration
from processing.mask import build_static_mask
from processing.pipeline import process
from processing_synth import ALL_FAMILIES, NOW, det, frame, square_geojson

BACKEND = Path(__file__).resolve().parent.parent
SNPP_END = date(2026, 11, 2)
CAL = Calibration(version="cal-7", k_table=pd.DataFrame({"cy": [73], "cx": [18], "month": [10], "k": [3.2],
                                                          "support": [55.0]}),
                  k_month={10: 2.9, 11: 3.1}, snpp_end=SNPP_END)
# The first poll: a 30-day NRT backfill Oct 16 .. Nov 14 for every source.
COVERAGE = [(f, date(2026, 10, 16) + pd.Timedelta(days=5 * i).to_pytimedelta(), 5)
            for f in ALL_FAMILIES for i in range(6)]
AVAIL = {f: date(2026, 11, 14) for f in ALL_FAMILIES}
KEYS = [(73, 18, 2026, 10), (73, 18, 2026, 11), (72, 6, 2026, 10), (74, 19, 2026, 11)]


def detections():
    rows = []
    for d in range(16, 32):
        rows += [det("MODIS_NRT", 36.61, 9.12, f"2026-10-{d}"), det("VIIRS_SNPP_NRT", 36.62, 9.13, f"2026-10-{d}")]
        rows += [det("VIIRS_NOAA20_NRT", 36.62, 9.13, f"2026-10-{d}"),
                 det("VIIRS_NOAA21_NRT", 36.62, 9.13, f"2026-10-{d}")]
    rows += [det("VIIRS_SNPP_NRT", 36.7, 9.2, "2026-11-01"), det("VIIRS_NOAA20_NRT", 36.7, 9.2, "2026-11-03")]
    rows += [det("VIIRS_NOAA20_NRT", 31.9, 5.5, "2026-11-03")] * 3          # Saharan gas field: south of 34N
    rows += [det("VIIRS_SNPP_NRT", 36.05, 3.05, "2026-10-20")] * 2          # NRT in the static-source mask
    rows += [det("MODIS_NRT", 36.4, 3.4, "2026-10-21")]                       # (72, 6): MODIS only
    rows += [det("VIIRS_NOAA20_NRT", 37.9, 9.4, "2026-11-04")]                # sea: outside the clip
    return frame(rows)


def mask():
    return build_static_mask(frame([det("VIIRS_SNPP_SP", 36.05, 3.05, f"2019-0{m}-01", type=2) for m in (1, 2, 3)]))


@pytest.fixture
def boundary(tmp_path):
    path = tmp_path / "synthetic_boundary.geojson"
    path.write_text(json.dumps(square_geojson([
        ({"ISO_A3": "TUN"}, [[8.0, 30.0], [11.6, 30.0], [11.6, 37.6], [8.0, 37.6]]),
        ({"ADM0_A3": "DZA"}, [[-2.0, 30.0], [8.0, 30.0], [8.0, 37.1], [-2.0, 37.1]])])))
    return load_boundary(path)


def run(dets=None, **kw):
    args = dict(mask=mask(), boundary=None, coverage=COVERAGE, availability=AVAIL, now=NOW)
    args.update(kw)
    return process(detections() if dets is None else dets, KEYS, CAL, **args)


def test_end_to_end_with_mask_and_clip(boundary):
    res = run(boundary=boundary)
    assert res.passed, [g.detail for g in res.gates if not g.passed]
    f = res.stage_summaries["filter"]
    assert f["dropped"]["south_of_min_lat"] == {"NOAA20": 3} and f["dropped"]["static_mask"] == {"SNPP": 2}
    assert f["dropped"]["outside_country"] == {"NOAA20": 1}
    assert f["rules"]["static_mask"]["status"] == f["rules"]["country_clip"]["status"] == "enabled"
    s = res.staged.set_index(["cy", "cx", "year", "month"])
    octo = s.loc[(73, 18, 2026, 10)]
    assert (octo.modis, octo.n_snpp, octo.n_noaa20, octo.n_noaa21, octo.viirs_ref) == (16, 16, 16, 16, 16.0)
    assert octo.is_partial and octo.k == 3.2   # first poll: October partial, MODIS fetched from Oct 16 only
    assert octo.quality_flags == ["modis_partial", "partial"]
    nov = s.loc[(73, 18, 2026, 11)]
    assert nov.reference == "mixed" and pd.isna(nov.harmonized)  # no approved r yet
    assert nov.quality_flags == ["mixed_reference", "modis_partial", "partial", "reference_pending"]
    assert s.loc[(72, 6, 2026, 10)].modis == 1 and s.loc[(74, 19, 2026, 11)].modis == 0
    json.dumps(res.summary())
    records = res.staged_records()
    assert json.loads(json.dumps(records)) == records and len(records) == len(KEYS)
    nov_rec = next(r for r in records if (r["year"], r["month"], r["cy"]) == (2026, 11, 73))
    assert nov_rec["harmonized"] is None and nov_rec["viirs_ref"] is None  # null, never 0 or NaN


def test_rules_reported_disabled_when_files_are_unset():
    res = run(mask=None)
    rules = res.stage_summaries["filter"]["rules"]
    assert rules["static_mask"]["status"] == "disabled" and rules["country_clip"]["status"] == "disabled"
    assert res.stage_summaries["filter"]["dropped"]["static_mask"] == {}


def test_process_is_deterministic_and_order_independent():
    a, b = run(), run()
    pd.testing.assert_frame_equal(a.staged, b.staged)
    assert json.dumps(a.summary(), sort_keys=True) == json.dumps(b.summary(), sort_keys=True)
    shuffled = run(detections().sample(frac=1, random_state=4))
    pd.testing.assert_frame_equal(a.staged, shuffled.staged)
    assert a.summary() == shuffled.summary()


def test_frozen_calibration_is_never_refit(monkeypatch):
    def refit(*args, **kwargs):
        raise AssertionError("process() must not refit the calibration (I6)")
    monkeypatch.setattr(calibrate, "fit", refit)
    res = run()
    assert res.passed and set(res.staged.calibration_version) == {"cal-7"}
    assert res.stage_summaries["inputs"]["calibration"]["version"] == "cal-7"


def test_missing_reference_stays_null_after_switch_with_approved_r():
    cal_r = CAL.with_inter_viirs({11: 1.05, 12: 1.1}, 1.02, version="cal-8")
    res = process(detections(), KEYS + [(73, 18, 2026, 12)], cal_r, mask=None, boundary=None,
                  coverage=COVERAGE, availability=AVAIL, now=NOW)
    s = res.staged.set_index(["cy", "cx", "year", "month"])
    assert s.loc[(73, 18, 2026, 11)].harmonized == pytest.approx(1 + 1.05 * 1)
    dec = s.loc[(73, 18, 2026, 12)]  # nothing fetched for December yet: unknown, not zero
    assert pd.isna(dec.harmonized) and pd.isna(dec.n_noaa20) and pd.isna(dec.modis)
    assert dec.quality_flags == ["modis_unavailable", "partial", "reference_unavailable"]
    assert res.passed


def test_summary_has_every_stage():
    res = run()
    assert set(res.stage_summaries) == {"inputs", "filter", "aggregate", "harmonize", "completeness", "drift"}
    c = res.stage_summaries["completeness"]["months"]
    assert c["2026-10"]["complete"] is False
    assert c["2026-10"]["reasons"] == ["SNPP: 16/31 days covered from 2026-10-01 to 2026-10-31"]
    assert res.stage_summaries["inputs"]["params"]["min_lat"] == 34.0
    assert res.summary()["passed"] is True


def test_importing_processing_pulls_no_db_worker_or_crewai():
    code = ("import sys, processing, processing.pipeline, processing.build_mask, processing.fit_inter_viirs, "
            "processing.archive\n"
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
            "{'sqlalchemy', 'psycopg', 'psycopg2', 'worker', 'crewai', 'requests', 'httpx', 'settings', 'api'})\n"
            "print(','.join(bad))\nsys.exit(1 if bad else 0)")
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True)
    assert out.returncode == 0, f"processing imported: {out.stdout} {out.stderr}"
