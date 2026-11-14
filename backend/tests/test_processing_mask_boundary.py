"""Static-source mask and country boundary on SYNTHETIC inputs (no real boundary file exists yet)."""

import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from processing import build_mask
from processing.boundary import BoundaryError, load_boundary
from processing.mask import StaticMask, build_static_mask, load_mask, square_index
from processing_synth import archive_row, det, frame, square_geojson, write_archive_csv

BACKEND = Path(__file__).resolve().parent.parent


# ---- mask ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("deg,index", [
    (0.29, 29), (1.13, 113), (0.07, 7),  # x / 0.01 lands just below the integer without the epsilon
    (36.53, 3653), (36.5299999, 3652), (36.52999, 3652), (-0.01, -1), (-0.00001, -1), (0.0, 0), (9.99999, 999)])
def test_square_index_edges(deg, index):
    assert 0.29 / 0.01 < 29  # the float trap the epsilon exists for
    assert square_index(deg, 0.01) == index


def flare_rows(lat, lon, n, type=2, source="VIIRS_SNPP_SP"):
    return [det(source, lat, lon, f"2019-07-{d + 1:02d}", type=type) for d in range(n)]


def test_min_count_threshold_and_inputs():
    rows = (flare_rows(36.123, 9.456, 3)                              # square (3612, 945): 3 -> masked
            + flare_rows(36.0, 9.0, 2, type=3)                        # 2 -> masked only with min_count 2
            + flare_rows(35.5, 8.5, 5, type=0)                        # type 0 never counts
            + [det("VIIRS_NOAA20_NRT", 35.7, 8.7, "2026-11-01")] * 4  # NRT rows have no type
            + flare_rows(35.2, 7.2, 1, type=1) + flare_rows(35.2, 7.2, 2, type=3, source="MODIS_SP"))
    mask = build_static_mask(frame(rows), min_count=3)
    assert mask.squares == ((3520, 720, 3), (3612, 945, 3))
    assert mask.input["typed_rows"] == 8 and mask.input["by_type"] == {"1": 1, "2": 3, "3": 4}
    assert mask.input["sp_rows"] == 13 and mask.input["candidate_squares"] == 3
    assert mask.version.startswith("mask-")
    loose = build_static_mask(frame(rows), min_count=2)
    assert (3600, 900, 2) in loose.squares and loose.version != mask.version


def test_contains_on_square_edges():
    mask = build_static_mask(frame(flare_rows(36.53, 9.13, 3)), min_count=3)
    assert mask.squares == ((3653, 913, 3),)
    lat = np.array([36.53, 36.53999, 36.5299999, 36.54, 36.535, 36.535])
    lon = np.array([9.13, 9.13999, 9.135, 9.135, 9.12999, 9.14])
    assert mask.contains(lat, lon).tolist() == [True, True, False, False, False, False]
    assert mask.contains(np.array([]), np.array([])).tolist() == []


def test_json_round_trip_and_tamper_check(tmp_path):
    mask = build_static_mask(frame(flare_rows(36.123, 9.456, 4) + flare_rows(-0.005, -0.005, 3)), min_count=3)
    path = tmp_path / "mask.json"
    sha = mask.save(path)
    back = load_mask(path)
    assert back.squares == mask.squares and back.version == mask.version and back.params == mask.params
    assert back.file_sha256 == sha == hashlib.sha256(path.read_bytes()).hexdigest()
    lat, lon = np.array([36.123, -0.001, 36.2]), np.array([9.456, -0.001, 9.4])
    assert back.contains(lat, lon).tolist() == mask.contains(lat, lon).tolist() == [True, True, False]
    assert back.summary()["status"] == "enabled" and back.summary()["squares"] == 2
    doc = json.loads(path.read_text())
    doc["squares"].append([1, 1, 99])
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="content_sha256"):
        StaticMask.load(path)


def write_flare_csvs(raw_dir):
    rows = [archive_row("VIIRS", "N", 36.12345, 9.45678, date(2019, 7, d), confidence="n",
                        type=2, version="2") for d in (1, 2, 3)]
    rows += [archive_row("VIIRS", "N", 35.0, 8.0, date(2019, 7, 1), confidence="h",
                         type=0, version="2")]
    write_archive_csv(raw_dir / "viirs-snpp_2019_Algeria.csv", rows)
    write_archive_csv(raw_dir / "sub" / "modis_2019_Algeria.csv",
                      [archive_row("MODIS", "Terra", 36.2, 9.6, date(2019, 7, 9),
                                   confidence=50, type=3, version="6.1")] * 2)


