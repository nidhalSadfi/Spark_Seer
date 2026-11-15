# Fire incident agents

A scheduled, multi-source search for public fire reports. You type countries or zones. It searches each source and returns incident tickets. Every ticket keeps each source's URL, its publish time and the time we fetched it. Each step writes an event, and the page shows those events live.

**Tickets are unverified public mentions.** They are not official alerts, and the tool never dispatches anyone.

## Run it

**With the backend (the normal way).** Starting the API (`cd backend && uvicorn api.main:app --reload --port 8000`) starts these agents too, as a separate process on `127.0.0.1:8765` (`backend/api/incidents.py`), restarts them if they die and stops them with the API. They read their keys from the **repo-root `.env`** (see `.env.example`), or from the environment (which wins); the package's own `.env` still works and wins over the root one. Restart the API after editing a key. `INCIDENTS_AUTOSTART=0` turns the autostart off and `INCIDENT_PORT` changes the port. `GET /api/status` here says which keys are visible (booleans, never values).

**By hand**, from `harmonization-modis-viirs/`:

```bash
uv pip install --python .venv/Scripts/python.exe -r requirements-incidents.txt
.venv/Scripts/python.exe -m incident_agents serve        # http://127.0.0.1:8765
.venv/Scripts/python.exe -m incident_agents run "France" --hours 24
.venv/Scripts/python.exe -m incident_agents run "Kabylie, Algeria" --hours 72 --satellite-only
.venv/Scripts/python.exe -m incident_agents replay "France" --from 2026-08-26 --to 2026-08-29
.venv/Scripts/python.exe -m incident_agents tickets
```

`serve` runs the page and the scheduler in one process, bound to 127.0.0.1 only. Data is stored in `data/incidents/incidents.sqlite`, which is gitignored.

**From the dashboard.** The React dashboard's *Social Alerts* page (`frontend/`, `/dashboard/social`) reads the tickets from this server and has this page's console built in (its *Agents* tab: start a run or a replay, schedules, the live step events as a node graph), and it lets people report a ticket as a community fire alert. For that the server answers cross-origin **GET, POST and DELETE** requests (JSON bodies) from `http://localhost:5173` and `:4173` (and the `127.0.0.1` forms). Any other web page is refused by the browser, so it cannot start a run and spend the quotas. Set `INCIDENT_CORS_ORIGINS` (comma-separated, empty for none) to allow another dashboard address, and keep that list to dashboards you trust. This page keeps working as before.

### 25-year cell history (optional, built once)

Tickets in Tunisia, Algeria and France north of 34°N show the fire history of their 0.5° cell. The grid is `data/cells.parquet`, which is gitignored. Build it once from the public FIRMS yearly files (about 140 MB downloaded):

```bash
.venv/Scripts/python.exe -m firms_tool archive --sensors modis_c61 --years 2001-2025 --countries Tunisia Algeria France --out data/raw
.venv/Scripts/python.exe -m firms_tool archive --sensors viirs_snpp --years 2012-2025 --countries Tunisia Algeria France --out data/raw
.venv/Scripts/python.exe pipeline/ingest.py data/raw data/cells.parquet 34
```

Without the file, tickets simply carry no history. If you add more countries, set `INCIDENT_HISTORY_COUNTRIES` (default `TN,DZ,FR`) to match.

## Secrets (`.env`, gitignored)

Put these names in the repo-root `.env` (the file the whole backend is configured from; the names are in `.env.example`) or in a `.env` next to this folder's `requirements-incidents.txt`. A real environment variable wins over both files, and this folder's file wins over the root one.

| Purpose | Name in .env |
|---|---|
| Groq model | `test_fire_groq_key` (or `GROQ_API_KEY`) |
| X via TwitterAPI.io | `twitterapi` |
| Bluesky search | `bsky_handle` and `bsky_password` (an app password, `xxxx-xxxx-xxxx-xxxx`) |
| FIRMS check | `FIRMS_MAP_KEY_1` |
| Kanari, GDELT, Open-Meteo, Nominatim | no key |

