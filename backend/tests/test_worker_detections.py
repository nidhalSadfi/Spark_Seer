"""Natural key, content hash and CSV parsing of FIRMS rows (pure, no database). Fixtures are SYNTHETIC."""
import json
from datetime import date, time

import pytest

from firms_fake import FIXTURES
from worker.detections import FirmsFormatError, content_hash, natural_key, parse_csv, parse_hhmm

MODIS_HEADER = ("latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,"
                "version,bright_t31,frp,daynight")
MODIS_ROW = "36.95120,9.12340,318.40,1.10,1.00,2026-11-12,1015,Terra,MODIS,72,6.1NRT,296.20,14.70,D"


def csv_text(header: str, *rows: str) -> str:
    return "\n".join((header,) + rows) + "\n"


def permuted(header: str, row: str, order: list[int]) -> str:
    h, r = header.split(","), row.split(",")
    return csv_text(",".join(h[i] for i in order), ",".join(r[i] for i in order))


def test_natural_key_uses_trimmed_raw_strings():
    row = {" latitude ": " 36.95120 ", "longitude": "9.12340 ", "acq_date": " 2026-11-12", "acq_time": "1015",
           "satellite": " Terra", "confidence": "72"}
    assert natural_key(" MODIS_NRT ", row) == ("MODIS_NRT", "Terra", "36.95120", "9.12340", "2026-11-12", "1015")


def test_natural_key_keeps_raw_spelling():
    # Raw strings, not parsed numbers: "36.9512" and "36.95120" are different keys by design.
    a = {"latitude": "36.9512", "longitude": "9.1", "acq_date": "2026-11-12", "acq_time": "1015", "satellite": "T"}
    assert natural_key("MODIS_NRT", a) != natural_key("MODIS_NRT", dict(a, latitude="36.95120"))


def test_content_hash_ignores_column_order_and_whitespace():
    row = dict(zip(MODIS_HEADER.split(","), MODIS_ROW.split(",")))
    reordered = dict(reversed(list(row.items())))
    padded = {f" {k} ": f"  {v} " for k, v in row.items()}
    assert content_hash(row) == content_hash(reordered) == content_hash(padded)
    assert len(content_hash(row)) == 64


def test_content_hash_changes_with_any_value_or_column():
    row = dict(zip(MODIS_HEADER.split(","), MODIS_ROW.split(",")))
    base = content_hash(row)
    assert content_hash(dict(row, confidence="73")) != base
    assert content_hash(dict(row, frp="14.71")) != base
    assert content_hash(dict(row, type="0")) != base            # an extra column (SP carries `type`)
    swapped = dict(row, brightness=row["bright_t31"], bright_t31=row["brightness"])
    assert content_hash(swapped) != base                         # same values under other names


def test_parse_is_column_order_independent():
    a = parse_csv(csv_text(MODIS_HEADER, MODIS_ROW), "MODIS_NRT").detections[0]
    b = parse_csv(permuted(MODIS_HEADER, MODIS_ROW, [13, 6, 0, 12, 5, 1, 7, 2, 3, 4, 8, 9, 10, 11]),
                  "MODIS_NRT").detections[0]
    assert (a.key, a.content_hash) == (b.key, b.content_hash)


def test_parse_modis_fixture_typed_fields():
    result = parse_csv((FIXTURES / "MODIS_NRT.csv").read_text(), "MODIS_NRT")
    assert (result.rows, result.malformed, result.duplicates, len(result.detections)) == (5, 0, 0, 5)
    d = result.detections[0]
    assert d.key == ("MODIS_NRT", "Terra", "36.95120", "9.12340", "2026-11-12", "1015")
    assert (d.latitude, d.longitude, d.acq_date, d.acq_time) == (36.9512, 9.1234, date(2026, 11, 12), time(10, 15))
    assert (d.product, d.instrument, d.confidence, d.version, d.frp, d.daynight, d.type) == \
        ("NRT", "MODIS", "72", "6.1NRT", 14.7, "D", None)
    assert d.delivered["bright_t31"] == "296.20" and "type" not in d.delivered
    # Same place, other date/time: a genuine repeat observation, kept as its own row.
    assert len({x.key for x in result.detections}) == 5


def test_parse_viirs_fixture():
    result = parse_csv((FIXTURES / "VIIRS_NOAA20_NRT.csv").read_text(), "VIIRS_NOAA20_NRT")
    assert len(result.detections) == 5
    assert {d.confidence for d in result.detections} == {"l", "n", "h"}
    assert {d.satellite for d in result.detections} == {"N20"}
    assert all(d.type is None and d.product == "NRT" for d in result.detections)


def test_sp_rows_keep_type():
    text = csv_text(MODIS_HEADER + ",type", MODIS_ROW + ",2")
    d = parse_csv(text, "MODIS_SP").detections[0]
    assert (d.product, d.type) == ("SP", 2)


def test_malformed_rows_are_counted_and_skipped():
    text = csv_text(
        MODIS_HEADER, MODIS_ROW,
        "36.1,9.1,318.4",                                                         # too few fields
        MODIS_ROW + ",extra",                                                     # too many fields
        MODIS_ROW.replace("36.95120", "north"),                                   # latitude not a number
        MODIS_ROW.replace("36.95120", "95.0"),                                    # latitude out of range
        MODIS_ROW.replace(",1015,", ",2575,"),                                    # not a time
        MODIS_ROW.replace(",Terra,", ",,"),                                       # empty key field
        MODIS_ROW.replace("2026-11-12", "12/11/2026"),                            # not ISO
    )
    result = parse_csv(text, "MODIS_NRT")
    assert (result.rows, result.malformed, len(result.detections)) == (8, 7, 1)


def test_duplicate_keys_in_one_response_keep_the_last_row():
    text = csv_text(MODIS_HEADER, MODIS_ROW, MODIS_ROW.replace(",72,", ",80,"))
    result = parse_csv(text, "MODIS_NRT")
    assert (result.rows, result.duplicates, len(result.detections)) == (2, 1, 1)
    assert result.detections[0].confidence == "80"


def test_empty_and_header_only_responses():
    assert parse_csv("", "MODIS_NRT").rows == 0
    assert parse_csv(MODIS_HEADER + "\n", "MODIS_NRT").detections == []


def test_missing_key_column_is_a_format_error():
    with pytest.raises(FirmsFormatError, match="acq_time"):
        parse_csv(csv_text(MODIS_HEADER.replace("acq_time", "time"), MODIS_ROW), "MODIS_NRT")
    with pytest.raises(FirmsFormatError):
        parse_csv("<html>Service unavailable</html>\n", "MODIS_NRT")


def test_parse_hhmm():
    assert parse_hhmm("0150") == parse_hhmm("150") == time(1, 50)
    assert parse_hhmm("0") == time(0, 0)
    for bad in ("2460", "12:30", "12345", ""):
        with pytest.raises(ValueError):
            parse_hhmm(bad)


def test_params_serialise_the_delivered_row():
    d = parse_csv(csv_text(MODIS_HEADER, MODIS_ROW), "MODIS_NRT").detections[0]
    p = d.params()
    assert json.loads(p["delivered"]) == d.delivered and p["type"] is None
    assert content_hash(json.loads(p["delivered"])) == p["content_hash"]  # hash is reproducible from jsonb
