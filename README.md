# EmberGuard

**One consistent wildfire record for the whole planet, from 2001 to today, built from NASA's MODIS and VIIRS satellites.**

🌍 **Live: <https://spark-seer.duckdns.org/>** · dashboard: <https://spark-seer.duckdns.org/dashboard>

NASA Space Apps Challenge 2026 (Tunis) · challenge *Harmonization of MODIS and VIIRS Hot Spots*

---

## Why

NASA's two fire-detecting satellite families see fires differently. VIIRS's 375 m pixels find several
times more hotspots than MODIS's 1 km pixels, so joining their records naively creates a fake jump in
fire activity in 2012, when VIIRS starts. In November 2026 the S-NPP satellite's record ended too, so
long-term series need a second hand-over, to NOAA-20.

EmberGuard harmonizes both families into one record that is consistent over time, keeps it updated
every three hours, and shows how reliable each number is.

## What you can do with it

| Page | What it shows |
|---|---|
| **Overview** | latest month per region, compared with that region's own history |
| **Global Fire Map** | live NASA FIRMS hotspots worldwide (aggregated cells when zoomed out) |
| **Activity Calendar** | year × month fire calendar for any area or drawn box, unusual months flagged, partial and missing months shown as such |
| **Analytics** | seasonality, trends, rankings, and the validation of the harmonization |
| **Data Explorer** | the monthly numbers as a table, with CSV export |
| **Fire Reports** | community fire reports, reviewed and checked against nearby satellite detections and weather |
| **Social Alerts** | AI agents that look for fire reports in social media and news |
| **Processing Center** | every 3-hourly update: its checks, the AI reviewers' verdict and reasons, and the models used |

## How it works

```
NASA FIRMS ──► worker (every 3 h) ──► PostgreSQL ──► FastAPI ──► React dashboard
  MODIS C6.1      fetch · filter · count        approved      read-only
  VIIRS S-NPP     harmonize · 4 hard gates      data +        API
  NOAA-20/21      3 AI reviewers · publish      history
```

1. **Harmonization.** 548 million archive detections (2001-2025) go through the same filters as live
   data: confidence rules, plus a world mask of 153,371 permanent heat sources such as gas flares,
   industry and volcanoes. They are counted per 0.5° cell and month. MODIS is calibrated to VIIRS
   with a ratio per cell and calendar month, fitted on 2012-2019 and tested on 2020-2021. An
   S-NPP → NOAA-20 factor carries the record past S-NPP's end.
2. **Live updates.** A worker downloads new FIRMS detections every three hours and recomputes only
   the affected cell-months.
3. **Checks.** Four hard rules in code: valid values, no detection lost or double-counted, missing data
   never stored as zero, and the frozen calibration. Then three AI reviewer agents (filter,
   harmonization, validation) read summaries of the results. They can **hold or reject** an update,
   but never approve one that failed a rule, and any number they quote must be in their data.
4. **Publishing.** Approved updates replace the published data in one database transaction, so a
   failed update can never damage the published data. Every version is kept.

## Results (held-out test years)

| | Error |
|---|---:|
| Harmonized MODIS → VIIRS, world (cell-month calibration) | **5.4 %** |
| Per continent | 4.9 – 8.9 % |
| Without harmonization (raw MODIS vs VIIRS) | 76 % |
| S-NPP → NOAA-20 hand-over (r = 1.017, accepted because it beats no correction) | 1.9 % (2.3 % uncorrected) |

The 30-60°S band (southern South America, South Africa, southern Australia, New Zealand) is weaker,
at 11.8 %, and is flagged as such. Full tables: `backend/data/world/VALIDATION.md`, produced by
`scripts/build_world.py`.

## Data

All fire data comes from **NASA FIRMS** (<https://firms.modaps.eosdis.nasa.gov/>):

- MODIS Collection 6.1 active fires (Terra and Aqua): archive 2001-2025 and near-real-time
- VIIRS 375 m active fires: Suomi NPP (2012 to 2 Nov 2026), NOAA-20 (2018 onward), NOAA-21 (near-real-time)
- Access: yearly archive files, the FIRMS Area API (live polling) and the Data Availability API

Other sources: Natural Earth boundaries, Open-Meteo and Meteomatics weather.

## Tech stack

- **Backend:** Python 3.11, FastAPI, pandas, PyArrow, Shapely, SQLAlchemy 2, psycopg 3, Alembic, PostgreSQL 16
- **AI:** CrewAI reviewer agents on OpenRouter free models (NVIDIA Nemotron, with automatic fallback); fire incident agents for Social Alerts
- **Frontend:** React 19, Vite, React Query, ECharts, Leaflet
- **Operations:** Docker, a Google Cloud VM, DuckDNS
- **Quality:** about 500 automated tests, including PostgreSQL integration tests and tests that check the optimized processing gives identical numbers

## Run it locally

Needs Docker (for Postgres), Python 3.11+ and Node `^20.19` or `>=22.12`.

```bash
cp .env.example .env                 # add the keys you have; each missing key only switches off its own part
docker compose up -d db
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
set -a; . <(tr -d '\r' < ../.env); set +a
alembic upgrade head && python -m worker.bootstrap
cd ../frontend && npm install && cd ..

./run-world.sh db                    # database, API, agents and dashboard → http://localhost:5173/dashboard
```

Without a database, `./run-world.sh` shows the committed Tunisia and Algeria history (calendar,
analytics, Data Explorer). The full guide, covering keys, the worker, the world dataset and
troubleshooting, is in **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**.

## Repository

```
backend/                            FastAPI API, processing science, worker, tests
backend/processing/                 deterministic stages: filters, mask, grid, harmonization, gates
backend/worker/                     FIRMS polling, processing runs, AI review, publishing
backend/harmonization-modis-viirs/  FIRMS tooling and the fire incident agents (Social Alerts)
frontend/                           React dashboard
docs/processing/ARCHITECTURE.md     the processing specification
docs/DEVELOPMENT.md                 the developer guide
run-world.sh                        one-command local launcher
```

## Use of AI

- **In the product:** the CrewAI reviewer agents only review summaries; they never compute or change
  numbers, and they can only make a decision stricter. The Processing Center shows every AI decision
  and the model behind it. The Social Alerts agents use a language model to find and summarize fire
  reports.
- **In development:** parts of the code, tests and documentation were written with Claude Code
  (Anthropic). Those commits carry a `Co-Authored-By: Claude …` line. Scientific choices were made by
  the team.
