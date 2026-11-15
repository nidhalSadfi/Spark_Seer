# Innovation section: public fire reports turned into incident tickets

Branch: `innovation-incident-intelligence`. Written 14 Nov 2026, about 22:45 Tunis time.

> **Direction changed later on 14 Nov 2026.** The user chose a standalone set of scheduled agents with no frontend. Its features:
> - any country or zone you type;
> - X, Bluesky, Kanari and GDELT as sources, one Groq agent per source;
> - satellite data as confirmation.
>
> That version is built in [`incident_agents/`](../incident_agents/README.md). The rest of this file is the original analysis. Its Kanari measurements still hold. Its build steps (backend-core router, React page) were not followed.

The plan combines two notes:

- `fire_incident_intelligence_project_context.md` (the user's product idea, kept outside the repo).
- The "Social tickets" and "Two agents" sections of [model-context.md](model-context.md).

The figures below come from live calls made tonight. Section 2 separates what was measured from what was only claimed.

## 1. Verdict

**The idea is feasible, but not in the form the product note describes, and not before the deadline.** The note describes a multi-source pipeline: X, a Bluesky Jetstream stream, a classifier, clustering, PostGIS, a queue and a review workflow. That is weeks of work.

The deadline is assumed to be Sunday 14:30 Tunis time, which is tomorrow (model-context.md gives that time; please confirm the day). That leaves about 16 hours, including sleep. Those same hours are needed for the main product:

- Running the team's calibration (`pipeline/calibrate.py` on `origin/backend-core`) on the real files.
- Committing `data/harmonized.parquet`, which the backend serves and which is not committed yet.
- Switching the frontend off mock data. `src/services/fireApi.js` on `origin/frontend` still generates numbers from a seeded random generator.

**What fits before the deadline is one narrow piece:** an incident inbox for northern Tunisia and Algeria. Each ticket starts from a satellite fire cluster, using Kanari's regional query. The ticket then gets three additions:

- a latitude flag for the Saharan gas-flare belt;
- the same-month history of its 0.5° cell from our harmonized 2001–2025 calendar;
- any public post that mentions it.

That is roughly one person for 5 to 6 hours: a backend router, a page and replay fixtures. It should start only after the three main-product tasks above have owners.

**What does not fit:**

- X through TwitterAPI.io;
- our own Bluesky stream;
- an LLM classifier;
- clustering of our own posts;
- review state stored in a database;
- the two-agent exchange.

These go in the later roadmap in section 5.3.

**The main finding tonight changes how the idea is framed.** Kanari is a free service that already joins satellite fire clusters with public posts. It manages that join for **0.5% of fires worldwide**: 383 of 71,872 since 3 Aug 2026 (Portugal: 4 of 46). For Algeria and Tunisia it has 0 matches among 180 archived fires. At the worldwide rate, about one match would be expected there, so the zero is consistent with the region rather than proof of a special gap. Either way, waiting for social posts to trigger a ticket would leave the page empty almost everywhere. **Tickets should start from satellite data, with public reports attached when they exist.** The pitch should not be "we detect fires from social media". It should be "every fire cluster arrives with its 25-year context and whatever the public said".

## 2. Measured tonight vs. claimed

Live calls on 14 Nov 2026, between 21:27 and 21:45 UTC, with no key. The first `signals` call carried a project-named User-Agent. Later calls used a generic one.

| Source | Observed | Not checked |
|---|---|---|
| Kanari `GET /api/signals?hours=72` | HTTP 200, `Cache-Control: public, max-age=30`. 13 signals out of 786 posts scanned. **All from Bluesky.** Countries: PT 4, US 4, BR 2, GP 1, ES 1, FR 1. **None in TN or DZ.** The field is `countryCode`. **Full post `text` is returned** (answers open question 3 of the product note). | Terms page, rate limit |
| Who posts those signals | Mostly relay and press accounts: `fogospt.bsky.social` 4, `vigiles.town` 3, plus press outlets. Not eyewitnesses. | — |
| Kanari `GET /api/events?hours=24` | Returns 2,000 of 19,378 clusters (`truncated: true`), in an unknown order. Fields: `centroid`, `bbox`, `viirsCount`, `goesCount`, `mtgCount`, `maxFrp`, a `confidence` of `possible` / `probable` / `corrobore`, and an optional `social` join. No country field, so it cannot serve one region on its own. | Whether it accepts a bbox |
| Kanari MCP `POST /api/mcp` | Plain HTTP JSON-RPC with an SSE-framed reply. No MCP client and no LLM needed. Tools: `active_fires` (filters `country`, `bbox`, `near`, `hours`, `limit`), `fire_archive_search` (`limit` at most 100), `fire_details`, `wildfire_stats`, `firefighting_aircraft` and `earliness_cases`. The reply carries "Source: kanari.io (CC BY 4.0)". | Rate limit, full terms |
| `wildfire_stats`, all periods (since 2026-08-03) | Worldwide 71,872 fires, 383 with witnesses. PT 46 / 4. **DZ 155 / 0. TN 25 / 0.** | — |
| `fire_archive_search` | DZ: the first 100 of 155, of which 59 are at 34°N or above. TN: 25, of which 20 are at 34°N or above. 0 witness posts and 0 press articles in all of them. Many clusters are MTG-only (Meteosat, geostationary), which is not a sensor in our grid. Several archived Mateur fires sit inside the Bizerte box, for example 36.92°N 9.64°E with 7 VIIRS detections. | DZ rows 101–155 |
| `active_fires`, 72 h | DZ: 13 matching. The top results are at **Hassi Messaoud, 31.6°N**, the oil field, labelled `probable`. These are almost certainly gas flares, and a cut at 34°N removes them. TN: 0. | — |
| GDELT DOC API | HTTP 429 on every attempt tonight. Coverage of Arabic and French fire news from DZ and TN is **unverified**. | Everything |
| TwitterAPI.io | Not called. It needs your own account and payment. | Price, provenance, terms |
| Telegram and GDELT inside Kanari | Kanari's documentation claims them. Every signal we observed was from Bluesky. | — |

## 3. What this adds over Kanari

Kanari already merges VIIRS, GOES and MTG detections with public posts, so rebuilding that would add nothing. Our ticket adds:

1. **Historical context from our own grid.** Each ticket names its 0.5° cell and calendar month. It shows that month's 2001–2025 harmonized history from `core.calendar_for_bbox`: the median, the `critical_months` list, and the high/normal/low flags it already computes. **It shows context, not a numeric anomaly for today.** The grid stops in 2025, and S-NPP stopped on 2 Nov 2026. Kanari counts mix in GOES and MTG, which are not in our unit. Comparing those counts to our median would repeat the mismatch this project exists to fix. The wording is: "Cell 36.5_9.5, October: a critical month in 2001–2025, median N harmonized detections." It never says "x% above normal".
2. **A latitude gate.** Kanari-era data is near-real-time, and NRT rows have no `type` column, so the only gate available is the 34°N cut. Clusters south of it are flagged `likely_industrial` and hidden by default, not deleted. That catches the Hassi Messaoud clusters that Kanari labels `probable`.
3. **Honest empty states.** "No public mention found" is shown as different from "no fire". Later, French and Arabic search (Bluesky `searchPosts`, GDELT with `sourcecountry:AG` / `TS`) could raise the match rate in this region. That is untested, which is why it sits in phase 2.

## 4. Decisions needed from you

The two notes disagree on three points. My recommendation comes first in each.

| Topic | model-context.md | Product note | Recommendation |
|---|---|---|---|
| **X / Twitter** | Leave X out. | A small TwitterAPI.io test. | **Leave it out of the hackathon.** It needs your account and payment, it is a third-party source of X data with unclear provenance, and nothing yet shows that X carries geolocatable fire reports for this region. Revisit with written terms from the provider. |
| **Ticket statuses** | 3 words: Satellite overlap / No satellite support / Mentioned. | 8 statuses plus 3 confidence dimensions plus review. | **Use the 3 words now, and keep the 3 dimensions as separate fields.** The 8-step workflow needs stored state and a reviewer. |
| **Bluesky** | Optional `searchPosts` with one app password. | Jetstream firehose. | **Neither for the hackathon.** Kanari already reads Bluesky, and the free Render service sleeps, which would kill a stream. Later, use `searchPosts` with French and Arabic terms. |

A fourth decision is **where the code goes**. The API lives on `origin/backend-core` (FastAPI) and the UI on `origin/frontend` (React, Vite, Leaflet). This branch is off `harmonization-modis-viirs`, which has neither. I recommend:

- keep this plan here;
- build on `innovation-incidents-api` off `origin/backend-core` and `innovation-incidents-ui` off `origin/frontend`;
- have the team merge them, since `render.yaml` deploys only from `main`.

## 5. Architecture

### 5.1 Hackathon cut, on the stack that exists

```text
   Kanari MCP over HTTP JSON-RPC                     Kanari GET /api/signals
   active_fires(country=DZ|TN, hours)                (public posts, text included)
   fire_archive_search / fire_details (replay)              |
                 |                                          |
                 v                                          v
   backend-core  api/sources/kanari.py   fetch, 10-min in-process cache, timeout, attribution
                 |
                 v
          api/incidents.py   optional router, same try/except pattern as passport and weather
            1. normalize  -> Signal{source, id, lat, lon, time, url, text?}
            2. region     -> inside the TN/DZ bbox (geoBoundaries outline later)
            3. latitude   -> lat < 34: flag likely_industrial, hide by default
            4. dedupe     -> ticket id = Kanari cluster id; posts = place + UTC day
            5. context    -> core.calendar_for_bbox(cell bbox, 2001, 2025) for that calendar month
            6. status     -> Satellite overlap | No satellite support | Mentioned
                 |
                 v
     GET /incidents?hours=72      GET /incidents/replay      GET /incidents/{id}
                 |
                 v
   frontend  src/pages/IncidentsPage.jsx at /dashboard/incidents
             list + a layer on the existing Leaflet FireMap + a ticket card
             card: place, time, sensors, status word, cell history, public links,
             "unverified, not an official alert", links to ONPC (TN) and DGPC (DZ)
```

**Why this shape:**

- **No worker and no database.** `render.yaml` defines one free web service that sleeps when idle. Tickets are computed when someone asks for them, from cached replies, and the ticket id is deterministic.
- **No LLM in the path.** Each step is a function, as model-context.md asks.
- **Kanari through MCP over plain HTTP.** It is the only Kanari call tonight that filtered by country. `/api/events` cuts off at 2,000 clusters worldwide.
- **Context needs the real parquet.** Step 5 works only once `data/harmonized.parquet` exists on backend-core. With `scripts/make_fake_data.py`, the card would show fake history, so it must print the `synthetic` flag the validation file already carries.
- **The FIRMS check is a stretch goal.** Kanari clusters already carry `viirsCount`. A public post with no cluster would need our own FIRMS area-API call (2 transactions per day for a small box). That means porting `firms_tool/area_api.py` to backend-core and setting `FIRMS_MAP_KEY` on Render. Without it, those tickets say "Mentioned".
- **Replay is needed for the demo.** It is November, and TN had 0 active clusters tonight, so the live list will probably be empty. `/incidents/replay` serves real archived fires: the Mateur fires in the Bizerte box, El Milia and Tamalous in DZ on 2026-11-12, and El Ksour / Le Krib in TN on 2026-10-28. They come from saved `fire_archive_search` / `fire_details` replies and are labelled **Replay** on screen. The same files are the test fixtures, so the demo works offline.

### 5.2 Ticket record (hackathon)

```json
{
  "id": "kanari:<cluster id> | post:<place>:<YYYY-MM-DD>",
  "mode": "live | replay",
  "place": "...", "country": "TN | DZ",
  "lat": 0.0, "lon": 0.0, "location_precision": "cluster | place",
  "first_seen": "...", "last_seen": "...",
  "satellite": {"viirs": 0, "goes": 0, "mtg": 0, "max_frp_mw": 0, "source_confidence": "possible|probable|corrobore"},
  "status": "Satellite overlap | No satellite support | Mentioned",
  "flags": ["likely_industrial"],
  "context": {"cell_bbox": [9.5, 36.5, 10.0, 37.0], "month": 10,
              "median_2001_2025": 0.0, "critical_month": true, "synthetic": false},
  "public": [{"source": "bluesky", "url": "...", "excerpt": "...", "at": "..."}],
  "public_note": "no public mention found (not evidence of no fire)",
  "attribution": "Source: kanari.io (CC BY 4.0)"
}
```

These values are shapes, not measured figures. The product note's three confidence dimensions stay as separate fields, never one score:

- **fire:** the satellite counts plus the latitude flag;
- **location:** `location_precision`;
- **corroboration:** the number of independent sources, where re-posts count once.

### 5.3 Target architecture after the hackathon

The product note's pipeline, kept modular, in order of value for this region:

1. **Connectors** behind one interface (`health_check`, `poll`, `normalize`): Kanari MCP and signals, FIRMS area API, GDELT DOC (French and Arabic, `sourcecountry:AG`/`TS`), Bluesky `searchPosts` (French and Arabic terms), and ONPC / DGPC if a readable feed exists.
2. **Store and schedule:** Postgres with PostGIS for signals, tickets and audit, plus a poll every ~10 minutes from a cron job, not from the sleeping web service.
3. **Pre-filter:** keywords and language (the product note's seed terms), the region gate and the latitude gate.
4. **Classifier:** a cheap model only on posts that survive the pre-filter, using the product note's 9 labels plus firsthand / relayed / news. backend-core already defaults to `claude-haiku-5-5` for the passport.
5. **Clustering:** distance and time windows, with re-posts collapsed, attached to Kanari or FIRMS clusters.
6. **Review:** the 8 statuses, a reviewer, merge / dismiss, and an audit log.
7. **Two agents, one exchange** (model-context.md), only for an open question or a mixed panel.
8. **X**, only with written provider terms and only if phases 1 to 7 leave a measurable gap.

## 6. Hackathon build steps

Estimates are for one person. Start only after the calibration, the parquet and the frontend wiring have owners.

| # | Step | Where | Est. |
|---|---|---|---|
| 1 | `api/sources/kanari.py`: MCP POST, SSE parsing, cache, timeout. Save fixtures from `active_fires` (DZ, TN), `fire_archive_search` and two `fire_details`. | backend-core | 1 h |
| 2 | `api/incidents.py`: normalize, the region and latitude gates, dedupe, status, and context through `core.calendar_for_bbox`. Optional router in `api/main.py`. | backend-core | 1.5 h |
| 3 | Tests on fixtures: a 31.6°N cluster is flagged and hidden; a Mateur cluster gets Bizerte-cell context; an empty list carries the "not evidence of no fire" note; the same cluster twice gives one ticket; fake data shows `synthetic: true`. | backend-core `tests/` | 1 h |
| 4 | `IncidentsPage.jsx`, a Sidebar link, a layer on `FireMap`, a Live/Replay toggle, attribution, a disclaimer and ONPC/DGPC links. | frontend | 1.5–2 h |
| 5 | One pitch slide: "Social posts match 0.5% of fires worldwide. Our tickets start from satellite data and 25 years of history." | slides | 0.5 h |

## 7. Risks

- **Kanari is a single free service** with no SLA and an unknown rate limit. Mitigations: the cache, replay fixtures, and a visible "source unavailable" state.
- **An empty live list** in November. Use Replay and say so on screen.
- **Context depends on the main product.** There is no real history until `data/harmonized.parquet` exists, and cells outside TN/DZ north of 34°N have no rows.
- **Licensing.** Show the attribution on every ticket. Show excerpts only while the source still lists the post.
- **Safety.** Every ticket is an unverified mention, with no dispatch and no alert. Link the official civil-protection pages and invent no phone numbers.
- **Scope.** Every hour spent here is taken from the calibration and the frontend wiring.

## 8. How we would measure it (after the hackathon)

Use the product note's metrics: precision, recall, time-to-detection, location error, duplicate and merge rates, and cost per useful ticket. Score them against a replay set from Kanari's archive since 3 Aug 2026 (DZ and TN, north of 34°N) plus press reports. Track one extra regional number: **the share of satellite tickets that gained any public mention.** It is 0 of 180 in Kanari's archive for DZ and TN, against 0.5% worldwide. A French and Arabic layer would have to move it.
