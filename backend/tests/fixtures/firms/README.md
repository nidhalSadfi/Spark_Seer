# FIRMS test fixtures: SYNTHETIC

Every file here is **synthetic**: hand-written rows in the FIRMS area-API CSV layout, with made-up
values. They are not NASA FIRMS data and must never be used as results. Tests never call FIRMS
(spec §13); `tests/firms_fake.py` serves these files to the real `firms_tool.area_api.Client`
through a fake HTTP session, returning only the rows whose `acq_date` falls in the requested window.

| File | Layout |
|---|---|
| `data_availability.csv` | `data_id,min_date,max_date` (FIRMS `data_availability` endpoint) |
| `MODIS_NRT.csv` | `latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight` |
| `VIIRS_NOAA20_NRT.csv`, `VIIRS_NOAA21_NRT.csv`, `VIIRS_SNPP_NRT.csv` | `latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight` |

NRT files have no `type` column (only SP products carry it). MODIS `confidence` is 0-100, VIIRS
`confidence` is `l`/`n`/`h`. `acq_time` is UTC `HHMM`. The dates (2026-11-12 to 2026-11-14) match
the fake clock the tests use (2026-11-14 12:00 UTC); S-NPP rows end on 2026-11-02, its catalogue
`coverage_end`. All coordinates fall inside the default bbox `3.5,36,10.5,37.5`.

Rows with the same position on different dates or times are deliberate: they are distinct
observations (different natural keys) and must not be collapsed.
