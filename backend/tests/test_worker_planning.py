"""firms_poll window planning (pure, no database)."""
from datetime import date, timedelta

from firms_tool.sensors import SENSORS
from worker.firms_poll import NRT_SOURCES, plan_source

TODAY = date(2026, 11, 14)
YESTERDAY = TODAY - timedelta(days=1)
MODIS = SENSORS["modis_c61"]
SNPP = SENSORS["viirs_snpp"]
AVAIL = {"MODIS_NRT": (date(2026, 8, 1), TODAY), "VIIRS_SNPP_NRT": (date(2026, 8, 1), date(2026, 11, 2))}


def spans(plan):
    return [(w.start, w.days) for w in plan.windows]


def covered(plan):
    days = [w.start + timedelta(days=i) for w in plan.windows for i in range(w.days)]
    assert days == sorted(set(days)), "windows overlap or are out of order"
    return days[0], days[-1], len(days)


def test_polls_the_four_nrt_sources():
    assert [s for s, _ in NRT_SOURCES] == ["MODIS_NRT", "VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]


def test_recent_watermark_fetches_today_and_yesterday():
    for watermark in (YESTERDAY, TODAY):
        plan = plan_source("MODIS_NRT", MODIS, TODAY, AVAIL, watermark, 30)
        assert spans(plan) == [(YESTERDAY, 2)] and plan.skipped is None and plan.notes == ()


def test_backfills_from_an_older_watermark_in_five_day_windows():
    plan = plan_source("MODIS_NRT", MODIS, TODAY, AVAIL, date(2026, 11, 8), 30)
    assert spans(plan) == [(date(2026, 11, 8), 5), (date(2026, 11, 13), 2)]
    assert all(w.days <= 5 and w.source == "MODIS_NRT" for w in plan.windows)


def test_backfill_is_capped_at_max_days_including_today():
    plan = plan_source("MODIS_NRT", MODIS, TODAY, AVAIL, date(2026, 9, 1), 30)
    assert covered(plan) == (date(2026, 10, 16), TODAY, 30)
    assert all(w.days <= 5 for w in plan.windows) and len(plan.windows) == 6
    assert any("capped" in n for n in plan.notes)


def test_first_poll_without_watermark_backfills_the_cap():
    plan = plan_source("MODIS_NRT", MODIS, TODAY, AVAIL, None, 10)
    assert covered(plan) == (date(2026, 11, 5), TODAY, 10)


def test_backfill_never_starts_before_availability_min_date():
    avail = {"MODIS_NRT": (date(2026, 11, 10), TODAY)}
    plan = plan_source("MODIS_NRT", MODIS, TODAY, avail, date(2026, 11, 1), 30)
    assert covered(plan) == (date(2026, 11, 10), TODAY, 5)


def test_today_and_yesterday_are_always_fetched():
    # Neither a tiny backfill cap nor a late availability min date drops yesterday.
    assert spans(plan_source("MODIS_NRT", MODIS, TODAY, AVAIL, date(2026, 11, 1), 1)) == [(YESTERDAY, 2)]
    late = {"MODIS_NRT": (TODAY, TODAY)}
    assert spans(plan_source("MODIS_NRT", MODIS, TODAY, late, None, 30)) == [(YESTERDAY, 2)]


def test_source_past_catalogue_coverage_end_is_skipped_once_fully_fetched():
    plan = plan_source("VIIRS_SNPP_NRT", SNPP, TODAY, AVAIL, date(2026, 11, 2), 30)
    assert plan.windows == () and "coverage_end 2026-11-02" in plan.skipped
    still_reported = {"VIIRS_SNPP_NRT": (date(2026, 8, 1), TODAY)}
    assert "still reports data up to 2026-11-14" in plan_source(
        "VIIRS_SNPP_NRT", SNPP, TODAY, still_reported, date(2026, 11, 2), 30).skipped
    # Its last days are older than the backfill floor: nothing left to fetch.
    assert plan_source("VIIRS_SNPP_NRT", SNPP, TODAY, AVAIL, None, 5).skipped


def test_ended_source_fetches_its_uncovered_last_days_up_to_coverage_end():
    # Never fetched: from the backfill floor (30 days incl. today = 10-16) to coverage_end, not to today.
    plan = plan_source("VIIRS_SNPP_NRT", SNPP, TODAY, AVAIL, None, 30)
    assert plan.skipped is None and covered(plan) == (date(2026, 10, 16), date(2026, 11, 2), 18)
    assert any("last uncovered days" in n for n in plan.notes)
    # Partly fetched: from the watermark (refetched, as for live sources) to coverage_end.
    plan = plan_source("VIIRS_SNPP_NRT", SNPP, TODAY, AVAIL, date(2026, 10, 30), 30)
    assert covered(plan) == (date(2026, 10, 30), date(2026, 11, 2), 4)
    # Availability ending before coverage_end caps the tail.
    early = {"VIIRS_SNPP_NRT": (date(2026, 8, 1), date(2026, 10, 31))}
    assert covered(plan_source("VIIRS_SNPP_NRT", SNPP, TODAY, early, None, 30))[1] == date(2026, 10, 31)
    # On its last covered day the source is still polled.
    on_last_day = plan_source("VIIRS_SNPP_NRT", SNPP, date(2026, 11, 2), AVAIL, date(2026, 11, 1), 30)
    assert on_last_day.skipped is None and spans(on_last_day) == [(date(2026, 11, 1), 2)]


def test_source_absent_from_availability_is_skipped():
    plan = plan_source("VIIRS_NOAA21_NRT", SENSORS["viirs_noaa21"], TODAY, AVAIL, None, 30)
    assert plan.windows == () and plan.skipped == "absent from FIRMS data_availability"


def test_stalled_availability_is_noted_not_an_error():
    avail = {"MODIS_NRT": (date(2026, 8, 1), date(2026, 11, 10))}
    plan = plan_source("MODIS_NRT", MODIS, TODAY, avail, YESTERDAY, 30)
    assert spans(plan) == [(YESTERDAY, 2)] and any("stalled" in n for n in plan.notes)
