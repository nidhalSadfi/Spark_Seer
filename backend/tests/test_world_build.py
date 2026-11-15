"""Filter parity for the world build (spec §16): the chunked batch path (processing/world.py,
scripts/build_world.py) gives exactly what the live processing functions give in one call.

Oracle: archive.read_firms_csvs -> filters.apply_filters(mask, boundary=None, WORLD_PARAMS) ->
grid.aggregate, and mask.build_static_mask(read_firms_csvs(...)). Every input is SYNTHETIC
(processing_synth plus hand-placed edge rows): NRT rows inside and outside the mask, SP rows without
a type, rows south of 34°N (kept: no latitude cut), an unknown instrument, a cell split across two
country files on the same day, and chunks far smaller than a file.
"""

import json
import zipfile
from datetime import date

import pandas as pd
import pytest

import build_world
import ingest_world
from pipeline import calibrate
from processing.archive import read_firms_csvs
from processing.calibration import Calibration
from processing.filters import apply_filters
from processing.grid import aggregate, to_ingest_cells
from processing.mask import build_static_mask
from processing.world import (WORLD_PARAMS, MaskTally, combine_daily, daily_counts, filter_chunk, iter_chunks,
                              mask_from_tally, mask_tally, merge_filter_summaries, monthly_from_daily)
from processing_synth import archive_row, synthetic_archive, write_archive_csv
from world_regions import cell_regions, continent

SPLIT = date(2016, 7, 15)  # a split date inside a month that has data, so n_upto != n somewhere
FLARE = (31.505, 5.805)    # one 0.01 deg square with type-2 rows (a static source)


def add_edge_rows(raw):
    """Rows the plain SP ingest never sees, in their own files (two 'countries')."""
    d = date(2016, 7, 4)
    flare = [archive_row("VIIRS", "N", FLARE[0] + i * 1e-4, FLARE[1], d, confidence="n", type=2, version="2")
             for i in range(4)]
    nrt_in = [archive_row("VIIRS", "N", FLARE[0], FLARE[1] + 2e-4, date(2016, 7, day), confidence="h", type=None,
                          version="2.0NRT") for day in (20, 21)]
    nrt_out = [archive_row("VIIRS", "N", 36.71, 9.3, date(2016, 7, day), confidence="n", type=None,
                           version="2.0NRT") for day in (10, 20)]
    south = [archive_row("VIIRS", "N", lat, 30.2, d, confidence="h", type=0, version="2") for lat in (-33.6, 0.0, 12.3)]
    no_type = [archive_row("VIIRS", "N", 36.8, 9.1, d, confidence="n", type=None, version="2")]
    # Same cell (73, 18) and same day in two country files: `days` must count the day once.
    border_a = [archive_row("VIIRS", "N", 36.6, 9.1, date(2016, 7, 16), confidence="n", type=0, version="2")]
    border_b = [archive_row("VIIRS", "N", 36.7, 9.2, date(2016, 7, 16), confidence="h", type=0, version="2"),
                archive_row("VIIRS", "N20", 36.7, 9.2, date(2016, 7, 16), confidence="h", type=0, version="2")]
    unknown = [{**archive_row("VIIRS", "N", 36.6, 9.1, d, confidence="n", type=0, version="2"), "instrument": "XYZ"}]
    write_archive_csv(raw / "viirs-snpp_2016_Algeria.csv", flare + nrt_in + nrt_out + south + no_type + border_a
                      + unknown)
    write_archive_csv(raw / "viirs-snpp_2016_Tunisia_edge.csv", border_b)


@pytest.fixture(scope="module")
def archive(tmp_path_factory):
    raw = tmp_path_factory.mktemp("world") / "raw"
    synthetic_archive(raw, years=range(2010, 2026))
    add_edge_rows(raw)
    return raw


