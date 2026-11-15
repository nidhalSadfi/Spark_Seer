<div align="center">

<img src="docs/images/banner.svg" alt="Spark Seer: one consistent wildfire record for the whole planet, from NASA MODIS and VIIRS" width="100%">

<br>

[![Live demo](https://img.shields.io/badge/live_demo-spark--seer.duckdns.org-f97316?style=for-the-badge&logo=googlechrome&logoColor=white)](https://spark-seer.duckdns.org/)
[![Dashboard](https://img.shields.io/badge/open-dashboard-0b1220?style=for-the-badge)](https://spark-seer.duckdns.org/dashboard)
[![NASA Space Apps 2026](https://img.shields.io/badge/NASA_Space_Apps-2026_·_Tunis-0b3d91?style=for-the-badge&logo=nasa&logoColor=white)](https://www.spaceappschallenge.org/)

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![Vite](https://img.shields.io/badge/Vite-646CFF?logo=vite&logoColor=white)
![CrewAI](https://img.shields.io/badge/CrewAI-reviewer_agents-FF5A50)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-~500-22c55e)

**Challenge:** *Harmonization of MODIS and VIIRS Hot Spots* · **Team:** PizzaNeptune 🍕🪐

[Why](#-why-this-exists) · [Tour](#-a-tour-of-the-dashboard) · [How it works](#-how-it-works) · [Results](#-results) · [Architecture](#-architecture) · [Run it](#-run-it-locally) · [Team](#-team)

</div>

---

<p align="center">
  <a href="https://spark-seer.duckdns.org/"><img src="docs/images/landing.jpg" alt="Spark Seer landing page with an interactive globe of fire hotspots" width="100%"></a>
</p>

## 🔥 Why this exists

NASA watches the planet's fires with two satellite families, and they **don't see fires the same way**:

| | MODIS (Terra, Aqua) | VIIRS (S-NPP, NOAA-20, NOAA-21) |
|---|---|---|
| Pixel size | 1 km | **375 m** |
| Record starts | 2000 / 2002 | 2012 |
| Hotspots found | baseline | **several times more** |

Glue the two records together naively and you get a **fake jump in fire activity in 2012**, the year
VIIRS arrives. A second break appeared in November 2026, when the S-NPP satellite's record ended and
the long-term series had to hand over to NOAA-20.

**Spark Seer harmonizes both families into one record that is consistent over time**, updates it
every three hours, and shows how reliable each number is.

<p align="center">
  <img src="docs/images/chart-harmonization.svg" alt="Line chart: raw MODIS runs about 3.5 times below raw VIIRS; the harmonized record puts 2001-2011 on the VIIRS scale" width="100%">
</p>

---

## 🧭 A tour of the dashboard

> Everything below is a live page at **<https://spark-seer.duckdns.org/dashboard>**. Pick an area, a sensor
> (Harmonized, MODIS or VIIRS) and a year range in the top bar; every page follows those filters.

### Overview: the latest month, against its own history

<img src="docs/images/overview.jpg" alt="Overview page with KPI tiles, a hotspot map preview and top fire regions" width="100%">

KPI tiles compare the latest month with the same calendar month in earlier years, next to a live map
preview and a ranking of the world's most active fire regions.

### Activity Calendar: the core view

<img src="docs/images/activity-calendar.jpg" alt="Fire Activity Calendar: a year by month heatmap, with the inspector open on August 2012" width="100%">

A **year × month heatmap** of harmonized hotspots for any area or drawn box. Unusual months are
outlined (robust z-score ≥ 3), partial months are marked, and **missing data is blank, never zero**.
Click a cell to see its count, the historical median, the anomaly and the same month in other years.

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/images/fire-map.jpg" alt="Global Fire Map with a live hotspot cluster selected">
      <p><b>Global Fire Map.</b> Near-real-time NASA FIRMS detections. Click a fire for its radiative
      power, satellites and persistence, replay the last 24 h, or report a fire the satellites missed.</p>
    </td>
    <td width="50%" valign="top">
      <img src="docs/images/analytics.jpg" alt="Historical Analytics with yearly totals by sensor">
      <p><b>Analytics.</b> Yearly totals by sensor, seasonality, monthly anomalies and rankings, with
      raw and harmonized values kept visibly apart.</p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/images/data-explorer.jpg" alt="Data Explorer table of monthly harmonized records with CSV export">
      <p><b>Data Explorer.</b> Every monthly number (raw MODIS, raw VIIRS, harmonized, support,
      z-score, flag and coverage) as a sortable table, with CSV export and an area report.</p>
    </td>
    <td width="50%" valign="top">
      <img src="docs/images/social-alerts.jpg" alt="Social Alerts agents graph">
      <p><b>Social Alerts.</b> AI agents search Bluesky, X, GDELT and Kanari for fire reports, then
      locate them and check them against satellite detections. These are unverified leads, never official alerts.</p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/images/processing-center.jpg" alt="Processing Center showing the scheduler, published versions and job counts">
      <p><b>Processing Center.</b> Each 3-hourly run in the open: scheduler state, raw and pending
      detections, the published dataset version and every job.</p>
    </td>
    <td width="50%" valign="top">
      <img src="docs/images/ai-review.jpg" alt="Job detail drawer showing the three-agent AI review and its verdict">
      <p><b>AI review, explained.</b> For each job, the three reviewer agents' roles, the verdict, its
      reasons and the model used. The AI can make a decision stricter, never looser.</p>
    </td>
  </tr>
</table>

**Fire Reports** (community reports, reviewed and checked against nearby detections and weather)
completes the set.

---

## ⚙️ How it works

### 1. Two satellite families → one reference

Each period of the record uses the best sensor available, converted to a single scale (VIIRS S-NPP):

```mermaid
gantt
    title Which sensor drives the harmonized value
    dateFormat YYYY-MM-DD
    axisFormat %Y
    section Satellites
    MODIS Terra / Aqua (1 km)          :active, m, 2001-01-01, 2026-11-15
    VIIRS S-NPP (375 m)                :s, 2012-01-20, 2026-11-02
    VIIRS NOAA-20 (375 m)              :n, 2018-04-01, 2026-11-15
    section Harmonized record
    MODIS × k  (cell × calendar month) :crit, h1, 2001-01-01, 2012-01-01
    S-NPP count (reference)            :h2, 2012-01-01, 2026-11-02
    NOAA-20 × r  (r = 1.017)           :crit, h3, 2026-11-02, 2026-11-15
    section Calibration
    Fit k                              :f, 2012-01-01, 2020-01-01
    Test k (held out)                  :t, 2020-01-01, 2022-01-01
```

- **k** is a MODIS → VIIRS ratio for every 0.5° cell and calendar month, shrunk toward the regional value
  where data is thin. It is fitted on 2012-2019 and scored on 2020-2021, which the fit never saw.
- **r** carries the record past the end of S-NPP. It is approved only if it beats "no correction"
  on held-out years.

### 2. From 548 million detections to a 0.5° calendar

```mermaid
flowchart LR
    A["🛰️ NASA FIRMS<br/>548 M archive detections<br/>2001–2025 + live NRT"] --> B["Filters<br/>confidence rules"]
    B --> C["Static-source mask<br/>153,371 gas flares,<br/>industry, volcanoes"]
    C --> D["0.5° grid<br/>count per cell × month<br/>per sensor"]
    D --> E["Harmonize<br/>MODIS × k · S-NPP · NOAA-20 × r"]
    E --> F["Completeness<br/>partial / missing ≠ 0"]
    F --> G[("📅 Fire activity<br/>calendar")]
    classDef src fill:#0b3d91,stroke:#60a5fa,color:#fff
    classDef step fill:#111a2e,stroke:#f97316,color:#e5e7eb
    classDef out fill:#ea580c,stroke:#fdba74,color:#fff
    class A src
    class B,C,D,E,F step
    class G out
```

Live data goes through **exactly the same filters** as the archive, so history and new months stay comparable.

### 3. Every 3 hours: fetch → check → review → publish

```mermaid
flowchart TD
    S(["⏰ Scheduler · every 3 h"]) --> F["Fetch FIRMS NRT<br/>MODIS · S-NPP · NOAA-20 · NOAA-21"]
    F --> D{"New or changed<br/>detections?"}
    D -- no --> NC(["no_change<br/>(no AI call)"])
    D -- yes --> R["Recompute only the<br/>affected cell-months"]
    R --> G{"4 hard gates<br/>in code"}
    G -- any gate fails --> RJ(["❌ reject"])
    G -- all pass --> AI["🤖 3 AI reviewers<br/>Filter · Harmonization · Validation"]
    AI --> V{"Verdict"}
    V -- hold / reject --> H(["⏸️ hold / reject<br/>staging kept for review"])
    V -- "approve<br/>(or AI unavailable)" --> P["Promote in ONE<br/>database transaction"]
    P --> PUB(["✅ new dataset version<br/>previous one kept in history"])

    classDef gate fill:#1f2937,stroke:#facc15,color:#fde68a
    classDef ok fill:#14532d,stroke:#22c55e,color:#dcfce7
    classDef bad fill:#7f1d1d,stroke:#ef4444,color:#fee2e2
    classDef ai fill:#312e81,stroke:#818cf8,color:#e0e7ff
    class G,D,V gate
    class PUB,P ok
    class RJ,H bad
    class AI ai
```

| Hard gate | What it guarantees |
|---|---|
| **G1** schema & range | counts are valid, keys unique, every affected cell-month appears exactly once |
| **G2** conservation | no detection is lost or double-counted, checked per cell-month **and** sensor |
| **G3** missing ≠ zero | an unavailable sensor gives *null + flag*, never a fake 0 |
| **G4** frozen calibration | live runs never silently refit `k` or `r` |

**Guardrails on the AI.** The reviewers read summaries only, never raw rows, and they never compute
or change a number. Any number they quote must appear in their input, or their answer is thrown out.
They can turn *approve* into *hold* or *reject*, but they can never approve a run that failed a gate.
If every model is down, the gates alone decide, so the AI's availability never stops the data.

---

## 📊 Results

<p align="center">
  <img src="docs/images/chart-accuracy.svg" alt="Bar chart of held-out error: 76 percent without harmonization, 5.4 percent world, 8.9 percent worst continent, 11.8 percent in the 30-60 degrees south band, 1.9 percent for the S-NPP to NOAA-20 hand-over" width="100%">
</p>

| Held-out test | Error |
|---|---:|
| Harmonized MODIS → VIIRS, whole world (cell-month calibration) | **5.4 %** |
| Per continent | 4.9 – 8.9 % |
| Without harmonization (raw MODIS vs VIIRS) | 76 % |
| S-NPP → NOAA-20 hand-over (r = 1.017, accepted because it beats no correction) | 1.9 % (2.3 % uncorrected) |

The **30-60°S band** (southern South America, South Africa, southern Australia, New Zealand) is
weaker, at 11.8 %, and is **flagged as such in the product rather than hidden**. Full tables are in
`backend/data/world/VALIDATION.md`, produced by `backend/scripts/build_world.py`.

---

## 🏗️ Architecture

```mermaid
flowchart LR
    subgraph EXT["External sources"]
        FIRMS["🛰️ NASA FIRMS<br/>Area API · archives ·<br/>Data Availability API"]
        OR["🤖 OpenRouter<br/>free LLMs"]
        WX["🌦️ Open-Meteo ·<br/>Meteomatics"]
        SOC["📰 Bluesky · X ·<br/>GDELT · Kanari"]
    end

    subgraph VM["Cloud VM · Docker Compose"]
        WEB["Caddy<br/>HTTPS · static dashboard<br/>/api/* proxy"]
        API["FastAPI<br/>read-only GETs<br/>+ /reports"]
        WK["Worker<br/>scheduler · processing ·<br/>CrewAI review · promotion"]
        AG["Fire-incident agents<br/>Social Alerts<br/>(started by the API)"]
        DB[("PostgreSQL 16<br/>raw · staging · approved ·<br/>history · versions · jobs")]
    end

    USER(["👩‍🚒 Browser<br/>React 19 dashboard"]) -->|"HTTPS (DuckDNS)"| WEB
    WEB --> API
    API -->|reads| DB
    WK -->|writes| DB
    FIRMS --> WK
    OR --> WK
    WX --> API
    SOC --> AG
    AG --> API

    classDef ext fill:#0b3d91,stroke:#60a5fa,color:#fff
    classDef svc fill:#111a2e,stroke:#f97316,color:#e5e7eb
    classDef db fill:#1e3a8a,stroke:#93c5fd,color:#fff
    class FIRMS,OR,WX,SOC ext
    class WEB,API,WK,AG svc
    class DB db
```

Design rules the code enforces, each with a test:

- **The API is read-only.** It never imports the worker or an LLM client.
- **Published data can't be damaged by a failed run.** Results go to staging and are promoted in one transaction.
- **No new data, no AI call.** Unchanged polls stop before CrewAI is even imported.
- **Restart-safe.** All schedule and job state lives in PostgreSQL, with one active job per kind.

The full specification is in **[docs/processing/ARCHITECTURE.md](docs/processing/ARCHITECTURE.md)**, and the
server setup is in **[DEPLOY.md](DEPLOY.md)**.

### Tech stack

| Layer | Tools |
|---|---|
| **Backend** | Python 3.11, FastAPI, pandas, PyArrow, Shapely, SQLAlchemy 2, psycopg 3, Alembic, PostgreSQL 16 |
| **AI** | CrewAI reviewer agents on OpenRouter free models (NVIDIA Nemotron, with automatic fallback); fire-incident agents for Social Alerts |
| **Frontend** | React 19, Vite, React Query, ECharts, Leaflet |
| **Operations** | Docker Compose, Caddy (automatic HTTPS), a cloud VM, DuckDNS |
| **Quality** | ~500 automated tests, including PostgreSQL integration tests and oracle tests showing the optimized processing gives identical numbers |

### Data sources

All fire data comes from **[NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/)**:

- **MODIS Collection 6.1** active fires (Terra and Aqua): archive 2001-2025 and near-real-time
- **VIIRS 375 m** active fires: Suomi NPP (2012 to 2 Nov 2026), NOAA-20 (2018 onward), NOAA-21 (near-real-time)
- Access through the yearly archive files, the FIRMS Area API (live polling) and the Data Availability API

Other sources: Natural Earth boundaries, plus Open-Meteo and Meteomatics weather.

---

## 🚀 Run it locally

**Requirements:** Docker (for Postgres), Python 3.11+, Node `^20.19` or `>=22.12`.

```bash
cp .env.example .env                 # add the keys you have; each missing key only switches off its own part
docker compose up -d db
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
set -a; . <(tr -d '\r' < ../.env); set +a
alembic upgrade head && python -m worker.bootstrap
cd ../frontend && npm install && cd ..

./run-world.sh db                    # database, API, agents and dashboard → http://localhost:5173/dashboard
```

> [!TIP]
> **No database?** `./run-world.sh` alone serves the committed Tunisia and Algeria history (calendar,
> analytics and Data Explorer), so you can try the dashboard in a minute.

The full guide (keys, the worker, the world dataset and troubleshooting) is in
**[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**.

<details>
<summary><b>📁 Repository layout</b></summary>

```
backend/                            FastAPI API, processing science, worker, tests
backend/processing/                 deterministic stages: filters, mask, grid, harmonization, gates
backend/worker/                     FIRMS polling, processing runs, AI review, publishing
backend/harmonization-modis-viirs/  FIRMS tooling and the fire incident agents (Social Alerts)
frontend/                           React dashboard
docs/processing/ARCHITECTURE.md     the processing specification
docs/DEVELOPMENT.md                 the developer guide
docs/images/                        README screenshots and figures
DEPLOY.md                           one-server production deployment
run-world.sh                        one-command local launcher
```

</details>

---

## 🤖 Use of AI

- **In the product:** the CrewAI reviewer agents only review summaries. They never compute or change
  numbers, and they can only make a decision stricter. The Processing Center shows every AI decision
  and the model behind it. The Social Alerts agents use a language model to find and summarize fire
  reports.
- **In development:** parts of the code, tests and documentation were written with AI coding
  assistants (Claude Code by Anthropic, and Cursor). The team made the scientific choices.

---

## 👥 Team

<table>
  <tr>
    <td width="140" align="center"><img src="frontend/public/branding/pizzaneptune-team.jpg" alt="PizzaNeptune team logo" width="120"></td>
    <td>
      <b>PizzaNeptune</b> · Tunis, Tunisia · NASA Space Apps Challenge 2026<br><br>
      Sarra Mansour <i>(team owner)</i> · Wejden Ghabarou · Mohamed Zerai · Mohamed Nidhal Sadfi ·
      Omar Kthiri · Mohamed Fathallah
    </td>
  </tr>
</table>

<div align="center">
<sub>Fire data: NASA FIRMS (LANCE). This project is not affiliated with or endorsed by NASA.</sub><br>
<sub>⭐ If this helped you, star the repo, and <a href="https://spark-seer.duckdns.org/">try the live dashboard</a>.</sub>
</div>
