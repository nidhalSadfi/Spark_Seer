# FIRMS MODIS / VIIRS hot-spot downloader

A small Python CLI for collecting NASA FIRMS active-fire detections (MODIS C6.1 and
VIIRS S-NPP / NOAA-20 / NOAA-21) for MODIS–VIIRS harmonization work. It supports
four ways of getting data. `viewport` is the final integration a later map calls.

| Path | Login | Area | Sensors | Best for |
|---|---|---|---|---|
| `archive` – public yearly files | none | world zip by default, or named countries | MODIS 2000–, S-NPP 2012–, NOAA-20 2018– (no NOAA-21) | completed years, world or one country |
| `submit` – archive download requests (`/download/create.php`) | Earthdata or email code | world, country, bbox, polygon | all four | custom areas or NOAA-21 |
| `viewport` – keyed `/api/area` for one map bbox | `FIRMS_MAP_KEY_1` | one viewport | all four (SP, then NRT) | the final integration a later map calls |
| `area-api` – `/api/area` with a free MAP_KEY | MAP_KEY | bbox or world | all four (SP, then NRT) | resumable downloads of a longer box |

## Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m firms_tool sensors
```

## Archive download requests (create.php / list.php)

1. Copy and edit the config: `cp examples/request.example.yaml my-request.yaml`
   (email, area, date range, sensors, `chunk`).
2. Preview the exact payloads. Nothing is sent:

   ```bash
   python -m firms_tool plan my-request.yaml
   ```

   The tool creates one request per sensor and date chunk. It clips each request to the sensor's
   coverage and ends it no later than 7 days ago, because the archive only holds older data.
3. Log in. The session cookie is saved to `.firms_session.json`, which is gitignored:

   ```bash
   python -m firms_tool login --email you@example.org   # FIRMS emails a 6-digit code
   python -m firms_tool login --earthdata               # ~/.netrc or EARTHDATA_USERNAME/PASSWORD
   # or paste the browser cookie:  export FIRMS_COOKIE='auth-firms-session=...'
   python -m firms_tool whoami
   ```

   `--earthdata` only works after you have approved the FIRMS application in Earthdata
   Login once in a browser.
4. **Verify the form mapping once.** The create form is only served to logged-in
   users, so the field names in `form:` are placeholders. Run:

   ```bash
   python -m firms_tool inspect-form --out form.json
   ```

   It lists every input and select option, plus the backend URLs referenced by the page JS.
   It also reports whether a captcha is present. Update `form.endpoint`, `form.fields`,
   `sensor_values`, `area_type_values` and `constants` to match. Then set `form.verified: true`.
   For an exact match, you can submit one request in the browser with DevTools → Network open
   and copy the field names from that POST.
5. Submit. Without `--yes` this is still a dry run:

   ```bash
   python -m firms_tool submit my-request.yaml --yes --delay 15
   ```

   Every response is appended to `submissions.jsonl`. The run stops at the first HTTP error.
6. Track and fetch the results:

   ```bash
   python -m firms_tool status            # rows parsed from list.php (add --json for raw)
   python -m firms_tool download --out data/requests
   ```

## Public yearly archive (no login)

```bash
python -m firms_tool archive --sensors modis_c61 viirs_snpp viirs_noaa20 \
    --years 2019-2023 --countries Australia --dry-run
