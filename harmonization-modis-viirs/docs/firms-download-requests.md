# NASA FIRMS archive download requests: findings

Checked 2026-11-14 against https://firms.modaps.eosdis.nasa.gov without an account. Nothing was submitted.

## Verdict

You can script request creation, but you must log in first. There is no captcha on the login page.
Whether the create form itself has a captcha can't be checked without logging in; `inspect-form` reports it.
The create form (`/download/create.php`) and the request list (`/download/list.php`) both return
"Authorization failure" to anonymous users. Because of that, the exact POST field names are **unverified**.
The CLI keeps them in a config mapping and refuses to submit until someone checks them once with a
logged-in session.

There is also a better source that needs no login. FIRMS publishes **public yearly per-country CSVs**
for MODIS, S-NPP and NOAA-20, which may make most requests unnecessary.

## Authentication

- **Earthdata Login (OAuth).** `/oauth/login?redirect=/api/auth/login/download/ed/` redirects with a
  302 to `urs.earthdata.nasa.gov/oauth/authorize` and sets the session cookie `auth-firms-session`.
- **Email one-time code.** No Earthdata account is needed. The `/download/` page JS calls
  `GET /api/auth/code/<email>`, which returns `{status:"ok", hash}`. The user then enters the
  6-digit code from the email, and the page calls `GET /api/auth/login/download/email/<hash>/<code>`.
  Possible errors are a database issue, a disabled account, too many failed attempts, or an invalid code.
- **Login check.** `GET /api/whoami` returns `{}` when logged out, and a body with `email`/`id` when logged in.
- **Backend.** `/api/download/*` answers `{"status":"invalid authentication"}` to anonymous calls. It is
  most likely the endpoint behind the create form, but this is not verified.

## Create-request form

These fields come from the FIRMS public docs and user guides, not from the form HTML:

- **Area:** world, country, WDPA protected area, a bounding box drawn on a map, or a custom polygon.
- **Source and coverage:**
  - MODIS C6.1: 2000-11-01 to present
  - VIIRS S-NPP 375 m: 2012-01-20 to 2026-11-02 (end date as shown on the page)
  - VIIRS NOAA-20: 2018-04-01 to present
  - VIIRS NOAA-21: 2023-03-01 to present
  - Landsat 30 m: 2022-06-20 to present
- **Date range:** must fall within the sensor's coverage. The archive only holds data older than the last 7 days.
- **Format:** CSV, JSON or Shapefile.
- **Email:** the notification goes to the account email. One guide also mentions an "email confirmation: yes" option.
- **Output:** a zip named like `DL_FIRE_<code>_<id>.zip`. The codes are `M-C61`, `SV-C2`, `J1V-C2` and `J2V-C2`.
  It contains `fire_archive_*` (standard processing, SP) and/or `fire_nrt_*` (near-real-time, NRT) files.
  NRT data is replaced by SP with roughly a 5-month lag, so a request that crosses that boundary yields both files.
- **Processing time:** reported anywhere from about an hour to a few days.

## Limits

- No limits are published for the maximum span per request, area size, or requests per day.
- The tool splits requests into one per sensor per year by default (`chunk: year`) and waits 15 s between submissions.
- Limits for the alternatives below are published.

## list.php

The status page needs a login, so its structure is unverified. The tool reads every table row,
keeps the header-to-cell mapping, and treats `.zip` or "download" links as files ready to download.

## Alternatives

1. **Public yearly archive (no login).**
   - Index of files: `/data/country/yearly_summary_files.txt`
   - One country: `/data/country/<product>/<year>/<product>_<year>_<Country>.csv`
   - All countries: `/data/country/zips/<product>_<year>_all_countries.zip`
   - Products: `modis` (2000–2025), `viirs-snpp` (2012–2025) and `viirs-jpss1` (NOAA-20, 2018–2025).
     There is no NOAA-21 and no 2026 data yet.
   - Includes the `type` column (0 = presumed vegetation fire, plus volcano, static-source and offshore classes).
     The API's SP output has it too.
2. **Area API (free MAP_KEY).**
   - URL: `/api/area/csv/<KEY>/<SOURCE>/<w,s,e,n>/<1..5 days>/<YYYY-MM-DD>`
   - Sources include `MODIS_SP`, `VIIRS_SNPP_SP`, `VIIRS_NOAA20_SP` and `VIIRS_NOAA21_SP`, plus the `_NRT` versions.
   - Limit: 5000 transactions per 10 minutes. Use `/api/data_availability` to check dates.
   - See "API vs archive requests" below for coverage, cost and column differences.
3. **Semi-manual.** Log in through the browser and create requests from the `plan` output,
   then run `status` and `download` with an exported cookie.

## API vs archive requests

