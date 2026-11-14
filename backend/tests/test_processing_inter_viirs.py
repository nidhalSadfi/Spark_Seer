"""Inter-VIIRS factor r = S-NPP / NOAA-20 (spec §5) on SYNTHETIC counts and archive CSVs."""

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from processing import fit_inter_viirs as cli
from processing.inter_viirs import OUTAGES_FILE, fit_inter_viirs, load_outages, outage_months
from processing_synth import archive_row, write_archive_csv

SPEC_OUTAGES = [("SNPP", "2022-07-27", "2022-08-10"), ("SNPP", "2024-05-30", "2024-06-03"),
                ("SNPP", "2024-07-10", "2024-07-15"), ("SNPP", "2024-07-25", "2024-07-28"),
                ("NOAA20", "2023-07-19", "2023-07-22")]
OUTAGE_MONTHS = {(2022, 7), (2022, 8), (2023, 7), (2024, 5), (2024, 6), (2024, 7)}


def region_counts(table: dict) -> pd.DataFrame:
    """{(year, month): (snpp, noaa20)} -> counts rows (one cell)."""
    rows = []
    for (y, m), (s, n) in table.items():
        rows += [(70, 10, y, m, "SNPP", s), (70, 10, y, m, "NOAA20", n)]
    return pd.DataFrame(rows, columns=["cy", "cx", "year", "month", "family", "n"])


def test_committed_outages_are_exactly_the_spec_list():
    doc = load_outages()
    assert [(o["family"], o["start"], o["end"]) for o in doc["outages"]] == SPEC_OUTAGES
    assert "data-exploration.md" in doc["source"] and "harmonization-modis-viirs" in doc["source"]
    assert outage_months(doc) == OUTAGE_MONTHS
    assert outage_months(doc, families=("NOAA20",)) == {(2023, 7)}


def test_recovers_a_planted_r_and_excludes_outage_months():
    rng = np.random.default_rng(3)
    season = 1 + 4 * np.exp(-((np.arange(1, 13) - 8) ** 2) / 4)
    table = {}
    for y in range(2019, 2026):
        for m in range(1, 13):
            lam = 3000 * season[m - 1]
            table[(y, m)] = (int(rng.poisson(1.02 * lam)), int(rng.poisson(lam)))
    for ym in OUTAGE_MONTHS:  # a dark satellite looks like "no fires"
        s, n = table[ym]
        table[ym] = (s // 3, n) if ym != (2023, 7) else (s, n // 3)
    fit = fit_inter_viirs(region_counts(table), load_outages())
    assert fit.r_global == pytest.approx(1.02, abs=0.005)
    assert all(v == pytest.approx(1.02, abs=0.02) for v in fit.r.values())
    rep = fit.report
    assert rep["excluded_months"] == 6 and rep["excluded_month_list"] == [
        "2022-07", "2022-08", "2023-07", "2024-05", "2024-06", "2024-07"]
    assert rep["train_months"] == 57 and rep["test_months"] == 21
    assert rep["error_pct_month_ratio"] < rep["error_pct_uncorrected"]
    assert abs(rep["deviation_from_reference_pct"]) < 1
    biased = fit_inter_viirs(region_counts(table), [])  # outages not excluded: r pulled away from 1.02
    assert abs(biased.r_global - 1.02) > 3 * abs(fit.r_global - 1.02)


def test_validation_numbers_hand_checked():
    table = {(2019, 1): (120, 100), (2019, 2): (80, 100), (2024, 1): (70, 60), (2024, 2): (24, 30)}
    fit = fit_inter_viirs(region_counts(table), [], train_years=(2019, 2019), test_years=(2024, 2024), pseudo=20)
    # r_global = 200 / 200 = 1; r1 = (120 + 20) / (100 + 20); r2 = (80 + 20) / (100 + 20); others 1.
    assert fit.r_global == 1.0
    assert fit.r[1] == pytest.approx(140 / 120) and fit.r[2] == pytest.approx(100 / 120) and fit.r[7] == 1.0
    rep = fit.report
    # Uncorrected: |60-70| = 10, |30-24| = 6 -> mean 8 / mean observed 47 = 17.0 %. Global r = 1: same.
    # Month ratio: 60 * 140/120 = 70 (error 0), 30 * 100/120 = 25 (error 1) -> 0.5 / 47 = 1.1 %.
    assert (rep["error_pct_uncorrected"], rep["error_pct_global_ratio"], rep["error_pct_month_ratio"]) == \
        (17.0, 17.0, 1.1)
    assert (rep["test_months"], rep["excluded_months"], rep["r_global"]) == (2, 0, 1.0)
    dark = [{"family": "NOAA20", "start": "2024-02-10", "end": "2024-02-11"}]
    fit = fit_inter_viirs(region_counts(table), dark, train_years=(2019, 2019), test_years=(2024, 2024))
    assert (fit.report["test_months"], fit.report["excluded_months"]) == (1, 1)
    assert (fit.report["error_pct_uncorrected"], fit.report["error_pct_month_ratio"]) == (14.3, 0.0)


def test_no_overlap_raises():
    with pytest.raises(ValueError, match="overlap"):
        fit_inter_viirs(region_counts({(2015, 1): (10, 0)}), [])


def test_cli_applies_the_filter_and_writes_the_fit(tmp_path):
    raw = tmp_path / "raw"
    snpp, noaa = [], []
    for y in range(2019, 2026):
        for m in range(1, 13):
            dark = (y, m) == (2022, 7)  # S-NPP outage month: no S-NPP rows at all
            for i in range(31 if not dark else 0):
                snpp.append(archive_row("VIIRS", "N", 36.6, 9.1, date(y, m, 1 + i % 28), confidence="n", type=0,
                                        version="2"))
            for i in range(30):
                noaa.append(archive_row("VIIRS", "N20", 36.6, 9.1, date(y, m, 1 + i % 28), confidence="h", type=0,
                                        version="2"))
    # Rows the shared filter drops: they must not move r.
    snpp += [archive_row("VIIRS", "N", 36.6, 9.1, date(2020, 1, 5), confidence="l", type=0, version="2")] * 50
    snpp += [archive_row("VIIRS", "N", 33.0, 9.1, date(2020, 1, 5), confidence="h", type=0, version="2")] * 50
    write_archive_csv(raw / "viirs-snpp_all_Tunisia.csv", snpp)
    write_archive_csv(raw / "viirs-jpss1_all_Tunisia.csv", noaa)
    out = tmp_path / "r.json"
    assert cli.main([str(raw), str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["status"] == "proposed" and doc["r_global"] == pytest.approx(31 / 30)
    assert all(v == pytest.approx(31 / 30) for v in doc["r"].values())
    rep = doc["report"]
    assert rep["error_pct_month_ratio"] == 0.0 and rep["error_pct_uncorrected"] == round(100 / 31, 1)
    assert rep["excluded_months"] == 6 and rep["train_months"] == 57
    assert doc["filter"]["dropped"]["low_confidence"] == {"SNPP": 50}
    assert doc["filter"]["dropped"]["south_of_min_lat"] == {"SNPP": 50}
    assert doc["filter"]["rules"]["country_clip"]["status"] == "disabled"
    assert doc["outages"]["file"] == str(OUTAGES_FILE) and len(doc["outages"]["sha256"]) == 64
    assert cli.main([str(tmp_path / "nothing"), str(out)]) == 2