def chunked(raw, mask, chunksize=37):
    tallies, sums, daily = MaskTally(), [], []
    for path in sorted(raw.rglob("*.csv")):
        for _, chunk in iter_chunks(path, chunksize):
            tallies.add(mask_tally(chunk))
            kept, s = filter_chunk(chunk, mask=mask, params=WORLD_PARAMS)
            sums.append(s)
            daily.append(daily_counts(kept))
    return tallies, merge_filter_summaries(sums), monthly_from_daily(combine_daily(daily), SPLIT)


def test_chunked_mask_equals_build_static_mask(archive):
    one_shot = build_static_mask(read_firms_csvs(archive))
    tally, _, _ = chunked(archive, mask=None)
    got = mask_from_tally(tally)
    assert got.to_dict() == one_shot.to_dict()
    assert len(got.squares) >= 1 and got.input["typed_rows"] > 100  # the flare square and random typed rows


def test_chunked_filter_and_counts_equal_processing(archive):
    mask = build_static_mask(read_firms_csvs(archive))
    kept, summary = apply_filters(read_firms_csvs(archive), mask=mask, boundary=None, params=WORLD_PARAMS)
    expected, _ = aggregate(kept, split_date=SPLIT, params=WORLD_PARAMS)
    _, merged, got = chunked(archive, mask=mask)
    pd.testing.assert_frame_equal(got, expected, check_dtype=True)
    assert merged.to_dict() == summary.to_dict()
    # Not vacuous: each world rule acted, and nothing was cut at 34N.
    d = summary.dropped
    assert d["static_mask"] == {"SNPP": 2} and d["sp_type_missing"] == {"SNPP": 1}
    assert d["unknown_source"] == {"unknown": 1} and d["south_of_min_lat"] == {}
    assert sum(d["sp_type_nonzero"].values()) > 100 and summary.rules["country_clip"]["status"] == "disabled"
    assert (expected.cy < 0).any() and (expected.n_upto != expected.n).any()
    border = expected[(expected.cy == 73) & (expected.cx == 18) & (expected.year == 2016) & (expected.month == 7)
                      & (expected.family == "SNPP")]
    assert border.days.item() == border_days(kept)


