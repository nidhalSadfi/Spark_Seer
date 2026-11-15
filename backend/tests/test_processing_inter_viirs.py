"""Inter-VIIRS factor r = S-NPP / NOAA-20 (spec §5) on SYNTHETIC counts and archive CSVs."""

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from processing import fit_inter_viirs as cli
from processing.inter_viirs import (OUTAGES_FILE, accept_r, approved_r_from_fit, countries_from_names, fit_inter_viirs,
                                    load_outages, outage_months, validate_inter_viirs)
from processing_synth import archive_row, write_archive_csv

SPEC_OUTAGES = [("SNPP", "2022-07-27", "2022-08-10"), ("SNPP", "2024-05-30", "2024-06-03"),
                ("SNPP", "2024-07-10", "2024-07-15"), ("SNPP", "2024-07-25", "2024-07-28"),
                ("NOAA20", "2023-07-19", "2023-07-22")]
OUTAGE_MONTHS = {(2022, 7), (2022, 8), (2023, 7), (2024, 5), (2024, 6), (2024, 7), (2024, 11)}


def region_counts(table: dict) -> pd.DataFrame:
    """{(year, month): (snpp, noaa20)} -> counts rows (one cell)."""
    rows = []
    for (y, m), (s, n) in table.items():
        rows += [(70, 10, y, m, "SNPP", s), (70, 10, y, m, "NOAA20", n)]
    return pd.DataFrame(rows, columns=["cy", "cx", "year", "month", "family", "n"])


def test_committed_outages_are_exactly_the_spec_list():
    doc = load_outages()
    days = [(o["family"], o["start"], o["end"]) for o in doc["outages"] if "start" in o]
    months = [(o["family"], o["month"]) for o in doc["outages"] if "month" in o]
    assert days == SPEC_OUTAGES and months == [("SNPP", "2024-11")]  # spec §5: S-NPP 2024-11 as a whole month
    assert "S-NPP outage" in next(o["note"] for o in doc["outages"] if "month" in o)
    assert "data-exploration.md" in doc["source"] and "harmonization-modis-viirs" in doc["source"]
    assert outage_months(doc) == OUTAGE_MONTHS
    assert outage_months(doc, families=("NOAA20",)) == {(2023, 7)}


def test_month_level_outage_entries(tmp_path):
    entries = [{"family": "NOAA20", "month": "2024-02", "note": "test"},
               {"family": "SNPP", "start": "2023-12-31", "end": "2024-01-01"}]
    assert outage_months(entries) == {(2024, 2), (2023, 12), (2024, 1)}
    assert outage_months(entries, families=("SNPP",)) == {(2023, 12), (2024, 1)}
    for bad in ({"family": "SNPP", "month": "2024-13"}, {"family": "SNPP", "start": "2024-01-01"},
                {"family": "SNPP", "month": "2024-01", "start": "2024-01-01", "end": "2024-01-02"},
                {"family": "SNPP", "start": "2024-01-02", "end": "2024-01-01"}, {"month": "2024-01"}):
        path = tmp_path / "outages.json"
        path.write_text(json.dumps({"source": "test", "outages": [bad]}))
        with pytest.raises(ValueError):
            load_outages(path)


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
    assert rep["excluded_months"] == 7 and rep["excluded_month_list"] == [
        "2022-07", "2022-08", "2023-07", "2024-05", "2024-06", "2024-07", "2024-11"]
    assert rep["train_months"] == 57 and rep["test_months"] == 20
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
    assert rep["excluded_months"] == 7 and rep["train_months"] == 57 and rep["test_months"] == 20
    assert doc["filter"]["dropped"]["low_confidence"] == {"SNPP": 50}
    assert doc["filter"]["dropped"]["south_of_min_lat"] == {"SNPP": 50}
    assert doc["filter"]["rules"]["country_clip"]["status"] == "disabled"
    assert doc["outages"]["file"] == str(OUTAGES_FILE) and len(doc["outages"]["sha256"]) == 64
    assert cli.main([str(tmp_path / "nothing"), str(out)]) == 2



# ----------------------------------------------------------------------------- fit pool (spec §5, 2026-11-15)

