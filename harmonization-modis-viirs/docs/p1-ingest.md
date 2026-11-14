# P1 ingest: monthly cells and demo-area weather

Branch `cursor/p1-monthly-cells-2a9d`, from `cursor/firms-live-api-checks-93a8`. No pull request.

Public yearly FIRMS country files only (`python -m firms_tool archive`). The area API was not used.

## Counts

Filters: latitude >= 34, `type` 0, MODIS confidence >= 30, VIIRS confidence `n` or `h`. Tunisia and Algeria together.

| Sensor | Detections (`n`) | Years | Files read | Rows in those files |
| --- | --- | --- | --- | --- |
| VIIRS S-NPP | **137,470** | 2012–2025, no gaps | 28 | 1,415,834 |
| MODIS C6.1 | **67,230** | 2001–2025, no gaps | 50 | 231,953 |

VIIRS is about 470 above the 137,000 check (0.3%). The month shape matches the claimed season, so the filter is the right one. Numbers were not adjusted.

VIIRS detections by month, 2012–2025: Jan 1,524, Feb 1,638, Mar 1,957, Apr 1,977, May 2,748, Jun 7,660, Jul 27,507, Aug 62,284, Sep 14,611, Oct 10,618, Nov 3,589, Dec 1,357.

MODIS detections by month, 2001–2025: Jan 295, Feb 375, Mar 446, Apr 378, May 616, Jun 2,957, Jul 17,125, Aug 30,348, Sep 7,940, Oct 5,189, Nov 1,265, Dec 296.

Tunisia alone, same filters: 5,920 MODIS detections. The EmberGuard note said about 6,300. This run is lower and was not forced to that figure.

Archive `version` on the MODIS files, all rows: `6.2` for 2001–2017, `6.03` for 2018–2022, `61.03` for 2023–2025. VIIRS files are version `2`. Pre-2018 MODIS `type` is the older archive classification.

## Grid

`data/cells.parquet` (84 KB, 16,607 rows). One row per 0.5° cell, year, month and sensor. A point falls in `[lat0, lat0+0.5)` × `[lon0, lon0+0.5)`. `lat` / `lon` are the cell centre. `n` is `count(*)`. `days` is `count(distinct acq_date)`.

Columns: `cell_id`, `lat_i`, `lon_i`, `lat0`, `lon0`, `lat`, `lon`, `year`, `month`, `sensor` (`MODIS` or `VIIRS`), `n`, `days`. `cell_id` is the southwest corner, for example `36.0_9.0`. `lat_i` / `lon_i` are `floor(coord × 2)`.

Sum of `n` is 204,700, the same as the detection total. Cell latitudes run from 34.0 to 37.0 and longitudes from −2.5 to 11.0.

## Demo boxes

`data/demo_areas.json`. Order is minLon, minLat, maxLon, maxLat. Bizerte is first.

| id | Name | Box | Centre |
| --- | --- | --- | --- |
| bizerte | Bizerte / NW Tunisia | 8.3, 36.0, 9.8, 37.2 | 36.60°N, 9.05°E |
| kabylie | Kabylie | 3.7, 36.3, 5.2, 37.0 | 36.65°N, 4.45°E |
| ne_algeria | NE Algeria | 7.0, 36.4, 8.6, 37.0 | 36.70°N, 7.80°E |
| petite_kabylie | Petite Kabylie | 5.3, 36.4, 6.9, 37.1 | 36.75°N, 6.10°E |

Petite Kabylie is the fourth box. It fills the coastal gap between Kabylie (ends 5.2°E) and NE Algeria (starts 7.0°E). Jijel and the Petite Kabylie oak forests burn in the same summer season, the box stays north of 36.4°N, and it does not overlap the other three.

## Weather

`pipeline/weather.py`. One Open-Meteo archive request per centre, 2001-01-01 through 2025-12-31, UTC, wind in m/s. Daily temperature (min, mean, max), precipitation sum, wind speed and gusts, relative humidity (min, mean, max). Each file has `fetched_at` and `daily.time` (9,131 days, no nulls).

- `data/weather/bizerte.json`
- `data/weather/kabylie.json`
- `data/weather/ne_algeria.json`
- `data/weather/petite_kabylie.json`

## Paths and what failed

Raw CSVs (122 MB, 78 files, gitignored): `data/raw/modis_c61/`, `data/raw/viirs_snpp/`.

Command: `python pipeline/ingest.py data/raw data/cells.parquet 34`.

`data/` is gitignored, so the CSVs, the 84 KB parquet, `demo_areas.json` and the weather JSON are not on the branch. Regenerate with the archive commands in the README, then ingest and weather.

The first weather pass got HTTP 429 for NE Algeria and Petite Kabylie. A retry with backoff wrote all four files. Nothing is still missing. Meteomatics was not used.
