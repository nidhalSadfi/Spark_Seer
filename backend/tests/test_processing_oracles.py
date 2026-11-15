"""Equivalence with the existing pipeline, which serves as the oracle (spec §5: "keep these identical
to the existing pipeline"). Every input is SYNTHETIC: FIRMS-shaped archive CSVs written to tmp.

(a) processing filter (mask and clip disabled) + aggregate, restricted to MODIS and S-NPP, gives the
    same per (cy, cx, year, month, sensor) counts as the REAL pipeline/ingest.py main().
(b) processing.process over a chosen subset of keys (all before snpp_end) reproduces the REAL
    pipeline/calibrate.harmonize (run on ingest.py's output) for exactly those keys: selected-key
    recompute == full recompute.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from pipeline import calibrate, ingest
from processing.archive import read_firms_csvs
from processing.calibration import Calibration
from processing.coverage import Coverage
from processing.filters import apply_filters
from processing.grid import aggregate, to_ingest_cells
from processing.pipeline import process
from processing_synth import ALL_FAMILIES, NOW, synthetic_archive

KEYS = ["cy", "cx", "year", "month", "sensor"]
KEY4 = ["cy", "cx", "year", "month"]


@pytest.fixture(scope="module")
def archive(tmp_path_factory):
    """Synthetic archive CSVs and the REAL ingest.py output on them (min_lat 34, as the README runs it)."""
    root = tmp_path_factory.mktemp("oracle")
    raw = root / "raw"
    written = synthetic_archive(raw)
    cells_path = root / "cells.parquet"
    ingest.main(str(raw), str(cells_path), 34.0)
    return raw, pd.read_parquet(cells_path), written


def test_a_filter_and_aggregate_equal_ingest_counts(archive):
    raw, expected, written = archive
    kept, summary = apply_filters(read_firms_csvs(raw), mask=None, boundary=None)
    counts, _ = aggregate(kept)
    got = to_ingest_cells(counts)

    exp = expected.astype({"cy": "int64", "cx": "int64", "year": "int64", "month": "int64", "n": "int64"})
    exp = exp.sort_values(KEYS).reset_index(drop=True)[KEYS + ["n"]]
    pd.testing.assert_frame_equal(got, exp, check_dtype=True)

    # The comparison is not vacuous: hundreds of keys, every drop rule exercised, NOAA-20 present
    # in processing (kept for later) but not in ingest.py's output.
    assert len(exp) > 500 and set(exp.sensor) == {"MODIS", "VIIRS"}
    assert summary.input_rows == sum(written.values())
    for reason in ("south_of_min_lat", "low_confidence", "sp_type_nonzero"):
        assert set(summary.dropped[reason]) >= {"MODIS", "SNPP"}, reason
    assert summary.kept_by_family["NOAA20"] > 0 and summary.rules["static_mask"]["status"] == "disabled"
    # The hand-placed edge rows landed where ingest.py puts them.
    assert ((exp.cy == 68) & (exp.year == 2015) & (exp.month == 7)).any()        # latitude exactly 34.0
    assert ((exp.cx == -1) & (exp.year == 2015) & (exp.month == 7)).any()        # longitude -0.0001


def run_process(raw, keys, cal):
    """Archived years: every family fetched for every day, availability past the last month."""
    return process(read_firms_csvs(raw), keys, cal, mask=None, boundary=None,
                   coverage=Coverage.full(ALL_FAMILIES, "2000-11-01", "2021-12-31"),
                   availability={f: date(2021, 12, 31) for f in ALL_FAMILIES}, now=NOW)


def assert_matches_calibrate(staged: pd.DataFrame, expected: pd.DataFrame) -> int:
    m = staged.merge(expected, on=KEY4, how="inner", suffixes=("", "_h"))
    assert len(m) == len(staged) == len(expected)
    pre = (m.year < 2012).to_numpy()
    np.testing.assert_array_equal(m.modis.to_numpy(dtype=float), m.MODIS.to_numpy(dtype=float))
    np.testing.assert_array_equal(m.harmonized.to_numpy(dtype=float), m.harmonized_h.to_numpy(dtype=float))
    np.testing.assert_array_equal(m.k.to_numpy(dtype=float), m.k_h.to_numpy(dtype=float))
    np.testing.assert_array_equal(m.support.to_numpy(dtype=float), m.support_h.to_numpy(dtype=float))
    np.testing.assert_array_equal(m.viirs_ref.to_numpy(dtype=float)[~pre], m.VIIRS.to_numpy(dtype=float)[~pre])
    # Deliberate difference (I7, missing != zero): before 2012 there is no VIIRS, so calibrate.harmonize's
    # VIIRS = 0 is null here. The harmonized value (MODIS x k) is identical.
    assert (m.VIIRS[pre] == 0).all() and m.viirs_ref[pre].isna().all()
    assert (m.reference[pre] == "MODIS*k").all() and (m.reference[~pre] == "SNPP").all()
    return int(pre.sum())


def test_b_selected_keys_equal_full_calibrate_harmonize(archive):
    raw, cells, _ = archive
    expected = calibrate.harmonize(cells)            # the REAL full recompute
    cal = Calibration.from_fit(cells, version="oracle-fit")
    assert expected.year.max() <= 2021 < cal.snpp_end.year
    subset = pd.concat([expected[expected.year < 2012].sample(40, random_state=1),
                        expected[expected.year >= 2012].sample(40, random_state=2)])[KEY4]
    selected = run_process(raw, subset, cal)
    assert selected.passed, [g.detail for g in selected.gates if not g.passed]
    want = expected.merge(subset, on=KEY4)
    assert assert_matches_calibrate(selected.staged, want) == 40

    full = run_process(raw, expected[KEY4], cal)
    assert full.passed
    assert assert_matches_calibrate(full.staged, expected) > 100
    # Selected-key recompute == full recompute, for every staged column.
    restricted = full.staged.merge(subset, on=KEY4).sort_values(KEY4).reset_index(drop=True)
    pd.testing.assert_frame_equal(selected.staged, restricted)