Verified live on 2026-11-14 with the user's MAP_KEY (`FIRMS_MAP_KEY_1`). All checks together used
683 of the 5000 transactions in one 10-minute window. Tool fixes are on branch
`cursor/firms-live-api-checks-93a8`.

### Recommendation

**Use the area API as the main source for all four sensors.** The public yearly archive is now an
optional bulk shortcut, and archive download requests (list.php) aren't needed.

- **Why:**
  - SP output from the API matches the yearly archive CSV row for row and column for column, including `type`.
  - It covers NOAA-21 SP and SP data through 2026-07-31, which the yearly archive doesn't have.
  - For one country it's cheap: about 80 minutes for Australia, 2001 to now, all four sensors.
- **How:**
  - Run `firms area-api --mode auto` over the country bbox, then clip to the border locally.
  - Keep the bbox within 60°×60° if you can, because cost goes up in 60° steps (see below).
- **Yearly archive:** still fine for finished years of MODIS (2018 onward), S-NPP and NOAA-20 when one
  file per country-year is handier. For **MODIS before 2018, prefer the API**, because the yearly files
  hold an older version (see "Version codes" below).
- **NRT (after 2026-07-31 for VIIRS, after 2026-08-31 for MODIS):** has no `type` column and uses
  `2.0NRT`/`6.1NRT` versions. Re-download those dates once SP catches up (about 3–5 months), or
  filter static sources another way.
- **Archive requests (list.php):** no remaining need. The API returns `type` for SP and covers
  every sensor and date the requests would.

### Data availability (`firms availability`, 2026-11-14)

| Source | SP range | NRT range |
|---|---|---|
| MODIS | 2000-11-01 .. 2026-08-31 | 2026-09-01 .. 2026-11-14 |
| VIIRS S-NPP | 2012-01-20 .. 2026-07-31 | 2026-08-01 .. 2026-11-02 (S-NPP data stops on 2026-11-02) |
| VIIRS NOAA-20 | 2018-04-01 .. 2026-07-31 | 2026-08-01 .. 2026-11-14 |
| VIIRS NOAA-21 | **2023-03-01 .. 2026-07-31 (SP exists)** | 2024-01-17 .. 2026-11-14 |

The availability call also lists `LANDSAT_NRT` (from 2022-06-20), `GOES_NRT` (from 2022-08-09), and
the burned-area products `BA_MODIS` and `BA_VIIRS`. For VIIRS, NRT starts the day after SP ends.
NOAA-21 NRT overlaps SP, and the tool uses SP wherever SP exists.

### Endpoints

| Endpoint | Status | Notes |
|---|---|---|
| `/api/area/csv/KEY/SOURCE/w,s,e,n\|world/DAYS[/DATE]` | **verified** | `DAYS` 1–5; returns DATE .. DATE+DAYS−1. Worked for all 8 SP/NRT sources tested. |
| `/api/data_availability/csv/KEY/ALL` | **verified** | `data_id,min_date,max_date`; costs 5 transactions. |
| `/mapserver/mapkey_status/?MAP_KEY=KEY` | **verified** | `transaction_limit` 5000, `current_transactions`, `transaction_interval` "10 minutes". **Free.** |
| `/api/country/csv/...`, `/api/countries` | not working | `Invalid API call.` (HTTP 400). |
| `/api/missing_data/?action=fetch_data` | works, no key | 169 days with gaps (S-NPP SP 80, Terra 57, Aqua 26, NOAA-20 6; NOAA-21 NRT 9). |

### Columns, version codes and match with the archive

`firms compare-archive --country Australia` fetches one API SP day over the bbox of that day's
archive rows and joins on latitude, longitude and acq_time.

| Day | Sensor | Archive rows | Matched in API | Value differences | Version (archive / API) |
|---|---|---|---|---|---|
| **2025-01-01** | MODIS | 345 | 345 (100%) | none | `61.03` / `61.03` |
| **2025-01-01** | S-NPP | 1529 | 1529 (100%) | none | `2` / `2` |
| **2025-01-01** | NOAA-20 | 1547 | 1547 (100%) | none | `2` / `2` |
| 2022-07-01, 2019-01-01, 2018-06-01 | all three | 96–5527 | 100% | none | MODIS `6.03` / `6.03`; VIIRS `2` / `2` |
| 2017-01-01 | MODIS | 71 | 100% | `version` 71, `type` 6 | `6.2` / `6.03` |
| 2015-01-01 | MODIS | 115 | 100% | `version` 115, `type` 6 | `6.2` / `6.03` |
| 2012-06-15 | MODIS | 1062 | 100% | `version` 1062 | `6.2` / `6.03` |
| 2012-01-01 | MODIS | 714 | 100% | `version` 714, `type` 8 (archive 0, API 2) | `6.2` / `6.03` |
| 2012–2017 | S-NPP | 219–5286 | 100% | none | `2` / `2` |