def test_validate_on_another_region_hand_checked():
    pool = {(2019, 1): (110, 100), (2019, 2): (220, 200)}
    fit = fit_inter_viirs(region_counts(pool), [], train_years=(2019, 2019), test_years=(2024, 2024), pseudo=20)
    assert fit.r_global == pytest.approx(1.1) and fit.r[1] == pytest.approx(1.1)
    home = {(2024, 1): (55, 50), (2024, 2): (40, 50), (2024, 3): (9, 1)}
    dark = [{"family": "SNPP", "month": "2024-03", "note": "test"}]
    v = validate_inter_viirs(region_counts(home), dark, fit, test_years=(2024, 2024))
    # r = 1: |50-55| = 5, |50-40| = 10 -> mean 7.5 / mean observed 47.5 = 15.8 %.
    # r = 1.1: |55-55| = 0, |55-40| = 15 -> 7.5 / 47.5 = 15.8 %. Per month (both 1.1 here): the same.
    assert (v["error_pct_uncorrected"], v["error_pct_global_ratio"], v["error_pct_month_ratio"]) == (15.8, 15.8, 15.8)
    assert (v["test_months"], v["test_snpp"], v["test_noaa20"], v["excluded_month_list"]) == (2, 95, 100, ["2024-03"])
    acc = accept_r(fit, v)
    assert (acc["approved_r"], acc["approved"]) == (1.0, "r_1")   # a tie keeps r = 1


def _planted(r_true: float) -> dict:
    rng = np.random.default_rng(7)
    table = {}
    for y in range(2019, 2026):
        for m in range(1, 13):
            n = int(rng.poisson(400))
            table[(y, m)] = (int(round(r_true * n)), n)
    return table


def test_acceptance_rule_approves_the_fitted_r_when_it_validates():
    fit = fit_inter_viirs(region_counts(_planted(0.98)), load_outages())
    home = {k: v for k, v in _planted(0.98).items() if k[0] >= 2024}
    acc = accept_r(fit, validate_inter_viirs(region_counts(home), load_outages(), fit))
    assert acc["approved"] == "r_global" and acc["approved_r"] == fit.r_global == pytest.approx(0.98, abs=0.002)
    assert acc["error_pct_r_global"] < acc["error_pct_r1"]
    assert "per-month r is reported, never applied" in acc["rule"]


def test_acceptance_rule_keeps_r_1_when_the_fitted_r_does_worse():
    fit = fit_inter_viirs(region_counts(_planted(0.9)), load_outages())   # the pool says 0.9 ...
    home = {k: v for k, v in _planted(1.0).items() if k[0] >= 2024}       # ... the validation region has none
    acc = accept_r(fit, validate_inter_viirs(region_counts(home), load_outages(), fit))
    assert (acc["approved"], acc["approved_r"], acc["error_pct_r1"]) == ("r_1", 1.0, 0.0)
    assert acc["error_pct_r_global"] > 0 and acc["r_global"] == pytest.approx(0.9, abs=0.002)


def test_acceptance_rule_needs_validation_months():
    fit = fit_inter_viirs(region_counts(_planted(1.0)), [])
    v = validate_inter_viirs(region_counts({(2030, 1): (5, 5)}), [], fit)
    assert v["test_months"] == 0
    with pytest.raises(ValueError, match="no validation months"):
        accept_r(fit, v)


def test_countries_from_file_names():
    names = ["viirs-snpp_2020_Portugal.csv", "a/viirs-jpss1_2019_Spain.csv", "firms_modis_Tunisia_2001-2025.csv",
             "firms_viirs-snpp_Algeria_2012-2025.csv", "viirs-snpp_2021_United_States.csv", "fire_archive_SV-C2_1.csv"]
    # fire_archive_* (a FIRMS area download) names no country.
    assert countries_from_names(names) == ["Algeria", "Portugal", "Spain", "Tunisia", "United States"]


def _viirs_dir(root, country: str, per_month: dict[str, int], years) -> None:
    """Yearly FIRMS country files: per_month = {"N": S-NPP rows, "N20": NOAA-20 rows} each month."""
    for y in years:
        for sat, prefix in (("N", "viirs-snpp"), ("N20", "viirs-jpss1")):
            rows = [archive_row("VIIRS", sat, 38.0, 23.0, date(y, m, 1 + i % 28), confidence="n", type=0,
                                version="2") for m in range(1, 13) for i in range(per_month[sat])]
            write_archive_csv(root / prefix / f"{prefix}_{y}_{country}.csv", rows)


