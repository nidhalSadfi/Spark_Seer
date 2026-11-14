"""Grid, monthly counts and the frozen calibration on small SYNTHETIC, hand-computed inputs."""

import dataclasses
from datetime import date

import numpy as np
import pandas as pd
import pytest

from pipeline import calibrate
from processing.calibration import Calibration
from processing.coverage import Coverage
from processing.grid import aggregate, cell_index, to_ingest_cells
from processing_synth import det, frame


def test_cell_index_matches_ingest_floor():
    assert cell_index([36.5, 36.49999, -0.0001, -0.5, 0.0, 37.99]).tolist() == [73, 72, -1, -1, 0, 75]


def counts_of(rows, keys=None, split=None):
    kept = frame(rows).assign(family=lambda d: d.source.map(
        {"MODIS_SP": "MODIS", "VIIRS_SNPP_SP": "SNPP", "VIIRS_NOAA20_NRT": "NOAA20"}))
    return aggregate(kept, keys, split_date=split)


def test_aggregate_n_and_distinct_days():
    rows = [det("MODIS_SP", 36.6, 9.1, "2020-07-01"), det("MODIS_SP", 36.7, 9.2, "2020-07-01"),
            det("MODIS_SP", 36.6, 9.1, "2020-07-03"), det("MODIS_SP", 36.6, 9.1, "2020-08-01"),
            det("VIIRS_SNPP_SP", 36.6, 9.1, "2020-07-31"), det("VIIRS_SNPP_SP", 35.9, -0.2, "2020-07-15")]
    counts, s = counts_of(rows)
    got = {tuple(r[:5]): (r[5], r[6]) for r in counts[["cy", "cx", "year", "month", "family", "n", "days"]]
           .itertuples(index=False)}
    assert got == {(73, 18, 2020, 7, "MODIS"): (3, 2), (73, 18, 2020, 8, "MODIS"): (1, 1),
                   (73, 18, 2020, 7, "SNPP"): (1, 1), (71, -1, 2020, 7, "SNPP"): (1, 1)}
    assert s.affected_keys is None and s.detections_by_family["MODIS"] == 4 and s.detections_outside_keys == 0


def test_aggregate_only_affected_keys_and_split():
    rows = [det("MODIS_SP", 36.6, 9.1, "2026-11-01"), det("MODIS_SP", 36.6, 9.1, "2026-11-02"),
            det("MODIS_SP", 36.6, 9.1, "2026-11-03"), det("MODIS_SP", 36.6, 9.1, "2026-11-03"),
            det("MODIS_SP", 36.6, 9.1, "2026-10-31"), det("VIIRS_SNPP_SP", 37.1, 9.9, "2026-11-01")]
    counts, s = counts_of(rows, keys=[(73, 18, 2026, 11)], split=date(2026, 11, 2))
    assert len(counts) == 1
    row = counts.iloc[0]
    assert (row.n, row.days, row.n_upto, row.days_upto) == (4, 3, 2, 2)
    assert s.affected_keys == 1 and s.detections_outside_keys == 2 and counts.attrs["split_date"] == date(2026, 11, 2)
    empty, _ = counts_of([], keys=[(73, 18, 2026, 11)])
    assert empty.empty and list(empty.columns) == ["cy", "cx", "year", "month", "family", "n", "days", "n_upto",
                                                   "days_upto"]


def test_to_ingest_cells_keeps_modis_and_snpp_only():
    counts = pd.DataFrame({"cy": [1, 1, 1], "cx": [2, 2, 2], "year": [2020] * 3, "month": [7] * 3,
                           "family": ["MODIS", "SNPP", "NOAA20"], "n": [3, 4, 5], "days": [1, 1, 1],
                           "n_upto": [3, 4, 5], "days_upto": [1, 1, 1]})
    assert to_ingest_cells(counts).sensor.tolist() == ["MODIS", "VIIRS"]


# ---- calibration --------------------------------------------------------------------------------

def cells_df():
    """ingest.py-schema counts: cell A busy in July, cell B thin; plus one cell only seen pre-2012."""
    rows = []
    for year in range(2008, 2022):
        rows += [(73, 18, year, 7, "MODIS", 10), (74, 19, year, 7, "MODIS", 1), (60, 1, year, 1, "MODIS", 2)]
        if year >= 2012:
            rows += [(73, 18, year, 7, "VIIRS", 40), (74, 19, year, 7, "VIIRS", 2)]
    rows.append((50, 5, 2005, 3, "MODIS", 4))
    return pd.DataFrame(rows, columns=["cy", "cx", "year", "month", "sensor", "n"])


