"""Hard gates G1-G4, the drift monitor and soft flags (spec §5-§6) on SYNTHETIC inputs: each gate
passes on a good run and fails on a crafted bad input."""

import dataclasses
from datetime import date

import numpy as np
import pandas as pd
import pytest

from processing.calibration import Calibration
from processing.coverage import Coverage
from processing.drift import drift_monitor
from processing.filters import apply_filters
from processing.gates import (gate_conservation, gate_frozen_calibration, gate_missing_not_zero, gate_schema,
                              run_gates, soft_flags)
from processing.grid import aggregate
from processing.harmonize import harmonize
from processing.params import ProcessingParams
from processing.pipeline import process
from processing_synth import ALL_FAMILIES, NOW, det, frame

SNPP_END = date(2026, 11, 2)
K_TABLE = pd.DataFrame({"cy": [73, 74], "cx": [18, 19], "month": [7, 7], "k": [2.5, 3.0], "support": [100.0, 40.0]})
CAL = Calibration(version="cal-1", k_table=K_TABLE, k_month={7: 2.0}, snpp_end=SNPP_END, r={12: 1.1}, r_global=1.02)
COV = Coverage.full(ALL_FAMILIES, "2000-11-01", "2026-11-13")
AVAIL = {f: date(2026, 11, 13) for f in ALL_FAMILIES}
KEYS = [(73, 18, 2010, 7), (73, 18, 2019, 7), (74, 19, 2019, 7), (73, 18, 2026, 11), (75, 20, 2019, 7)]


def detections():
    rows = [det("MODIS_SP", 36.6, 9.1, "2010-07-0%d" % d) for d in (1, 2, 3)]
    rows += [det("MODIS_SP", 36.6, 9.1, "2019-07-05")] * 10 + [det("VIIRS_SNPP_SP", 36.6, 9.1, "2019-07-06")] * 30
    rows += [det("MODIS_SP", 37.1, 9.6, "2019-07-05")] * 5 + [det("VIIRS_SNPP_SP", 37.1, 9.6, "2019-07-06")] * 20
    rows += [det("VIIRS_NOAA20_SP", 37.1, 9.6, "2019-07-06")] * 4 + [det("VIIRS_NOAA21_NRT", 37.1, 9.6, "2019-07-06")]
    rows += [det("VIIRS_SNPP_NRT", 36.6, 9.1, "2026-11-01"), det("VIIRS_NOAA20_NRT", 36.6, 9.1, "2026-11-05")]
    rows += [det("MODIS_SP", 36.6, 9.1, "2019-07-05", confidence="10")]  # filtered out
    return frame(rows)


@pytest.fixture(scope="module")
def good():
    return process(detections(), KEYS, CAL, mask=None, boundary=None, coverage=COV, availability=AVAIL, now=NOW)


def kept_rows():
    return apply_filters(detections(), mask=None, boundary=None)[0]


def test_good_run_passes_every_gate(good):
    assert [g.name for g in good.gates] == ["G1_schema_range", "G2_conservation", "G3_missing_not_zero",
                                            "G4_frozen_calibration"]
    assert good.passed, [g.detail for g in good.gates if not g.passed]
    g2 = good.gates[1].detail["families"]
    assert g2 == {"MODIS": {"staged": 18, "passed_filter": 18}, "SNPP": {"staged": 51, "passed_filter": 51},
                  "NOAA20": {"staged": 5, "passed_filter": 5}, "NOAA21": {"staged": 1, "passed_filter": 1}}


