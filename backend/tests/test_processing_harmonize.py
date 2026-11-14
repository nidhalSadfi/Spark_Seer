"""Harmonization and completeness on hand-computed SYNTHETIC fixtures (spec §5, I7)."""

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from processing.calibration import Calibration
from processing.completeness import completeness, month_status
from processing.coverage import Coverage
from processing.filters import apply_filters
from processing.grid import aggregate
from processing.harmonize import STAGED_COLUMNS, harmonize
from processing_synth import ALL_FAMILIES, NOW, det, frame

SNPP_END = date(2026, 11, 2)
K_TABLE = pd.DataFrame({"cy": [73], "cx": [18], "month": [7], "k": [2.5], "support": [100.0]})
CAL = Calibration(version="cal-1", k_table=K_TABLE, k_month={7: 3.0}, snpp_end=SNPP_END,
                  r={11: 1.05, 12: 1.1}, r_global=1.02)
CAL_NO_R = Calibration(version="cal-0", k_table=K_TABLE, k_month={7: 3.0}, snpp_end=SNPP_END)
FULL = Coverage.full(ALL_FAMILIES, "2000-11-01", "2026-12-31")
A = (73, 18)  # cell of (36.6N, 9.1E)


def rows_for(source, day, n, lat=36.6, lon=9.1):
    return [det(source, lat, lon, day) for _ in range(n)]


def run(rows, keys, cal=CAL, coverage=FULL, split=True):
    kept, _ = apply_filters(frame(rows), mask=None, boundary=None)
    counts, _ = aggregate(kept, keys, split_date=cal.snpp_end if split else None)
    staged, summary = harmonize(counts, keys, cal, coverage)
    return staged.set_index(["cy", "cx", "year", "month"]), summary


def test_pre_2012_is_modis_times_k():
    rows = (rows_for("MODIS_SP", "2010-07-01", 2) + rows_for("MODIS_SP", "2010-07-02", 1)
            + rows_for("MODIS_SP", "2010-07-05", 1) + rows_for("MODIS_SP", "2010-07-09", 2, lat=37.1, lon=9.6))
    staged, s = run(rows, [(*A, 2010, 7), (74, 19, 2010, 7)])
    a = staged.loc[(*A, 2010, 7)]
    assert (a.modis, a.modis_days, a.harmonized, a.k, a.support, a.reference) == (4, 3, 10.0, 2.5, 100.0, "MODIS*k")
    assert pd.isna(a.viirs_ref) and pd.isna(a.viirs_days) and a.quality_flags == []
    b = staged.loc[(74, 19, 2010, 7)]  # cell not in the k table -> k_month[7] = 3.0, support 0
    assert (b.modis, b.harmonized, b.k, b.support) == (2, 6.0, 3.0, 0.0)
    assert list(staged.reset_index().columns) == STAGED_COLUMNS
    assert s.by_reference == {"MODIS*k": 2} and s.calibration_version == "cal-1"


def test_snpp_path_from_2012_ignores_noaa20_and_noaa21():
    rows = (rows_for("MODIS_SP", "2019-07-10", 2)
            + [det("VIIRS_SNPP_SP", 36.6, 9.1, f"2019-07-0{d}") for d in range(1, 6)]
            + rows_for("VIIRS_NOAA20_SP", "2019-07-03", 7) + rows_for("VIIRS_NOAA21_NRT", "2019-07-03", 9))
    staged, _ = run(rows, [(*A, 2019, 7)])
    r = staged.loc[(*A, 2019, 7)]
    assert (r.reference, r.viirs_ref, r.harmonized, r.viirs_days, r.modis, r.k) == ("SNPP", 5.0, 5.0, 5, 2, 2.5)
    assert (r.n_snpp, r.n_noaa20, r.n_noaa21) == (5, 7, 9) and r.quality_flags == []


def test_after_snpp_end_noaa20_times_r():
    rows = (rows_for("VIIRS_NOAA20_NRT", "2026-12-01", 6) + rows_for("VIIRS_NOAA20_NRT", "2026-12-09", 4)
            + rows_for("VIIRS_SNPP_NRT", "2026-12-01", 2))  # S-NPP after its end: raw only
    staged, _ = run(rows, [(*A, 2026, 12)])
    r = staged.loc[(*A, 2026, 12)]
    assert r.reference == "NOAA20" and r.viirs_ref == pytest.approx(11.0) and r.harmonized == pytest.approx(11.0)
    assert (r.viirs_days, r.n_noaa20, r.n_snpp) == (2, 10, 2) and r.quality_flags == []