## How one run works

| Step | Who | What |
|---|---|---|
| plan | Groq `openai/gpt-oss-20b` | Turns the text into zones (name + ISO-2) and extra keywords. |
| validate | code | Resolves each zone to a box. On errors, sends the errors back to the model, at most 2 retries. An empty result never triggers a retry. |
| fetch | code, one connector per source | Kanari verified signals, Bluesky `searchPosts`, X advanced search, GDELT headlines. Each records its URL, publish time and fetch time. |
| filter | code | Drops items outside the time window, items without a search term, and duplicate URLs. A Kanari signal wins over the same Bluesky post. |
| judge | Groq, one agent per source | Decides the category, the timing, whether the author saw it firsthand, the place phrase and the country. Code drops any place not written in the post and any country out of scope. |
| locate | code | Open-Meteo geocoding inside the zone (Nominatim for regions). Each location gets a precision and a radius. Coordinates are never invented. |
| ticket | code | Merges reports in the same country within the larger radius (at least 10 km) and 48 h. A URL is never counted twice. |
| satellite | code | Kanari clusters for the country, one call per run, then FIRMS NOAA-20/21 and MODIS NRT in a small box. Status: **Satellite overlap**, **No satellite support** or **Mentioned**. |

| history | code | The 0.5° cell under the ticket: MODIS years with fire out of 25, detections, peak month, worst year, and how the cell ranks in the grid. MODIS and VIIRS are never summed. |
| lead | code | Signed minutes from the first public post to the first satellite detection (positive: people posted first), kept separately against Kanari's first detection and the first FIRMS polar pass. |

`--satellite-only` also opens tickets from Kanari satellite clusters that have no post. It keeps the 20 largest clusters per zone. In Algeria, Tunisia and Libya, clusters south of 34°N are skipped, because they are almost always oil and gas flares.

## Replay a past window

`replay` (or **Period → Past dates** on the page) rebuilds what the agents would have found on past dates. Kanari's archive starts on **3 Aug 2026**, so earlier dates are refused. A replay covers at most 31 days.

A broad keyword search over a past window only reaches its last few hours: sources return newest first, and our page caps stop there. So a replay is **fire-seeded**:

1. It lists the significant fires Kanari archived in the zones. It keeps those inside a zone box and checks their country with a reverse geocode, because Kanari tagged Zaragoza and Bilbao as FR. Flares are dropped. Fires with witness posts or press come first, then the largest, up to 6 (`REPLAY_MAX_FIRES`).
2. Each fire becomes a satellite ticket. Its place name is searched on Bluesky and X, from 6 h before to 24 h after Kanari first saw it.
   - Kanari writes names in Latin script, so the OpenStreetMap spellings in the country's languages are searched too, for example Mateur → ماطر and El Tarf → الطارف.
   - A relevant post found by a fire's own name search joins that fire's ticket when it names the place, even if the geocoder can't resolve its spelling.
3. The posts go through the same judge, locator and merge steps as a live run. The satellite check uses the archive plus FIRMS. FIRMS uses standard processing where it exists, NRT after it, and adds S-NPP before 2 Nov 2026.

Replay runs and tickets are marked `mode = replay`. They show a Replay badge and never merge with live tickets. Schedules are live only.

**What counts for lead time:**
- **Only posts about one specific place start the clock.** Region-level mentions and news items stay on the ticket but are not timed. In the first Algeria replay, a provincial roundup ("69 fires in 13 provinces") would otherwise have read as posts arriving 6 hours before the satellites.
- **Satellite detections more than 6 h before the first post are ignored,** because they are more likely another fire.
- **When a post search hits its page limit,** earlier posts may exist. The lead is then marked as a lower bound.
- **Replay leads only cover fires that Kanari saw,** so they cannot show how often posts come with no satellite detection at all.

## Checked live on 15 Nov 2026 (replay)

