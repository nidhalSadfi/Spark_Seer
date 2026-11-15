# Fire incident agents: architecture and flow

This document explains how the incident agents in [`incident_agents/`](../incident_agents/) turn a typed request ("Tunisie", "Kabylie, Algeria", "Var et Bouches-du-Rhône") into incident tickets.

Each ticket lists the sources that reported it, with links. A satellite check then marks each ticket as backed by satellite data or not. For setup, commands and limits, see [`incident_agents/README.md`](../incident_agents/README.md).

**Tickets are unverified public mentions.** They are not official alerts, and the tool never dispatches anyone.

## 1. The big picture

The system has three layers:

- **Entry points:** you type a query on a local page, or a saved schedule fires.
- **The run pipeline:** one Groq model call plans the search, plain code fetches from each source, and a Groq judge agent per source reads the posts.
- **Storage:** everything lands in one SQLite file. Every step also writes an event that the page shows live.

```mermaid
flowchart LR
    subgraph Entry["Entry points"]
        UI["Web page<br/>query box + ticket list"]
        SCH["Scheduler<br/>checks every 30 s"]
        CLI["CLI<br/>python -m incident_agents run"]
    end

    subgraph Server["FastAPI on 127.0.0.1:8765"]
        API["POST /api/runs<br/>GET /api/tickets<br/>/api/schedules"]
        SSE["GET /api/events/stream<br/>server-sent events"]
    end

    subgraph Pipeline["One run (pipeline.run)"]
        PLAN["Planner agent<br/>Groq gpt-oss-20b"]
        VAL["Validator<br/>code + geocoder"]
        FETCH["Source connectors<br/>plain code"]
        FILT["Filter<br/>window, terms, dedupe"]
        JUDGE["Judge agents<br/>one per source, Groq"]
        LOC["Locator<br/>Open-Meteo / Nominatim"]
        TIX["Ticketer<br/>merge by place and time"]
        SAT["Satellite check<br/>Kanari + FIRMS"]
    end

    subgraph Sources["External sources"]
        K["Kanari<br/>verified signals + fire clusters"]
        B["Bluesky<br/>searchPosts"]
        X["X via TwitterAPI.io"]
        G["GDELT<br/>news headlines"]
        F["NASA FIRMS<br/>VIIRS + MODIS NRT"]
    end

    DB[("SQLite<br/>runs, events, tickets,<br/>ticket_sources, verdicts,<br/>schedules, usage")]

    UI --> API
    SCH --> PLAN
    CLI --> PLAN
    API --> PLAN
    PLAN --> VAL
    VAL -- "errors, max 2 retries" --> PLAN
    VAL --> FETCH
    FETCH --> K & B & X & G
    FETCH --> FILT --> JUDGE --> LOC --> TIX --> SAT
    SAT --> K & F
    Pipeline -- "events + tickets" --> DB
    DB --> SSE --> UI
    DB --> API
```

## 2. One run, step by step

```mermaid
sequenceDiagram
    autonumber
    actor U as User or schedule
    participant P as Planner (Groq)
    participant V as Validator (code)
    participant S as Sources
    participant J as Judge (Groq, per source)
    participant L as Locator
    participant T as Ticketer
    participant Sat as Kanari + FIRMS
    participant DB as SQLite + events

    U->>P: "Tunisie", last 24 h
    P->>V: zones [Tunisia, TN], extra keywords
    alt zone or country invalid
        V-->>P: errors as plain sentences
        P->>V: corrected plan (at most 2 retries)
    end
    V->>DB: event plan ok
    V->>S: per-language searches (ar, fr)
    S-->>V: posts and headlines with URL, publish time, fetch time
    V->>DB: event fetch ok / error per source
    Note over V: filter: inside time window,<br/>contains a search term, no duplicate URL
    V->>J: new items in batches of 8 (cached verdicts reused)
    J-->>V: category, timing, place phrase, country
    Note over V: code drops a place not written in the post<br/>and a country outside the plan
    V->>L: ticketable items with a place
    L-->>V: lat, lon, precision, radius
    V->>T: located reports
    T->>DB: create or update ticket, add source rows
    T->>Sat: fires near the ticket, time window +-24 h
    Sat-->>T: matching clusters / detections
    T->>DB: status Satellite overlap / No satellite support / Mentioned
    DB-->>U: event run done, tickets on the page
```