def test_cli_with_validate_dir_fits_the_pool_and_applies_the_rule(tmp_path, capsys):
    pool, home, flat = tmp_path / "pool", tmp_path / "home", tmp_path / "flat"
    _viirs_dir(pool, "Spain", {"N": 33, "N20": 30}, range(2019, 2026))          # r = 1.1 in the pool
    _viirs_dir(pool, "Greece", {"N": 11, "N20": 10}, [2019])
    _viirs_dir(home, "Tunisia", {"N": 22, "N20": 20}, [2024, 2025])             # and at home: r = 1.1 wins
    _viirs_dir(flat, "Algeria", {"N": 20, "N20": 20}, [2024, 2025])             # no difference: r = 1 wins
    out = tmp_path / "r.json"
    assert cli.main([str(pool), str(out), "--validate-dir", str(home)]) == 0
    printed = json.loads(capsys.readouterr().out)
    doc = json.loads(out.read_text())
    assert doc["status"] == "proposed" and doc["r_global"] == pytest.approx(1.1)
    v, acc = doc["validation"], doc["acceptance"]
    # r = 1: |20-22| / 22 = 9.1 %; r = 1.1: 0 %. Test years 2024-2025 minus 4 outage months.
    assert (v["error_pct_uncorrected"], v["error_pct_global_ratio"], v["error_pct_month_ratio"]) == (9.1, 0.0, 0.0)
    assert (v["test_months"], v["countries"], v["files"]) == (20, ["Tunisia"], 4)
    assert (acc["approved"], acc["error_pct_r1"], acc["error_pct_r_global"]) == ("r_global", 9.1, 0.0)
    assert doc["approved_r"] == acc["approved_r"] == doc["r_global"]
    assert approved_r_from_fit(out) == pytest.approx(1.1)
    inp = doc["input"]
    assert inp["countries"] == ["Greece", "Spain"] and inp["files"] == len(inp["file_list"]) == 16
    assert "viirs-snpp/viirs-snpp_2019_Greece.csv" in inp["file_list"] and len(inp["files_sha256"]) == 64
    assert doc["filter"]["kept_by_family"] == {"SNPP": 33 * 12 * 7 + 11 * 12, "NOAA20": 30 * 12 * 7 + 10 * 12}
    assert v["filter"]["kept_by_family"] == {"SNPP": 22 * 24, "NOAA20": 20 * 24}
    assert "country clip" in doc["note"] and doc["filter"]["rules"]["country_clip"]["status"] == "disabled"
    assert printed["acceptance"] == acc and printed["pool"]["countries"] == ["Greece", "Spain"]

    assert cli.main([str(pool), str(out), "--validate-dir", str(flat)]) == 0
    doc2 = json.loads(out.read_text())
    assert (doc2["acceptance"]["approved"], doc2["approved_r"]) == ("r_1", 1.0)
    assert (doc2["acceptance"]["error_pct_r1"], doc2["acceptance"]["error_pct_r_global"]) == (0.0, 10.0)
    assert doc2["input"]["files_sha256"] == inp["files_sha256"]      # same pool, same hash
    assert doc2["validation"]["files_sha256"] != v["files_sha256"]
    assert cli.main([str(pool), str(out), "--validate-dir", str(tmp_path / "nothing")]) == 2


def test_cli_without_validate_dir_writes_what_it_always_did(tmp_path):
    pool = tmp_path / "pool"
    _viirs_dir(pool, "Spain", {"N": 33, "N20": 30}, range(2019, 2026))
    out = tmp_path / "r.json"
    assert cli.main([str(pool), str(out)]) == 0
    doc = json.loads(out.read_text())
    assert sorted(doc) == ["filter", "input", "kind", "outages", "r", "r_global", "report", "status"]
    assert doc["input"] == {"raw_csv_dir": str(pool), "files": 14}
    assert sorted(doc["report"]) == sorted([
        "train_years", "test_years", "pseudo", "r_global", "r", "train_months", "train_snpp", "train_noaa20",
        "test_months", "excluded_months", "excluded_month_list", "australia_reference_r",
        "deviation_from_reference_pct", "error_pct_uncorrected", "error_pct_global_ratio", "error_pct_month_ratio"])
    with pytest.raises(ValueError, match="no approved_r"):
        approved_r_from_fit(out)
    with pytest.raises(ValueError, match="not an inter-VIIRS fit"):
        approved_r_from_fit({"approved_r": 1.0})