def border_days(kept):
    k = kept[(kept.family == "SNPP") & (kept.latitude // 0.5 == 73) & (kept.longitude // 0.5 == 18)]
    k = k[pd.to_datetime(k.acq_date).dt.to_period("M") == pd.Period("2016-07")]
    return k.acq_date.nunique()


def test_teammate_ingest_equals_processing_on_plain_sp_files(tmp_path):
    """On SP country files (no NRT rows, no missing type, no odd sensor), ingest_world's filters (the
    data person's prepare()) give the same MODIS / S-NPP counts as processing with WORLD_PARAMS."""
    raw = tmp_path / "raw"
    synthetic_archive(raw, years=range(2011, 2017))
    theirs = ingest_world.run(raw, tmp_path / "theirs.parquet", None, workers=1)
    kept, _ = apply_filters(read_firms_csvs(raw), mask=None, boundary=None, params=WORLD_PARAMS)
    ours = to_ingest_cells(aggregate(kept, params=WORLD_PARAMS)[0])
    t = theirs.rename(columns={"lat_i": "cy", "lon_i": "cx"})[["cy", "cx", "year", "month", "sensor", "n"]]
    t = t.astype({c: "int64" for c in ("cy", "cx", "year", "month", "n")})
    t = t.sort_values(["cy", "cx", "year", "month", "sensor"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(ours, t)


def zip_world(raw, world):
    """The CSVs as FIRMS-style yearly all-countries zips under world/raw/<sensor>/."""
    groups = {}
    for p in sorted(raw.rglob("*.csv")):
        kind, year = p.name.split("_")[:2]
        groups.setdefault((kind, year), []).append(p)
    for (kind, year), files in groups.items():
        out = world / "raw" / kind / f"{kind}_{year}_all_countries.zip"
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w") as z:
            for i, f in enumerate(files):
                z.write(f, f"{kind}/{year}/{kind}_{year}_Country{i}.csv")


def test_build_world_end_to_end(archive, tmp_path, monkeypatch):
    world = tmp_path / "world"
    zip_world(archive, world)
    monkeypatch.setattr(build_world, "CHUNK", 50)
    for step in ("mask", "count", "harmonize", "r", "report"):
        assert build_world.main([step, "--world", str(world), "--workers", "1"]) == 0

    # Mask and counts: equal to the one-shot processing functions on the same rows.
    mask = build_static_mask(read_firms_csvs(archive))
    saved = json.loads((world / "static_mask.json").read_text())
    assert saved["squares"] == [list(s) for s in mask.squares] and saved["version"] == mask.version
    kept, summary = apply_filters(read_firms_csvs(archive), mask=mask, boundary=None, params=WORLD_PARAMS)
    expected, _ = aggregate(kept, split_date=WORLD_PARAMS.snpp_end, params=WORLD_PARAMS)
    got = build_world.load_counts(world)
    pd.testing.assert_frame_equal(got, expected)
    assert json.loads((world / "filter_summary.json").read_text())["filter"]["dropped"] == summary.dropped

    # Harmonized parquet: calibrate.harmonize on the same cells, in the shape worker/bootstrap reads.
    h = pd.read_parquet(world / "harmonized.parquet")
    ref = calibrate.harmonize(to_ingest_cells(expected)).sort_values(["cy", "cx", "year", "month"])
    pd.testing.assert_frame_equal(h.drop(columns=["MODIS_days", "VIIRS_days"]).reset_index(drop=True),
                                  ref.reset_index(drop=True), check_dtype=False)
    dtypes = dict(h.dtypes.astype(str))
    assert dtypes.pop("source") in ("object", "str")  # a text column reads back as "object" (pandas 2) or "str" (pandas 3)
    assert dtypes == {
        "cy": "int32", "cx": "int32", "year": "int16", "month": "int8", "MODIS": "float64", "VIIRS": "float64",
        "k": "float64", "k_month": "float64", "support": "float64", "harmonized": "float64",
        "MODIS_days": "int16", "VIIRS_days": "int16"}
    from worker.bootstrap import parquet_rows  # pure function: no database
    rows = parquet_rows(h, calibration_version_id=1)
    assert len(rows) == len(h) and all(r["reference"] in ("MODIS*k", "SNPP") for r in rows)
    Calibration.from_harmonized_parquet(world / "harmonized.parquet", version="t")
    report = json.loads((world / "harmonized_validation.json").read_text())
    assert {k: report[k] for k in ("error_pct_cell_month_ratio", "k_global")} == \
        {k: calibrate.validate(to_ingest_cells(expected))[k] for k in ("error_pct_cell_month_ratio", "k_global")}
    assert "Africa" in report["regions"]["continent"] and report["regions"]["global"]["test_months"] == 24

    # r: the inter_viirs functions on the S-NPP / NOAA-20 counts; bootstrap can read approved_r.
    from processing.inter_viirs import approved_r_from_fit
    fit = json.loads((world / "inter_viirs_fit.json").read_text())
    assert approved_r_from_fit(fit) == fit["acceptance"]["approved_r"]
    assert fit["approved_r"] in (1.0, fit["r_global"])
    assert (world / "VALIDATION.md").read_text().startswith("# World validation")
    steps = json.loads((world / "logs" / "steps.json").read_text())
    assert set(steps) == {"mask", "count", "harmonize", "r", "report"}


def test_regions_cover_every_cell_and_known_places():
    cells = pd.DataFrame({"cy": range(-180, 180, 7), "cx": range(-360, 360, 14)})
    r = cell_regions(cells)
    assert r.continent.notna().all() and r.lat_band.notna().all()
    places = {(48.8, 2.3): "Europe", (37.2, 9.8): "Africa", (29.3, 48.0): "Asia", (5.0, 6.5): "Africa",
              (61.0, 75.0): "Asia", (31.8, -102.3): "North America", (-3.0, -60.0): "South America",
              (-33.9, 151.0): "Oceania", (-75.0, 0.0): "Antarctica", (39.9, 32.8): "Asia", (-19.0, 47.0): "Africa"}
    for (lat, lon), name in places.items():
        assert continent([lat], [lon])[0] == name, (lat, lon)

