"""The shared filter (spec §5) on small SYNTHETIC detections: every drop reason, the NRT mask rule,
VIIRS confidence variants and how the mask and the clip are reported."""

import json

import pytest

from processing.boundary import load_boundary
from processing.filters import DROP_REASONS, apply_filters
from processing.mask import build_static_mask
from processing.params import ProcessingParams
from processing_synth import det, frame, square_geojson


def run(rows, mask=None, boundary=None, **kw):
    return apply_filters(frame(rows), mask=mask, boundary=boundary, **kw)


def test_each_drop_reason_is_counted_per_family():
    rows = [
        det("GOES_NRT", 36.0, 9.0, "2026-11-01"),                               # unknown_source
        det("MODIS_SP", 33.9, 9.0, "2026-11-01"),                               # south_of_min_lat
        det("VIIRS_SNPP_SP", 33.99999, 9.0, "2026-11-01"),                      # south_of_min_lat
        det("MODIS_SP", 36.0, 9.0, "2026-11-01", confidence="29"),              # low_confidence
        det("VIIRS_NOAA20_NRT", 36.0, 9.0, "2026-11-01", confidence="l"),       # low_confidence
        det("MODIS_SP", 36.0, 9.0, "2026-11-01", type=None),                    # sp_type_missing
        det("VIIRS_SNPP_SP", 36.0, 9.0, "2026-11-01", type=2),                  # sp_type_nonzero
        det("MODIS_SP", 36.0, 9.0, "2026-11-01", type=3),                       # sp_type_nonzero
        det("MODIS_SP", 34.0, 9.0, "2026-11-01", confidence="30"),              # kept: both limits inclusive
        det("VIIRS_NOAA21_NRT", 36.0, 9.0, "2026-11-01"),                       # kept: NRT, no type
    ]
    kept, s = run(rows)
    assert s.input_rows == 10 and s.kept_rows == 2 and s.dropped_total == 8
    assert s.dropped == {
        "unknown_source": {"unknown": 1}, "invalid_coordinates": {},
        "south_of_min_lat": {"MODIS": 1, "SNPP": 1},
        "low_confidence": {"MODIS": 1, "NOAA20": 1},
        "sp_type_missing": {"MODIS": 1}, "sp_type_nonzero": {"MODIS": 1, "SNPP": 1},
        "static_mask": {}, "outside_country": {}}
    assert list(s.dropped) == list(DROP_REASONS)
    assert s.kept_by_family == {"MODIS": 1, "SNPP": 0, "NOAA20": 0, "NOAA21": 1}  # every family seen
    assert sorted(kept.family) == ["MODIS", "NOAA21"]
    assert s.drop_share["low_confidence"] == 0.2
    json.dumps(s.to_dict())


def test_first_failing_rule_wins():
    _, s = run([det("MODIS_SP", 30.0, 9.0, "2026-11-01", confidence="5", type=2)])
    assert s.dropped["south_of_min_lat"] == {"MODIS": 1}
    assert sum(sum(v.values()) for v in s.dropped.values()) == 1


def test_min_lat_is_a_parameter():
    _, s = run([det("MODIS_SP", 33.0, 9.0, "2026-11-01")], params=ProcessingParams(min_lat=30.0))
    assert s.kept_rows == 1


@pytest.mark.parametrize("conf,kept", [
    ("n", True), ("h", True), ("nominal", True), ("high", True), (" N ", True), ("HIGH", True), ("Nominal", True),
    ("l", False), ("low", False), ("x", False), ("", False), (None, False), ("nonsense", False)])
def test_viirs_confidence_variants(conf, kept):
    _, s = run([det("VIIRS_SNPP_NRT", 36.0, 9.0, "2026-11-01", confidence=conf)])
    assert s.kept_rows == int(kept)


@pytest.mark.parametrize("conf,kept", [("30", True), ("100", True), ("29", False), ("0", False),
                                       ("abc", False), (None, False), ("n", False)])
def test_modis_confidence_is_numeric(conf, kept):
    _, s = run([det("MODIS_NRT", 36.0, 9.0, "2026-11-01", confidence=conf)])
    assert s.kept_rows == int(kept)


