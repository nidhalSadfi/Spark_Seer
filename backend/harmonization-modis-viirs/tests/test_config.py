from datetime import date

import pytest

from firms_tool.config import ConfigError, parse_config, plan_jobs, split_range

BASE_CFG = {
    "email": "a@b.org",
    "area": {"type": "bbox", "bbox": [110, -45, 155, -10]},
    "date_range": {"start": "2017-06-01", "end": "2019-03-31"},
    "sensors": ["modis_c61", "viirs_noaa20", "viirs_noaa21"],
}


def test_split_year():
    assert split_range(date(2017, 6, 1), date(2019, 3, 31), "year") == [
        (date(2017, 6, 1), date(2017, 12, 31)),
        (date(2018, 1, 1), date(2018, 12, 31)),
        (date(2019, 1, 1), date(2019, 3, 31)),
    ]


def test_split_days_and_month():
    assert len(split_range(date(2020, 1, 1), date(2020, 1, 10), "4d")) == 3
    assert split_range(date(2020, 12, 15), date(2021, 1, 5), "month")[-1] == (date(2021, 1, 1), date(2021, 1, 5))


def test_plan_clips_to_coverage_and_skips_noaa21():
    jobs = plan_jobs(parse_config(BASE_CFG), today=date(2026, 11, 14))
    by_sensor = {}
    for j in jobs:
        by_sensor.setdefault(j.sensor.key, []).append((j.start, j.end))
    assert len(by_sensor["modis_c61"]) == 3
    assert by_sensor["viirs_noaa20"][0][0] == date(2018, 4, 1)
    assert "viirs_noaa21" not in by_sensor


def test_payload_mapping_override():
    cfg = dict(BASE_CFG, sensors=["viirs_snpp"], chunk="none",
               form={"fields": {"bbox": "coords"}, "constants": {"email_confirm": "yes"}})
    (job,) = plan_jobs(parse_config(cfg), today=date(2026, 11, 14))
    assert job.payload["coords"] == "110,-45,155,-10"
    assert job.payload["sensor"] == "SV-C2"
    assert job.payload["email_confirm"] == "yes"


def test_end_clipped_to_archive_lag():
    cfg = dict(BASE_CFG, sensors=["modis_c61"], chunk="none",
               date_range={"start": "2026-11-01", "end": "2026-11-30"})
    (job,) = plan_jobs(parse_config(cfg), today=date(2026, 11, 14))
    assert job.end == date(2026, 11, 7)


def test_bad_bbox():
    with pytest.raises(ConfigError):
        parse_config(dict(BASE_CFG, area={"type": "bbox", "bbox": [10, 0, 5, 1]}))
