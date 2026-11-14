"""Equivalence with the existing pipeline, which serves as the oracle (spec §5: "keep these identical
to the existing pipeline"). Every input is SYNTHETIC: FIRMS-shaped archive CSVs written to tmp.

(a) processing filter (mask and clip disabled) + aggregate, restricted to MODIS and S-NPP, gives the
    same per (cy, cx, year, month, sensor) counts as the REAL pipeline/ingest.py main().
"""

import pandas as pd
import pytest

from pipeline import ingest
from processing.archive import read_firms_csvs
from processing.filters import apply_filters
from processing.grid import aggregate, to_ingest_cells
from processing_synth import synthetic_archive

KEYS = ["cy", "cx", "year", "month", "sensor"]


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