@pytest.mark.parametrize("mutate,failure", [
    (lambda s: pd.concat([s, s.iloc[[1]]], ignore_index=True), "duplicate_key"),
    (lambda s: s.drop(index=2), "affected_key_missing"),
    (lambda s: s.assign(cy=s.cy.where(s.index != 0, 99)), "unexpected_key"),
    (lambda s: s.assign(modis=s.modis.astype("Float64").where(s.index != 1, -1.0)), "modis_not_count"),
    (lambda s: s.assign(modis=s.modis.astype("Float64").where(s.index != 1, 2.5)), "modis_not_count"),
    (lambda s: s.assign(modis_days=s.modis_days.where(s.index != 1, 32)), "modis_days_not_count"),
    (lambda s: s.assign(n_noaa21=s.n_noaa21.where(s.index != 1, -3)), "n_noaa21_not_count"),
    (lambda s: s.assign(harmonized=s.harmonized.where(s.index != 1, -0.5)), "harmonized_out_of_range"),
    (lambda s: s.assign(k=s.k.where(s.index != 1, np.nan)), "k_out_of_range"),
    (lambda s: s.assign(is_partial=pd.array([pd.NA] * len(s), dtype="boolean")), "is_partial_not_bool"),
    (lambda s: s.assign(reference="VIIRS"), "reference_unknown"),
    (lambda s: s.assign(quality_flags=[["made_up"]] * len(s)), "unknown_flag"),
])
def test_g1_fails_on_bad_schema_or_range(good, mutate, failure):
    result = gate_schema(mutate(good.staged.copy()), KEYS)
    assert not result.passed and failure in result.detail["failures"], result.detail


def test_g1_missing_column():
    result = gate_schema(pd.DataFrame({"cy": [1]}), [(1, 1, 2020, 1)])
    assert not result.passed and "missing_columns" in result.detail["failures"]


def staged_from(kept):
    counts, _ = aggregate(kept, KEYS, split_date=SNPP_END)
    return harmonize(counts, KEYS, CAL, COV)[0]


def test_g2_catches_a_dropped_or_duplicated_detection():
    kept = kept_rows()
    assert gate_conservation(staged_from(kept), kept, KEYS).passed
    dropped = gate_conservation(staged_from(kept.drop(index=5)), kept, KEYS)
    assert not dropped.passed and dropped.detail["families"]["MODIS"] == {"staged": 17, "passed_filter": 18}
    duplicated = gate_conservation(staged_from(pd.concat([kept, kept.iloc[[20]]])), kept, KEYS)
    assert not duplicated.passed and duplicated.detail["failures"] == {"SNPP_mismatch": {"rows": 1}}


def test_g2_catches_a_null_count_hiding_detections(good):
    bad = good.staged.assign(n_noaa20=good.staged.n_noaa20.where(good.staged.cy != 74, pd.NA))
    assert not gate_conservation(bad, kept_rows(), KEYS).passed


SNPP_DARK = Coverage.from_windows([(f, "2000-11-01", 9500) for f in ("MODIS", "NOAA20", "NOAA21")])


def honest_dark():
    """Rows harmonized while S-NPP has no fetched day at all (MODIS, NOAA-20 and NOAA-21 do)."""
    counts, _ = aggregate(kept_rows(), KEYS, split_date=SNPP_END)
    return harmonize(counts, KEYS, CAL, SNPP_DARK)[0].assign(is_partial=False)


def test_g3_passes_honest_nulls_when_the_reference_is_dark():
    staged = honest_dark()
    post = staged[staged.year == 2019]
    assert post.n_snpp.isna().all() and post.viirs_ref.isna().all() and post.harmonized.isna().all()
    assert gate_missing_not_zero(staged, CAL, SNPP_DARK).passed


@pytest.mark.parametrize("mutate,failure", [
    (lambda s, sel: s.assign(n_snpp=s.n_snpp.where(~sel, 0)), "n_snpp_not_null_without_coverage"),
    (lambda s, sel: s.assign(harmonized=s.harmonized.where(~sel, 0.0)), "value_without_reference"),
    (lambda s, sel: s.assign(viirs_ref=s.viirs_ref.where(~sel, 0.0)), "value_without_reference"),
    (lambda s, sel: s.assign(quality_flags=[[] for _ in range(len(s))]), "reference_missing_unflagged"),
])
def test_g3_catches_zero_for_missing_reference(mutate, failure):
    staged = honest_dark()
    sel = (staged.year == 2019).to_numpy()
    result = gate_missing_not_zero(mutate(staged, sel), CAL, SNPP_DARK)
    assert not result.passed and failure in result.detail["failures"], result.detail


def test_g3_catches_a_value_without_approved_r(good):
    nov = (good.staged.year == 2026).to_numpy()
    # CAL has an approved r (November falls back to r_global): 1 S-NPP + 1.02 x 1 NOAA-20 detection.
    assert good.staged.harmonized[nov].tolist() == [pytest.approx(2.02)]
    assert gate_missing_not_zero(good.staged, CAL, COV).passed
    no_r = Calibration(version="cal-1", k_table=K_TABLE, k_month={7: 2.0}, snpp_end=SNPP_END)
    result = gate_missing_not_zero(good.staged, no_r, COV)  # the same rows claimed under a calibration without r
    assert not result.passed
    assert set(result.detail["failures"]) == {"value_without_r", "r_missing_unflagged"}
    assert result.detail["failures"]["value_without_r"]["keys"] == [[73, 18, 2026, 11]]