def test_build_mask_cli(tmp_path, capsys):
    raw = tmp_path / "raw"
    write_flare_csvs(raw)
    out = tmp_path / "mask.json"
    assert build_mask.main([str(raw), str(out)]) == 0
    mask = load_mask(out)
    assert mask.squares == ((3612, 945, 3),)
    printed = json.loads(capsys.readouterr().out)
    assert printed["files"] == 2 and printed["input"]["typed_rows"] == 5
    assert build_mask.main([str(raw), str(out), "--min-count", "2"]) == 0
    assert load_mask(out).squares == ((3612, 945, 3), (3620, 960, 2))
    assert build_mask.main([str(tmp_path / "empty"), str(out)]) == 2


def test_build_mask_module_entry_point(tmp_path):
    raw = tmp_path / "raw"
    write_flare_csvs(raw)
    out = tmp_path / "mask.json"
    subprocess.run([sys.executable, "-m", "processing.build_mask", str(raw), str(out), "--min-count", "3"],
                   cwd=BACKEND, check=True, capture_output=True)
    assert load_mask(out).squares == ((3612, 945, 3),)


# ---- boundary -----------------------------------------------------------------------------------

TUN = [[8.0, 34.0], [11.0, 34.0], [11.0, 37.5], [8.0, 37.5]]
DZA = [[-2.0, 34.0], [8.0, 34.0], [8.0, 37.0], [-2.0, 37.0]]


def write_geojson(tmp_path, features, name="ne_test.geojson", **top):
    path = tmp_path / name
    path.write_text(json.dumps({**square_geojson(features), **top}))
    return path


def test_selects_by_iso_properties_and_tests_points(tmp_path):
    path = write_geojson(tmp_path, [({"ISO_A3": "TUN"}, TUN), ({"ISO_A3": "-99", "ADM0_A3": "DZA"}, DZA),
                                    ({"iso_a3": "MAR"}, [[-5.0, 34.0], [-2.0, 34.0], [-2.0, 36.0], [-5.0, 36.0]])])
    b = load_boundary(path, ("TUN", "DZA"), source="synthetic squares")
    assert b.features == 2 and b.codes == ("DZA", "TUN") and b.source == "synthetic squares"
    assert b.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    lat = np.array([36.0, 35.0, 35.0, 34.0, 37.5, 37.50001, 35.0, 33.99999])
    lon = np.array([9.0, 3.0, -2.0, 8.0, 11.0, 9.0, -3.0, 9.0])
    # inside TUN, inside DZA, on the DZA-MAR edge, on a shared corner, on a TUN corner, sea, MAR, south
    assert b.contains(lat, lon).tolist() == [True, True, True, True, True, False, False, False]
    assert b.summary()["status"] == "enabled"
    morocco = load_boundary(path, ("MAR",))
    assert morocco.contains([35.0], [-3.0]).tolist() == [True]


def test_source_defaults_to_file_metadata_or_name(tmp_path):
    assert load_boundary(write_geojson(tmp_path, [({"ISO_A3": "TUN"}, TUN)]), ("TUN",)).source == "ne_test.geojson"
    named = write_geojson(tmp_path, [({"ISO_A3": "TUN"}, TUN)], name="b.geojson",
                          source="Natural Earth (synthetic stand-in)")
    assert load_boundary(named, ("TUN",)).source == "Natural Earth (synthetic stand-in)"


def test_missing_country_or_bad_file_raises(tmp_path):
    path = write_geojson(tmp_path, [({"ISO_A3": "TUN"}, TUN)])
    with pytest.raises(BoundaryError, match="DZA"):
        load_boundary(path, ("TUN", "DZA"))
    bad = tmp_path / "bad.geojson"
    bad.write_text(json.dumps({"type": "Feature"}))
    with pytest.raises(BoundaryError, match="FeatureCollection"):
        load_boundary(bad, ("TUN",))
    bad.write_text("{not json")
    with pytest.raises(BoundaryError, match="JSON"):
        load_boundary(bad, ("TUN",))


def test_invalid_polygon_is_repaired(tmp_path):
    bowtie = [[8.0, 34.0], [11.0, 37.0], [11.0, 34.0], [8.0, 37.0]]
    b = load_boundary(write_geojson(tmp_path, [({"ISO_A3": "TUN"}, bowtie)]), ("TUN",))
    assert b.repaired and b.contains([35.5, 34.5], [8.5, 9.5]).tolist() == [True, False]
