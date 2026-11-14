# P1 ingest: world monthly cells

P1 is a world 0.5° cell grid: one row per sensor, year, month and cell. A later map can take a bounding box or a `cell_id` and read `n` and `days` from that grid. North Africa, including the four demo boxes, is a regional slice of the same grid.

Public yearly FIRMS files only (`python -m firms_tool archive`). The area API was not used. The world archive was not downloaded for this write-up, and a world `cells.parquet` was not built. This document gives no world detection count.

## World grid

Filters, with no latitude floor: `type` 0, MODIS confidence >= 30, VIIRS confidence nominal or high (`n` or `h`). MODIS C6.1 and VIIRS Suomi NPP only.

A point falls in `[lat0, lat0+0.5)` × `[lon0, lon0+0.5)`. `lat` / `lon` are the cell centre. `n` is the detection count. `days` is the number of distinct `acq_date` values. Only cells with at least one kept detection are stored, once per sensor, year and month.

Columns: `cell_id`, `lat_i`, `lon_i`, `lat0`, `lon0`, `lat`, `lon`, `year`, `month`, `sensor` (`MODIS` or `VIIRS`), `n`, `days`. `lat_i` / `lon_i` are `floor(coord × 2)`. `cell_id` is the southwest corner, one decimal, with the sign on each side. Examples: `36.0_9.0`, `-33.5_150.0`, `36.0_-0.5`, `-33.5_-71.5`. Splitting that id on `_` returns the two corners, minus signs included, so a map can request the same cell south of the equator and west of Greenwich. A bounding box selects cells whose half-open square overlaps the box (`lat0` / `lon0` plus half a degree, or the centre in `lat` / `lon`).

The 259,200 figure below is the weather-centre count: every 0.5° cell on Earth, including cells with no fire row. It is not a detection total and not a row count for `cells.parquet`.

### Commands

```bash
python -m firms_tool archive --sensors modis_c61 --years 2001-2025 --global --out data/raw
python -m firms_tool archive --sensors viirs_snpp --years 2012-2025 --global --out data/raw
python pipeline/ingest.py data/raw data/cells.parquet
python pipeline/weather.py docs/p1/global_areas.json data/weather-global --dry-run
```

`--global` is the public all-countries zip, `/data/country/zips/<product>_<year>_all_countries.zip` (the same URL as `--countries all` or `--countries world`). Leaving out `--countries` selects that zip as well. `--dry-run` on `archive` prints the URLs and does not download. `ingest.py` reads CSV members inside the zips. Omitting the latitude argument keeps every latitude. `none` means the same thing.

These world commands were not run here. The counts in the next section are the North Africa slice.

### Weather preview

`docs/p1/global_areas.json` is a 0.5° world grid of Open-Meteo centres: longitude from −180 to 180 and latitude from −90 to 90, **259,200** centres (720 × 360), the same cell size as the fire grid, 2001-01-01 through 2025-12-31. The dry-run prints `areas=259200` and does not call Open-Meteo. `python pipeline/weather.py --world-grid --dry-run data/weather-global` is the same 259,200 centres. A coarser preview is `python pipeline/weather.py --world-grid --step 30 --dry-run data/weather-coarser` (72 centres). The world grid is not fetched. `--fetch` would send one request per centre and is outside this P1 run.

## North Africa slice

The counts in this section are Tunisia and Algeria, latitude >= 34. They come from that regional run.

```bash
python -m firms_tool archive --sensors modis_c61 --years 2001-2025 --countries Tunisia Algeria --out data/raw
python -m firms_tool archive --sensors viirs_snpp --years 2012-2025 --countries Tunisia Algeria --out data/raw
python pipeline/ingest.py data/raw data/cells.parquet 34
python pipeline/weather.py data/demo_areas.json data/weather
```

`docs/p1/demo_areas.json` is the tracked copy of the four boxes. `pipeline/weather.py` reads either path. The world ingest command and this one both write `data/cells.parquet`. The 84 KB file below is the output of the command that passes `34`.

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

### Grid from that run

`data/cells.parquet` (84 KB, 16,607 rows). One row per 0.5° cell, year, month and sensor. A point falls in `[lat0, lat0+0.5)` × `[lon0, lon0+0.5)`. `lat` / `lon` are the cell centre. `n` is `count(*)`. `days` is `count(distinct acq_date)`.

Columns: `cell_id`, `lat_i`, `lon_i`, `lat0`, `lon0`, `lat`, `lon`, `year`, `month`, `sensor` (`MODIS` or `VIIRS`), `n`, `days`. `cell_id` is the southwest corner, for example `36.0_9.0`. `lat_i` / `lon_i` are `floor(coord × 2)`.

Sum of `n` is 204,700, the same as the detection total. Cell latitudes run from 34.0 to 37.0 and longitudes from −2.5 to 11.0.

### Demo boxes

`data/demo_areas.json`. Order is minLon, minLat, maxLon, maxLat. Bizerte is first.

| id | Name | Box | Centre |
| --- | --- | --- | --- |
| bizerte | Bizerte / NW Tunisia | 8.3, 36.0, 9.8, 37.2 | 36.60°N, 9.05°E |
| kabylie | Kabylie | 3.7, 36.3, 5.2, 37.0 | 36.65°N, 4.45°E |
| ne_algeria | NE Algeria | 7.0, 36.4, 8.6, 37.0 | 36.70°N, 7.80°E |
| petite_kabylie | Petite Kabylie | 5.3, 36.4, 6.9, 37.1 | 36.75°N, 6.10°E |

Petite Kabylie is the fourth box. It fills the coastal gap between Kabylie (ends 5.2°E) and NE Algeria (starts 7.0°E). Jijel and the Petite Kabylie oak forests burn in the same summer season, the box stays north of 36.4°N, and it does not overlap the other three.

### Weather

`pipeline/weather.py`. One Open-Meteo archive request per centre, 2001-01-01 through 2025-12-31, UTC, wind in m/s. Daily temperature (min, mean, max), precipitation sum, wind speed and gusts, relative humidity (min, mean, max). Each file has `fetched_at` and `daily.time` (9,131 days, no nulls).

- `data/weather/bizerte.json`
- `data/weather/kabylie.json`
- `data/weather/ne_algeria.json`
- `data/weather/petite_kabylie.json`

The first weather pass got HTTP 429 for NE Algeria and Petite Kabylie. A retry with backoff wrote all four files. Nothing is still missing. Meteomatics was not used.

### Paths

Raw CSVs from the regional archive (122 MB, 78 files, gitignored): `data/raw/modis_c61/`, `data/raw/viirs_snpp/`.

`data/` is gitignored, so the regional CSVs, the 84 KB parquet, `demo_areas.json` and the four weather JSON files are not on the branch. A world `cells.parquet` is not on the branch either.