`replay "France" --from 2026-08-26 --to 2026-08-29 --max-fires 5`:
- **Seeds:** 5 of 12 archived fires in France were replayed. All 5 tickets got "Satellite overlap" and a 25-year history.
- **Posts:** 42 found, 37 kept, 28 judged as current fire reports.
- **Calenzana (Corsica):** the first X post ("Un feu de végétation est signalé à Calenzana") came **47 min before** Kanari's first detection. Nearby FIRMS passes had no match. The search returned fewer posts than a full page, so no earlier post was cut off.
- **Magescq (Landes):** the satellites were first, Kanari by 26 min and a NOAA-20/21 pass by 38 min.
- **Satellite-only seeds can be industrial heat.** Fos-sur-Mer is an industrial port, so a seed there may not be a vegetation fire.

`replay "Tunisie et Algérie" --from 2026-08-24 --to 2026-08-30 --max-fires 6`:
- **A Tunisia-only replay with Latin names found 0 posts.** With Arabic names added, the Tunisia and Algeria replay found 68 posts and kept 40. All of them came from the El Milia and Constantine fires in Algeria.
- **Constantine (Djebel Chettaba):** an X post came **76 min before** Kanari's first detection and **141 min before** the first FIRMS pass.
- **A later Constantine smoke photo:** the satellites were first, Kanari by 120 min and FIRMS by 55 min.
- **The Tunisian fires had no posts on X or Bluesky.** Djebel Abiod was searched in Latin and Arabic. Mateur was searched only in Latin, before the Arabic names were added. Local reporting is probably mostly on Facebook, which we can't search.

**Across both replays,** 4 tickets have a lead time. Posts came first in 2 of them (Calenzana, Constantine), and the satellites in the other 2. That's too few for a general claim. Kanari's first detection is usually Meteosat MTG, a geostationary satellite imaging every 10 minutes, which is hard for posts to beat.

## Checked live on 14 Nov 2026

- **Judge:** 10 Kanari-verified fire posts (PT, ES, FR, US) were all classified as active fire reports. Located places fell within about 1–25 km of Kanari's point.
- **Rejections in France, Algeria and Tunisia** were correct: building fires, Canadian and Ukrainian news, a tanker fire in Hormuz, vehicle recalls. No judged post from those countries has become a ticket yet. The only tickets there came from Kanari.
- **FIRMS:** 18 NOAA-20/21 detections found inside a 10 km box around a live Kanari VIIRS cluster in California.
- **Scheduler:** a 60-minute schedule ran at once with `schedule_id`, set its next run an hour later, and was removed.
- **GDELT:** returned 429 from this machine on every try, so no headline has been tested end to end.

## Limits measured on 14 Nov 2026

- **Groq:** 8,000 tokens per minute and **1,000 requests per day**.
  - The client paces itself on the rate-limit headers, and a run with ~120 items takes 2–3 minutes.
  - A run makes about 15–25 calls. Verdicts are cached by URL, so a scheduled run only pays for new posts.
  - Runs stop calling the model at 900 calls a day (`GROQ_CALLS_PER_DAY`).
  - Hourly schedules for one or two countries fit within the limit.
- **TwitterAPI.io free tier:** one request every 5 seconds. Pages are capped at 4 per run and 40 per day (`TWITTER_PAGES_PER_RUN`, `TWITTER_PAGES_PER_DAY`).
- **GDELT:** one request every 5 seconds. It answered 429 from this machine all evening, and the run reports that and continues.
- **Kanari:** at most one call per minute per endpoint, as its docs ask. Replies are cached for 5 minutes. Attribution "Source: kanari.io" is shown.
- **Schedules:** every 15 minutes at the shortest (`MIN_SCHEDULE_MINUTES`).

## Not done yet

- The Bluesky Jetstream stream: tested, and it can rewind 24 h with no key, but it is not wired in.
- Review actions on tickets.
- Official civil-protection feeds.
- Replay before 3 Aug 2026, such as the 2021 or 2023 seasons. It would need FIRMS clusters as seeds instead of Kanari's archive.
- GDELT in replay. It is not searched there.