### What each step does

| # | Step | Who | Input | Output | Event shown on the page |
|---|---|---|---|---|---|
| 1 | **plan** | Groq `openai/gpt-oss-20b`, strict JSON schema | The free text, in any language | Zones (name, ISO-2 country, kind) and extra keywords per language | `plan · ok  Tunisia (TN)` |
| 2 | **validate** | Code | The model's plan | Each zone resolved to a bounding box. Countries use a built-in mainland table, and regions go through Nominatim. | `plan · running … attempt 2` on a retry |
| 3 | **fetch** | One connector per source, plain code, no model | Zones, languages, search terms, time window | `Record`s: source, URL, text, author, publish time, fetch time | `fetch/bluesky · ok  fr: 32 posts` |
| 4 | **filter** | Code | All records | Records inside the window that contain a term, deduped by URL. A Kanari-verified post wins over the same Bluesky post. | `filter · ok  kept 65 of 74` |
| 5 | **judge** | One Groq agent per source | Batches of 8 posts, treated as untrusted data | Category, timing, firsthand or not, place phrase, country | `judge/x · ok  0 of 38 look like a current fire report` |
| 6 | **locate** | Code: Open-Meteo, then Nominatim | The place phrase and country | Point, precision (town, area, region) and radius in km | `locate · ok  3 located` |
| 7 | **ticket** | Code | Located reports | A new ticket, or an update to one within the larger radius (at least 10 km) and 48 h | `ticket · created  #12 …` |
| 8 | **satellite** | Code: Kanari clusters, then FIRMS NOAA-20/21 and MODIS | Ticket point and time | Ticket status and the matching detections | `satellite · ok  #12: Satellite overlap` |

## 3. Why there are two kinds of agent

The model is used only where language understanding is needed. Everything that can be checked is done in code.

```mermaid
flowchart TB
    subgraph LLM["Groq model (light, cheap)"]
        A1["Planner<br/>free text to zones + keywords"]
        A2["Judge per source<br/>is this a current vegetation fire? where?"]
    end
    subgraph Code["Plain code (deterministic, checkable)"]
        C1["Validate zones against<br/>country table and geocoder"]
        C2["Fetch with each API's own rules<br/>pacing, caps, caching"]
        C3["Check the judge:<br/>place must appear in the text,<br/>country must be in the plan"]
        C4["Geocode, merge, satellite check"]
    end
    A1 --> C1
    C1 -- "errors fed back" --> A1
    C2 --> A2 --> C3 --> C4
```

- **The planner** maps free text in any language to sources' inputs: country codes, languages and search terms for each language. Known countries only search their own languages; Algeria and Tunisia search Arabic and French, and France searches French.
- **The judge** separates wildfires from building fires, figurative uses of "fire", old news and fires abroad. It must copy the place name exactly as written, so it cannot invent a location.
- **The code** checks every model answer and drops anything that fails. It never repairs one.

## 4. Sources and what each one gives

| Source | Access | What we take | Used for |
|---|---|---|---|
| **Kanari** (kanari.io) | No key, CC BY 4.0, at most 1 call per minute, cached 5 min | Social posts it has already matched to satellite fires, and fire clusters by country | Reports that are already confirmed (they skip the judge), plus the satellite check |
| **Bluesky** | Handle + app password | `searchPosts` per term and language | Firsthand posts |
| **X** via TwitterAPI.io | API key, free tier 1 request per 5 s | Advanced search, 20 tweets per page, capped at 4 pages per run and 40 per day | Firsthand posts and news accounts |
| **GDELT DOC 2.0** | No key, 1 request per 5 s | Headlines by source country and language | Press coverage (not yet verified: rate-limited during every test) |
| **NASA FIRMS** | Map key | VIIRS NOAA-20, NOAA-21 and MODIS near-real-time detections in a small box | A second satellite check after Kanari |
| **Open-Meteo / Nominatim** | No key | Coordinates, feature type, admin regions | Locating places and zone boxes |

## 5. How a ticket is built

