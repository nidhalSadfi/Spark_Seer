# Fire incident agents

A scheduled, multi-source search for public fire reports. You type countries or zones. It searches each source and returns incident tickets. Every ticket keeps each source's URL, its publish time and the time we fetched it. Each step writes an event, and the page shows those events live.

**Tickets are unverified public mentions.** They are not official alerts, and the tool never dispatches anyone.

## Run it

From `harmonization-modis-viirs/`:

```bash
uv pip install --python .venv/Scripts/python.exe -r requirements-incidents.txt
.venv/Scripts/python.exe -m incident_agents serve        # http://127.0.0.1:8765
.venv/Scripts/python.exe -m incident_agents run "France" --hours 24
.venv/Scripts/python.exe -m incident_agents run "Kabylie, Algeria" --hours 72 --satellite-only
.venv/Scripts/python.exe -m incident_agents tickets
```

`serve` runs the page and the scheduler in one process, bound to 127.0.0.1 only. Data is stored in `data/incidents/incidents.sqlite`, which is gitignored.

## Secrets (`.env`, gitignored)

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

`--satellite-only` also opens tickets from Kanari satellite clusters that have no post. It keeps the 20 largest clusters per zone. In Algeria, Tunisia and Libya, clusters south of 34°N are skipped, because they are almost always oil and gas flares.

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
- The 25-year cell history on Tunisia and Algeria tickets.
- Official civil-protection feeds.