def test_from_fit_matches_calibrate_fit_and_lookup_falls_back():
    cells = cells_df()
    cal = Calibration.from_fit(cells, version="v-test")
    k_cell, k_month = calibrate.fit(cells, range(2012, 2022))
    # Hand check: July region V = 400 + 20 = 420, M = 100 + 10 = 110 -> k_month 3.818...
    assert cal.k_month[7] == pytest.approx(420 / 110) == k_month[7]
    # Cell (73, 18): (400 + 20 * 420/110) / (100 + 20)
    keys = pd.DataFrame({"cy": [73, 74, 50, 99], "cx": [18, 19, 5, 9], "year": [2010, 2010, 2005, 2010],
                         "month": [7, 7, 3, 7]})
    lk = cal.lookup(keys)
    assert lk.k.iloc[0] == pytest.approx((400 + 20 * 420 / 110) / 120)
    assert lk.k.iloc[1] == pytest.approx((20 + 20 * 420 / 110) / 30)
    assert lk.k.iloc[2] == 1.0 and lk.support.iloc[2] == 0       # March: no k_month either -> 1.0
    assert lk.k.iloc[3] == cal.k_month[7] and lk.support.iloc[3] == 0  # unknown cell -> k_month
    assert lk.support.iloc[0] == 100 and lk.support.iloc[1] == 10
    assert cal.r_status == "missing" and cal.r_for(7) is None and cal.snpp_end == date(2026, 11, 2)


def test_from_harmonized_parquet_reproduces_its_k(tmp_path):
    cells = cells_df()
    h = calibrate.harmonize(cells)
    path = tmp_path / "harmonized.parquet"
    h.to_parquet(path, index=False)
    from_parquet = Calibration.from_harmonized_parquet(path)
    from_fit = Calibration.from_fit(cells, version="fit")
    assert from_parquet.version.startswith("parquet-")
    keys = h[["cy", "cx", "year", "month"]]
    np.testing.assert_array_equal(from_parquet.lookup(keys).k.to_numpy(), h.k.to_numpy())
    np.testing.assert_array_equal(from_parquet.lookup(keys).support.to_numpy(), h.support.to_numpy())
    new = pd.DataFrame({"cy": [99, 73], "cx": [9, 18], "year": [2026, 2026], "month": [7, 2]})
    pd.testing.assert_frame_equal(from_parquet.lookup(new), from_fit.lookup(new))


def test_parquet_with_conflicting_k_is_rejected(tmp_path):
    h = calibrate.harmonize(cells_df())
    h.loc[(h.cy == 73) & (h.year == 2015), "k"] += 1  # one year of a cell-month that spans many years
    path = tmp_path / "bad.parquet"
    h.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="conflicting"):
        Calibration.from_harmonized_parquet(path)


def test_calibration_is_frozen_and_validated():
    cal = Calibration.from_fit(cells_df(), version="v1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cal.version = "v2"
    with pytest.raises(TypeError):
        cal.k_month[7] = 1.0
    with_r = cal.with_inter_viirs({7: 1.03}, 1.02, version="v2")
    assert with_r.r_status == "approved" and with_r.r_for(7) == 1.03 and with_r.r_for(8) == 1.02
    assert with_r.fingerprint != cal.fingerprint and cal.r_status == "missing"
    with pytest.raises(ValueError):
        cal.with_inter_viirs({13: 1.0}, 1.0, version="v3")
    with pytest.raises(ValueError):
        cal.with_inter_viirs({7: 1.0}, None, version="v3")
    with pytest.raises(ValueError):
        Calibration(version="", k_table=cal.k_table, k_month={}, snpp_end=None)
    json_ready = cal.summary()
    assert json_ready["version"] == "v1" and json_ready["r_status"] == "missing"


# ---- coverage -----------------------------------------------------------------------------------

def test_coverage_merges_windows_and_counts_days():
    cov = Coverage.from_windows([("MODIS", "2026-10-16", 5), ("MODIS", date(2026, 10, 21), 5),
                                 ("MODIS", "2026-10-24", 3), ("SNPP", "2026-10-01", 1)])
    assert cov.of("MODIS") == ((date(2026, 10, 16), date(2026, 10, 26)),)
    assert cov.covered_days("MODIS", date(2026, 10, 1), date(2026, 10, 31)) == 11
    assert cov.covered_days("NOAA20", date(2026, 10, 1), date(2026, 10, 31)) == 0
    assert cov.summary()["SNPP"] == {"days": 1, "intervals": [["2026-10-01", "2026-10-01"]]}
    with pytest.raises(ValueError):
        Coverage.from_windows([("GOES", "2026-10-01", 1)])
    with pytest.raises(ValueError):
        Coverage.from_windows([("MODIS", "2026-10-01", 0)])