```mermaid
flowchart TD
    R["Judged report<br/>ticketable + place + country"] --> G{"Geocoded inside<br/>the zone?"}
    G -- no --> U["Counted as relevant but unlocated<br/>URL kept in the event"]
    G -- yes --> D{"URL already<br/>on a ticket?"}
    D -- yes --> Skip["Skip, never counted twice"]
    D -- no --> M{"Existing ticket, same country,<br/>within max(10 km, radii),<br/>within 48 h?"}
    M -- yes --> Up["Add source row to that ticket<br/>keep the most precise location"]
    M -- no --> New["Create ticket"]
    Up --> S["Satellite check"]
    New --> S
    S --> S1{"Kanari or FIRMS fire<br/>near the point, +-24 h?"}
    S1 -- yes --> OK["Satellite overlap"]
    S1 -- "no, check ran" --> NO["No satellite support"]
    S1 -- "check not possible" --> MEN["Mentioned"]
```

Each ticket stores:

- **Where:** place name, country, point, precision and radius.
- **When:** first and last time it was seen.
- **Status:** one of the three satellite outcomes above.
- **Satellite matches:** the clusters or detections that matched.
- **One row per source:** source name, URL, publish time, fetch time, a 280-character excerpt and the judge's verdict.

**Satellite-only tickets** (the `--satellite-only` option) also open tickets from Kanari fire clusters that no post mentions. In Algeria, Tunisia and Libya, clusters south of 34°N are skipped, because they are almost always oil and gas flares.

## 6. Step signals for a frontend

Every step writes a row to the `events` table:

- the run id;
- the step and the source;
- a status: `started`, `running`, `progress`, `ok`, `error`, `skipped`, `created`, `updated` or `done`;
- a message, with secrets redacted;
- optional JSON data.

The page subscribes to `GET /api/events/stream`, a server-sent event stream. The stream resumes from `Last-Event-ID` after a reconnect, so a future React or Leaflet frontend can drive a progress view from the same stream.

```mermaid
stateDiagram-v2
    [*] --> started
    started --> plan
    plan --> plan: retry with errors
    plan --> fetch
    fetch --> filter
    filter --> judge
    judge --> locate
    locate --> ticket
    ticket --> satellite
    satellite --> done
    plan --> error: plan still invalid after retries
    judge --> locate: daily model cap, rest skipped
    done --> [*]
    error --> [*]
```

A failing source writes an `error` event for that source, and the run continues with the others. Only a failure in planning, or an unexpected exception, ends the run with `run · error`.

## 7. Limits and safeguards

| Concern | Safeguard |
|---|---|
| Groq free tier: 1,000 requests per day, 8,000 tokens per minute | Pacing on the rate-limit headers. Verdicts are cached by URL, so a scheduled run pays only for new posts. Runs stop calling the model at 900 calls per day. |
| X free tier | 6 s between requests, and the page caps per run and per day above |
| Kanari's request to keep calls low | One country-wide cluster call per run, cached and filtered locally |
| Prompt injection in posts | Posts are passed as data; the judge's output is schema-checked and validated in code |
| Invented locations | A place must appear in the post text; coordinates come only from geocoders |
| Overlapping runs | One run at a time (409 if busy); schedules every 15 min at the shortest |
| Exposure | The server binds to 127.0.0.1 only; keys stay in the gitignored `.env` |

## 8. Code map

| File | Role |
|---|---|
| `__main__.py` | CLI: `run`, `serve`, `schedule-loop`, `tickets` |
| `config.py` | Paths, secret lookup and redaction, caps |
| `llm.py` | Groq client: strict JSON, pacing, retries, call counting |
| `planner.py` | Planner agent, validation loop, keyword rules per language |
| `geo.py` | Country boxes, zone boxes, place geocoding with precision |
| `sources/` | One connector per source: `kanari`, `bluesky`, `twitter`, `gdelt`, `firms` |
| `judge.py` | Judge agent and the code checks on its answers |
| `tickets.py` | Merging, upsert, satellite check and status |
| `pipeline.py` | One run from end to end, emitting every event |
| `store.py` | SQLite (WAL) tables and queries |
| `scheduler.py` | Background thread that runs due schedules |
| `server.py` + `static/index.html` | FastAPI endpoints, the event stream and the page |
| `../tests/test_incident_agents.py` | 20 offline tests with fake model and sources |

## 9. Not done yet

- Bluesky Jetstream, the live firehose: tested, not wired in.
- Review actions on tickets (confirm or dismiss).
- The 25-year cell history from the harmonization work on Tunisia and Algeria tickets.
- Official civil-protection feeds.
- A working GDELT path from this machine.