- **Columns are identical** for the archive and the API SP output, 15 for each sensor. MODIS has
  `latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight,type`;
  VIIRS has the same with `bright_ti4`/`bright_ti5`. **The API SP output includes `type`**, which
  corrects the earlier note.
- **NRT output has 14 columns, without `type`.** `firms area-api` merges SP and NRT chunks into one
  file and leaves `type` empty on NRT rows.
- **Version codes:** API SP data is MODIS `6.03` (seen 2012–2022-07) and `61.03` (seen 2025-01-01; the archive uses it from 2023), and
  VIIRS `2`. The **yearly archive's MODIS files for 2012–2017 still say `6.2`**, with a few `type`
  reclassifications (about 1–8% of rows, archive `0` → API `2` for static sources). Detections,
  coordinates and every other value are identical. The exact year where the archive switches to `6.03`
  falls between 2017 and 2018; 2018-06-01 already matches.
- The API bbox sometimes picks up 1–3 extra rows outside the country, so clip locally.
- Confidence: MODIS 0–100; VIIRS `l/n/h` (same in both).

### Cost and time (measured)

**Cost doesn't depend on row count.** It is
**2 transactions × days × ⌈width/60°⌉ × ⌈height/60°⌉**. Tiles are counted on the bbox extent, not
aligned to a grid.

| Call | Rows | Transactions |
|---|---|---|
| 1°×1° box, 1 / 2 / 3 / 5 days (any sensor, SP or NRT, even empty) | 0–4 | 2 / 4 / 6 / 10 |
| Australia box 112,−44,154,−10, 1 day / 5 days | 1.5k / 8–24k | 2 / 10 |
| 60×60, 61×60, 90×60, 90×90, 180×180 boxes, 1 day | — | 2, 4, 4, 8, 18 |
| World, 1 day (VIIRS SP 75k rows, NRT 66k rows) | 66–75k | 36 |
| World, 5 days (NOAA-20 377k rows; MODIS 84k rows) | 84–377k | 180 |

Response times were 0.1–0.6 s for country boxes and 0.6–7.6 s for world calls (MODIS world 5 days
took 7.6 s). No 5xx errors occurred. The tool now paces itself on this cost model: `--dry-run`
prints transactions and the rate-limit floor.

**Estimates, 2001-01-01 to 2026-11-13, `--mode auto`, all four sensors:**

| Area | Calls | Transactions | Time |
|---|---|---|---|
| Any box ≤ 60°×60° (for example Australia) | 3872 (MODIS 1890, S-NPP 1081, NOAA-20 630, NOAA-21 271) | ~38.7k | **~80 min**, limited by the rate cap (about 50 min of calls) |
| One sensor-year, box ≤ 60° | 73 | 730 | ~1.5 min |
| World | 3872 | ~697k | **~23 h** (MODIS ~11.3 h, S-NPP ~6.5 h, NOAA-20 ~3.8 h, NOAA-21 ~1.6 h) |
| One VIIRS sensor-year, world | 73 | 13.1k | ~26 min; about 2.2 GB of CSV |

Splitting a box into smaller boxes saves nothing, because tile counts add up the same. It only helps
if a single huge call times out. Several countries can share one call if they fit in one 60° box.
The public archive gives the same data (MODIS from 2018 on) in one download per country-year, with
no key and no rate limit.

### Limits that matter

- **Rate limit:** 5000 transactions per 10 minutes per key. World-scale jobs take hours;
  country-scale jobs take minutes to about an hour.
- **NRT gap:** the most recent 3–5 months are NRT only, with no `type` column.
- **MODIS yearly archive before 2018:** it holds version `6.2` and an older `type` classification than
  the API's `6.03`. Don't mix the two sources for those years.
- **S-NPP:** no data after 2026-11-02.
- **No country endpoint:** use a bbox and clip.

## Tool

The repository branch is `cursor/firms-request-tool-0a9c`, run as `python -m firms_tool`. It has these commands:

- `plan` / `submit` (dry run unless `--yes`)
- `login` (email code or `--earthdata`)
- `whoami`
- `inspect-form`
- `status`
- `download`
- `archive`
- `mapkey-status`
- `availability`
- `area-api`:
  - 5-day windows, with SP→NRT `--mode auto`
  - resumable chunk cache
  - key-budget pacing
  - retries
  - `--dry-run`
- `compare-archive`

The MAP_KEY is read from `FIRMS_MAP_KEY_1`, then `FIRMS_MAP_KEY`. The tool never prints it.

## Inputs needed from the user

- Region: a country (ISO-3 code plus the archive name), a bbox, or a polygon.
- Study period.
- Sensors.
- Format.
- Email or Earthdata account. Logging in needs a person to enter the emailed code or approve the app.
- The MAP_KEY (secret `FIRMS_MAP_KEY_1`) is visible on the agent VM and was verified on 2026-11-14.