python -m firms_tool archive --sensors modis_c61 --years 2020 --countries all   # all-countries zip
```

Country names follow the archive file names, for example `United_States` or
`Cote_d_Ivoire`; see `/data/country/yearly_summary_files.txt`. With no
`--countries`, the scope is the all-countries zip. `--global` is that same
zip. `--countries Tunisia Algeria` limits the download to those two files.

## P1: world monthly cells

P1 is a world 0.5° cell grid, one row per sensor, year, month and cell, so a
later map can ask for a bounding box or a `cell_id`. `data/` is gitignored.
These commands are the world path. This write-up did not run them: there is
no world `cells.parquet` here, and no world detection count.

```bash
python -m firms_tool archive --sensors modis_c61 --years 2001-2025 --global --out data/raw
python -m firms_tool archive --sensors viirs_snpp --years 2012-2025 --global --out data/raw
python pipeline/ingest.py data/raw data/cells.parquet
python pipeline/weather.py docs/p1/global_areas.json data/weather-global --dry-run
```

`--global` is the public all-countries zip at
`/data/country/zips/<product>_<year>_all_countries.zip` (the same URL as
`--countries all` or `--countries world`, and the same zip you get by omitting
`--countries`). Add `--dry-run` to `archive` to print those URLs and skip the
download. `ingest.py` reads loose CSVs and CSV members inside the zips. With
no latitude argument, or with `none`, every latitude is kept.

Filters on that grid: `type` 0, MODIS confidence at least 30, VIIRS confidence
nominal or high. Each output row has `n` (detections) and `days` (distinct
`acq_date`). A point falls in `[lat0, lat0+0.5)` × `[lon0, lon0+0.5)`.
`cell_id` is the southwest corner, one decimal, sign on each side, for example
`36.0_9.0` or `-33.5_-71.5`. `lat` / `lon` are the cell centre. Only cells
with at least one kept detection are stored.

Weather for the world stays a preview. `docs/p1/global_areas.json` is one
Open-Meteo centre per 0.5° cell: **259,200** centres (720 × 360), from
2001-01-01 through 2025-12-31. The command above prints `areas=259200` and
does not call Open-Meteo. `python pipeline/weather.py --world-grid --dry-run data/weather-global`
is the same 259,200 centres. A coarser preview is
`python pipeline/weather.py --world-grid --step 30 --dry-run data/weather-coarser`
(72 centres). `--fetch` would request every centre; the P1 command leaves it off.

## North Africa slice

The same grid, limited to Tunisia and Algeria and to latitude >= 34. The four
demo boxes are an example region inside that slice. Counts from this run are
in `docs/p1-ingest.md` (VIIRS 137,470, MODIS 67,230, 16,607 cells). Those
numbers are the slice. They are not a world total.

```bash
python -m firms_tool archive --sensors modis_c61 --years 2001-2025 --countries Tunisia Algeria --out data/raw
python -m firms_tool archive --sensors viirs_snpp --years 2012-2025 --countries Tunisia Algeria --out data/raw
python pipeline/ingest.py data/raw data/cells.parquet 34
python pipeline/weather.py data/demo_areas.json data/weather
```

The two ingest commands write the same path. The 16,607-row file described in
the write-up is the output of the command that passes `34`. Demo boxes, in
order minLon, minLat, maxLon, maxLat: Bizerte / NW Tunisia `8.3,36.0,9.8,37.2`,
Kabylie `3.7,36.3,5.2,37.0`, NE Algeria `7.0,36.4,8.6,37.0`, Petite Kabylie
`5.3,36.4,6.9,37.1`. The tracked copy is `docs/p1/demo_areas.json`, and that
path works with `weather.py` as well. Weather fetches the Open-Meteo historical
archive at each of the four centres, one request per area, 2001-01-01 through
2025-12-31. Any other area file with the same `areas` list is fetched the same
way.

## Map viewport (final integration)

`viewport` is the call a later interactive map makes for the current view.
It reads `FIRMS_MAP_KEY_1` from the environment or the gitignored `.env`
(`FIRMS_MAP_KEY` is the fallback, or pass `--map-key`). The key is never
printed. URLs shown here use `<MAP_KEY>`.

```bash
python -m firms_tool viewport --sensor viirs_snpp \
    --bbox 8.3,36.0,9.8,37.2 --start 2024-07-01 --end 2024-07-05
```

That requests

`https://firms.modaps.eosdis.nasa.gov/api/area/csv/<MAP_KEY>/<SOURCE>/<west,south,east,north>/<days>/<YYYY-MM-DD>`

in windows of at most 5 days. The same entry point in code is
`firms_tool.area_api.query_viewport(bbox, sensor, start, end)`.
`--dry-run` prints those URLs with the key redacted and does not download.
`world` is rejected. Bulk historical files stay on `archive`
(`/data/country/...`).

## Area API (MAP_KEY)

`viewport` above is the map integration. `area-api` is the resumable downloader
for a longer box. Both read the key from `FIRMS_MAP_KEY_1`. If that is unset, the tool falls back to
`FIRMS_MAP_KEY`, or you can pass `--map-key`. The key is never printed; URLs show it as `<MAP_KEY>`.

```bash
python -m firms_tool mapkey-status            # transactions used in the current 10-minute window
python -m firms_tool availability             # SP / NRT date range per source
python -m firms_tool area-api --sensors modis_c61 viirs_snpp viirs_noaa20 viirs_noaa21 \
    --bbox 110,-45,155,-10 --start 2019-01-01 --end 2026-10-31 --dry-run
python -m firms_tool area-api --config my-request.yaml       # reuses bbox/world, dates, sensors
python -m firms_tool compare-archive --day 2025-01-01 --country Australia
```

How `area-api` behaves:

- **Windows:** calls are split into 5-day windows, which is the most the server accepts.
- **SP vs NRT:** with the default `--mode auto`, it uses standard processing (SP) up to the last
  date `data_availability` reports, then near-real-time (NRT) after that. `--mode sp` and
  `--mode nrt` force one or the other.
- **Resume:** each window is cached as its own file under `<out>/chunks/`. Re-running the same
  command skips windows that are already cached, so an interrupted run picks up where it stopped.
- **Output:** the chunks are then merged into one CSV per sensor, with an extra `source` column.
  SP chunks have a `type` column and NRT chunks don't; the merged file keeps `type` and leaves it
  empty on NRT rows.
- **Cost:** a call costs 2 transactions per day per 60°×60° tile of the bbox extent, whatever the
  row count (measured 2026-11-14). A 5-day call over Australia costs 10; a 5-day world call costs 180.
  `--dry-run` prints the total and the minimum time the 5000-per-10-minutes limit allows.
- **Pacing:** the tool checks `mapkey_status` (free) at the start, every `--check-every` calls, and
  whenever the predicted spend would leave less than `--reserve` plus the next call's cost. It pauses
  until the budget recovers. If the server says the limit is exceeded, it waits for the window to reset.
- **Retries:** HTTP 429 and 5xx errors and dropped connections are retried with exponential backoff.

The country endpoint is not working, so clip bounding-box results to country borders locally.

## Tests

```bash
pip install pytest && python -m pytest -q
```