def straddle_rows():
    return (rows_for("VIIRS_SNPP_NRT", "2026-11-01", 2) + rows_for("VIIRS_SNPP_NRT", "2026-11-02", 1)
            + rows_for("VIIRS_SNPP_NRT", "2026-11-03", 1)       # after S-NPP's end: not in the reference
            + rows_for("VIIRS_NOAA20_NRT", "2026-11-01", 5)     # before the switch: not in the reference
            + rows_for("VIIRS_NOAA20_NRT", "2026-11-03", 4) + rows_for("VIIRS_NOAA20_NRT", "2026-11-20", 2))


def test_month_straddling_snpp_end_mixes_and_flags():
    staged, s = run(straddle_rows(), [(*A, 2026, 11)])
    r = staged.loc[(*A, 2026, 11)]
    # S-NPP Nov 1-2: 3 detections on 2 days; NOAA-20 Nov 3-30: 6 detections on 2 days; r[11] = 1.05.
    assert r.reference == "mixed" and r.viirs_ref == pytest.approx(3 + 1.05 * 6) == pytest.approx(9.3)
    assert r.harmonized == pytest.approx(9.3) and r.viirs_days == 4
    assert (r.n_snpp, r.n_noaa20) == (4, 11) and r.quality_flags == ["mixed_reference"]
    assert s.flags["mixed_reference"] == 1


def test_straddle_needs_counts_split_at_snpp_end():
    with pytest.raises(ValueError, match="split_date"):
        run(straddle_rows(), [(*A, 2026, 11)], split=False)


def test_missing_r_gives_null_and_reference_pending_never_zero():
    keys = [(*A, 2026, 11), (*A, 2026, 12), (74, 19, 2026, 12)]  # the last has no detections at all
    staged, s = run(straddle_rows() + rows_for("VIIRS_NOAA20_NRT", "2026-12-04", 3), keys, cal=CAL_NO_R)
    for key in keys:
        r = staged.loc[key]
        assert pd.isna(r.viirs_ref) and pd.isna(r.harmonized), key
        assert "reference_pending" in r.quality_flags
    assert staged.loc[(*A, 2026, 11)].quality_flags == ["mixed_reference", "reference_pending"]
    assert staged.loc[(74, 19, 2026, 12)].n_noaa20 == 0  # the raw count is known (covered): a real zero
    assert s.r_status == "missing" and s.null_harmonized == 3


def test_noaa21_never_contributes():
    base = rows_for("VIIRS_NOAA20_NRT", "2026-12-01", 10)
    with_n21 = base + rows_for("VIIRS_NOAA21_NRT", "2026-12-01", 100)
    a, _ = run(base, [(*A, 2026, 12)])
    b, _ = run(with_n21, [(*A, 2026, 12)])
    assert a.harmonized.iloc[0] == b.harmonized.iloc[0] and b.n_noaa21.iloc[0] == 100


def test_family_without_coverage_is_null_not_zero():
    modis_only = Coverage.full(["MODIS"], "2000-11-01", "2026-12-31")
    staged, s = run(rows_for("MODIS_SP", "2019-07-10", 2), [(*A, 2019, 7)], coverage=modis_only)
    r = staged.loc[(*A, 2019, 7)]
    assert r.modis == 2 and pd.isna(r.n_snpp) and pd.isna(r.n_noaa20) and pd.isna(r.n_noaa21)
    assert pd.isna(r.viirs_ref) and pd.isna(r.harmonized) and pd.isna(r.viirs_days)
    assert r.quality_flags == ["reference_unavailable"]
    no_modis = Coverage.full(["SNPP"], "2000-11-01", "2026-12-31")
    staged, _ = run([], [(*A, 2010, 7)], coverage=no_modis)
    r = staged.loc[(*A, 2010, 7)]
    assert pd.isna(r.modis) and pd.isna(r.harmonized) and r.quality_flags == ["modis_unavailable"]


def test_covered_cell_without_detections_is_a_real_zero():
    staged, _ = run([], [(*A, 2010, 7), (*A, 2019, 7)])
    pre, post = staged.loc[(*A, 2010, 7)], staged.loc[(*A, 2019, 7)]
    assert (pre.modis, pre.harmonized) == (0, 0.0)
    assert (post.modis, post.n_snpp, post.viirs_ref, post.harmonized, post.viirs_days) == (0, 0, 0.0, 0.0, 0)


# ---- completeness -------------------------------------------------------------------------------

AVAIL_NOW = {f: date(2026, 11, 14) for f in ALL_FAMILIES}


def first_poll():
    """The first poll's 30-day backfill: Oct 16 .. Nov 14 for every NRT source."""
    return Coverage.from_windows([(f, date(2026, 10, 16) + timedelta(days=5 * i), 5)
                                  for f in ALL_FAMILIES for i in range(6)])