def test_all_viirs_families_are_kept():
    rows = [det(src, 36.0, 9.0, "2026-11-01") for src in
            ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "VIIRS_SNPP_SP", "VIIRS_NOAA20_SP")]
    kept, s = run(rows)
    assert s.kept_by_family == {"SNPP": 2, "NOAA20": 2, "NOAA21": 1}
    assert list(kept["product"]) == ["NRT", "NRT", "NRT", "SP", "SP"]


def test_nrt_inside_mask_dropped_sp_type0_same_spot_kept():
    flare = [det("VIIRS_SNPP_SP", 36.12345, 9.45678, "2020-01-0%d" % d, type=2) for d in (1, 2, 3)]
    mask = build_static_mask(frame(flare), min_count=3)
    rows = [det("VIIRS_NOAA20_NRT", 36.12345, 9.45678, "2026-11-01"),      # NRT in mask -> dropped
            det("VIIRS_SNPP_SP", 36.12345, 9.45678, "2026-11-01", type=0),  # SP type 0 -> kept
            det("MODIS_NRT", 36.12999, 9.45001, "2026-11-01"),               # same 0.01 square -> dropped
            det("MODIS_NRT", 36.13, 9.45678, "2026-11-01")]                  # next square north -> kept
    kept, s = run(rows, mask=mask)
    assert s.dropped["static_mask"] == {"NOAA20": 1, "MODIS": 1}
    assert list(kept["source"]) == ["VIIRS_SNPP_SP", "MODIS_NRT"]
    assert s.rules["static_mask"]["status"] == "enabled" and s.rules["static_mask"]["version"] == mask.version


def test_mask_and_clip_report_disabled_when_absent():
    _, s = run([det("MODIS_NRT", 36.0, 9.0, "2026-11-01")])
    assert s.rules["static_mask"]["status"] == "disabled" and "STATIC_MASK_FILE" in s.rules["static_mask"]["reason"]
    assert s.rules["country_clip"]["status"] == "disabled"
    assert "COUNTRY_BOUNDARY_FILE" in s.rules["country_clip"]["reason"]


def test_country_clip_on_and_off(tmp_path):
    path = tmp_path / "boundary.geojson"
    path.write_text(json.dumps(square_geojson([
        ({"ISO_A3": "TUN"}, [[8.0, 34.0], [11.0, 34.0], [11.0, 37.5], [8.0, 37.5]]),
        ({"ISO_A3": "-99", "ADM0_A3": "DZA"}, [[-2.0, 34.0], [8.0, 34.0], [8.0, 37.0], [-2.0, 37.0]]),
        ({"ISO_A3": "MAR"}, [[-5.0, 34.0], [-2.0, 34.0], [-2.0, 36.0], [-5.0, 36.0]])])))
    boundary = load_boundary(path)
    rows = [det("MODIS_NRT", 36.0, 9.0, "2026-11-01"),       # Tunisia
            det("MODIS_NRT", 36.0, 3.0, "2026-11-01"),       # Algeria
            det("VIIRS_SNPP_NRT", 35.0, -3.0, "2026-11-01"),  # Morocco strip of the bbox -> removed
            det("VIIRS_SNPP_NRT", 37.8, 9.0, "2026-11-01"),   # sea -> removed
            det("VIIRS_SNPP_NRT", 35.0, -2.0, "2026-11-01"),  # exactly on the Algeria-Morocco edge -> kept
            det("MODIS_NRT", 37.5, 9.0, "2026-11-01")]       # exactly on the coast line -> kept
    kept, s = run(rows, boundary=boundary)
    assert s.dropped["outside_country"] == {"SNPP": 2}
    assert len(kept) == 4
    clip = s.rules["country_clip"]
    assert clip["status"] == "enabled" and clip["codes"] == ["DZA", "TUN"] and clip["features"] == 2
    assert len(clip["sha256"]) == 64 and clip["source"] == "boundary.geojson"
    _, off = run(rows)
    assert off.kept_rows == 6 and off.dropped["outside_country"] == {}


def test_empty_input():
    kept, s = run([])
    assert kept.empty and s.input_rows == 0 and s.kept_rows == 0
    assert all(v == {} for v in s.dropped.values())