def test_g3_catches_a_wrong_reference(good):
    bad = good.staged.assign(reference=good.staged.reference.where(good.staged.year != 2019, "NOAA20"))
    result = gate_missing_not_zero(bad, CAL, COV)
    assert not result.passed and "reference_not_calendar" in result.detail["failures"]


def test_g4_catches_another_calibration(good):
    assert gate_frozen_calibration(good.staged, CAL).passed
    other_version = good.staged.assign(calibration_version=["cal-2"] + ["cal-1"] * (len(good.staged) - 1))
    assert "other_version" in gate_frozen_calibration(other_version, CAL).detail["failures"]
    refit = good.staged.assign(k=good.staged.k * 1.01)
    result = gate_frozen_calibration(refit, CAL)
    assert not result.passed and result.detail["failures"]["k_not_from_calibration"]["rows"] == len(KEYS)
    newer = Calibration(version="cal-1", k_table=K_TABLE.assign(k=K_TABLE.k + 0.1), k_month={7: 2.0},
                        snpp_end=SNPP_END)
    assert not gate_frozen_calibration(good.staged, newer).passed


def test_run_gates_returns_json_ready_results(good):
    import json
    gates = run_gates(good.staged, kept_rows(), KEYS, CAL, COV)
    assert all(g.passed for g in gates)
    json.dumps([g.to_dict() for g in gates])


# ---- drift and soft flags -----------------------------------------------------------------------

def test_drift_hand_computed(good):
    d = drift_monitor(good.staged)
    # July 2019, complete: MODIS x k = 10 x 2.5 + 5 x 3.0 + 0 x 2.0 (cell 75,20 uses k_month) = 40;
    # S-NPP = 30 + 20 + 0 = 50 -> |40 - 50| / 50 = 20 %. 2010 (MODIS*k) and partial Nov 2026 are left out.
    assert d.months == [{"month": "2019-07", "cells": 3, "modis_k": 40.0, "viirs_ref": 50.0, "error_pct": 20.0,
                         "flagged": False}]
    tight = drift_monitor(good.staged, ProcessingParams(drift_warn_pct=15))
    assert tight.flagged == ["2019-07"] and tight.warn_pct == 15
    flags = soft_flags(good.staged, tight)
    assert {"flag": "drift_above_warn", "warn_pct": 15, "months": ["2019-07"]} in flags


def test_soft_flags_list_row_flags_and_drop_share_jumps(good):
    flags = {f["flag"]: f for f in good.soft_flags}
    assert flags["partial"]["keys"] == [[73, 18, 2026, 11]]       # November 2026 has not ended
    assert flags["mixed_reference"]["rows"] == 1 and "reference_pending" not in flags
    no_r = Calibration(version="cal-0", k_table=K_TABLE, k_month={7: 2.0}, snpp_end=SNPP_END)
    pending = process(detections(), KEYS, no_r, mask=None, boundary=None, coverage=COV, availability=AVAIL, now=NOW)
    assert pending.passed
    assert {f["flag"]: f for f in pending.soft_flags}["reference_pending"]["keys"] == [[73, 18, 2026, 11]]
    _, f_sum = apply_filters(frame([det("MODIS_NRT", 36.0, 9.0, "2026-11-01")] * 8), mask=None, boundary=None)
    f_sum = dataclasses.replace(f_sum, drop_share={**f_sum.drop_share, "outside_country": 0.2})
    jump = soft_flags(good.staged.iloc[:0], None, f_sum, recent_drop_shares={"outside_country": 0.02})
    assert jump == [{"flag": "drop_share_high", "reason": "outside_country", "share": 0.2, "recent": 0.02,
                     "limit": 0.07}]
    assert soft_flags(good.staged.iloc[:0], None, f_sum, recent_drop_shares={"outside_country": 0.1}) == []
    assert soft_flags(good.staged.iloc[:0], None, f_sum) == []  # no history given: not evaluated