def test_month_not_ended_is_partial():
    complete, reasons = month_status(2026, 11, FULL, AVAIL_NOW, NOW, SNPP_END)
    assert not complete and reasons[0] == "month_not_ended"


def test_first_poll_backfill_leaves_october_partial():
    complete, reasons = month_status(2026, 10, first_poll(), AVAIL_NOW, NOW, SNPP_END)
    assert not complete
    assert reasons == ["MODIS: 16/31 days covered from 2026-10-01 to 2026-10-31",
                       "SNPP: 16/31 days covered from 2026-10-01 to 2026-10-31"]
    sp = Coverage.from_windows([(f, "2026-10-01", 31) for f in ("MODIS", "SNPP")])  # SP fills October
    assert month_status(2026, 10, sp, AVAIL_NOW, NOW, SNPP_END) == (True, [])


def test_availability_before_month_end_is_partial():
    cov = Coverage.full(["MODIS", "SNPP"], "2026-09-01", "2026-09-30")
    stale = {"MODIS": date(2026, 11, 14), "SNPP": date(2026, 9, 29)}
    assert month_status(2026, 9, cov, stale, NOW, SNPP_END) == (
        False, ["SNPP: availability max_date 2026-09-29 is before 2026-09-30"])
    assert month_status(2026, 9, cov, {"MODIS": date(2026, 9, 30)}, NOW, SNPP_END) == (
        False, ["SNPP: no availability max_date"])
    assert month_status(2026, 9, cov, {"MODIS": "2026-09-30", "SNPP": "2026-09-30"}, NOW, SNPP_END) == (True, [])


def test_straddle_month_expects_snpp_until_its_end_only():
    cov = Coverage.from_windows([("MODIS", "2026-11-01", 30), ("SNPP", "2026-11-01", 2), ("NOAA20", "2026-11-03", 28)])
    avail = {"MODIS": date(2026, 12, 4), "SNPP": SNPP_END, "NOAA20": date(2026, 12, 4)}
    later = datetime(2026, 12, 5, tzinfo=timezone.utc)
    assert month_status(2026, 11, cov, avail, later, SNPP_END) == (True, [])
    short = Coverage.from_windows([("MODIS", "2026-11-01", 30), ("SNPP", "2026-11-01", 1),
                                   ("NOAA20", "2026-11-04", 27)])
    complete, reasons = month_status(2026, 11, short, avail, later, SNPP_END)
    assert not complete and reasons == ["SNPP: 1/2 days covered from 2026-11-01 to 2026-11-02",
                                        "NOAA20: 27/28 days covered from 2026-11-03 to 2026-11-30"]


def test_pre_2012_month_only_needs_modis():
    cov = Coverage.full(["MODIS"], "2010-07-01", "2010-07-31")
    assert month_status(2010, 7, cov, {"MODIS": date(2026, 11, 14)}, NOW, SNPP_END) == (True, [])


def test_month_end_is_judged_in_utc():
    tunis_midnight = datetime(2026, 11, 1, 0, 30, tzinfo=timezone(timedelta(hours=1)))  # Oct 31 23:30 UTC
    cov = Coverage.full(["MODIS", "SNPP"], "2026-10-01", "2026-10-31")
    assert month_status(2026, 10, cov, AVAIL_NOW, tunis_midnight, SNPP_END)[1] == ["month_not_ended"]
    assert month_status(2026, 10, cov, AVAIL_NOW, datetime(2026, 11, 1), SNPP_END) == (True, [])


def test_zero_detection_cell_is_partial_without_coverage_and_complete_with_it():
    keys = [(*A, 2026, 9), (*A, 2026, 10)]
    staged, _ = run([], keys, coverage=first_poll())
    out, s = completeness(staged.reset_index(), first_poll(), AVAIL_NOW, NOW, SNPP_END)
    sep, octo = out.set_index(["cy", "cx", "year", "month"]).loc[(*A, 2026, 9)], \
        out.set_index(["cy", "cx", "year", "month"]).loc[(*A, 2026, 10)]
    assert sep.is_partial and pd.isna(sep.modis) and "partial" in sep.quality_flags  # no fetch in September
    assert octo.is_partial and octo.modis == 0  # half-covered October: a known count, still partial
    assert s.partial_months == 2 and s.partial_rows == 2
    full = Coverage.full(["MODIS", "SNPP"], "2026-09-01", "2026-10-31")
    staged, _ = run([], keys, coverage=full)
    out, s = completeness(staged.reset_index(), full, AVAIL_NOW, NOW, SNPP_END)
    assert not out.is_partial.any() and out.modis.tolist() == [0, 0] and s.complete_months == 2
    assert out.quality_flags.tolist() == [[], []]
