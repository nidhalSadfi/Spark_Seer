# EmberGuard automated FIRMS processing — architecture spec

Status: approved 2026-11-14 (Phase 0). This file is the contract every implementation phase
follows. If code and this spec disagree, stop and raise it; do not silently pick one.

## 1. Goal

Every 3 hours (configurable), check NASA FIRMS MODIS and VIIRS near-real-time (NRT) data for the
EmberGuard region, detect new or modified detections, recompute only the affected cell-months,
let a CrewAI flow orchestrate filtering, harmonization and validation over deterministic Python
tools, and publish to PostgreSQL only what passes validation. FastAPI serves the approved data;
the React Processing Center shows what the pipeline actually did.

Region: Tunisia + Algeria north of 34°N (the existing EmberLedger scope). Query bbox is
configurable; the default is the union of the areas in `backend/data/areas.json` padded to whole
0.5° cells.

## 2. Decisions (approved 2026-11-14)

| # | Decision |
|---|---|
| D1 | Monorepo on this branch: `backend/` (from origin/backend-core), `frontend/` (from origin/frontend), `backend/firms_tool/` (copied from origin/harmonization-modis-viirs@e07ecf5). meteomatics-weather stays out (its `/weather` and `/health` clash). |
| D2 | VIIRS reference: S-NPP until its coverage ends (sensor catalogue says 2026-11-02; confirm with `data_availability`), then NOAA-20 scaled to S-NPP terms by a factor fitted and validated on the regional overlap. NOAA-21 is stored raw but not part of the harmonized series. |
| D3 | If every OpenRouter model fails: publish when all deterministic hard gates pass, with `ai_review = "unavailable"`. The LLM can never approve something a hard gate rejected. |
| D4 | Runtime: local docker-compose (`db`, later `api`, `worker`). Hosting is decided later. |
| D5 | Secrets only in `.env` (never committed, never in tests): `FIRMS_MAP_KEY` (or `FIRMS_MAP_KEY_1`), `OPENROUTER_API_KEY`. `OPENROUTER_MODELS` is an ordered list with no default. |

## 3. Topology and invariants

```
NASA FIRMS ─► WORKER (backend/worker/, own process; the only code that imports crewai)
 area API      DB-driven scheduler (60 s tick)
               ├─ firms_poll       every FIRMS_POLL_INTERVAL_HOURS (default 3)
               └─ monthly_reconcile monthly
                      │ writes
                      ▼
               PostgreSQL (raw, staging, approved, history, versions, jobs)
                      │ reads only
                      ▼
API (backend/api/, FastAPI) ─► React dashboard + Processing Center (GET only)
```

Invariants. Each one must have at least one automated test that fails if it breaks.

- **I1 Read-only API.** The API process never imports `worker`, `crewai` or the LLM client, and
  no GET endpoint writes to job tables or calls FIRMS/OpenRouter. The only write endpoint for
  processing is an admin-token-protected manual trigger, which the dashboard does not call.
- **I2 Approved data is never overwritten by a failed run.** Results go to staging; promotion is
  one transaction; any failure or rejection leaves the approved tables byte-identical.
- **I3 No new or modified rows → no CrewAI.** The diff runs before `crewai` is imported; such a job
  ends `no_change`.
- **I4 One active job per kind.** Two workers or a restart never run the same kind twice at once.
- **I5 Restart-safe.** All schedule and job state lives in PostgreSQL; nothing depends on process
  memory surviving.
- **I6 Frozen calibration in the 3-hour job.** `firms_poll` uses the current approved calibration
  version and never refits ratios. Refitting happens only in `monthly_reconcile`, behind a gate.
- **I7 Missing ≠ zero.** When a sensor has no data for a day (outage, end of mission, not yet
  available), counts are unknown, never stored as 0, and harmonized values that depend on them
  are null with a flag.
- **I8 The LLM never produces numbers.** Every number in agent output must appear in the tool
  results it was given (reuse `check_numbers` from `backend/api/passport.py`); otherwise the
  text is dropped and the verdict falls back to the deterministic result.

## 4. FIRMS ingestion

- Client: reuse `firms_tool.area_api.Client` (retries, 429/5xx backoff, budget pacing,
  key redaction), `plan_sensor`, `windows_for`, `call_cost` and `firms_tool.sensors.SENSORS`.
  Do not write a second FIRMS client.
- Sources per poll (NRT): `MODIS_NRT`, `VIIRS_SNPP_NRT` (while available), `VIIRS_NOAA20_NRT`,
  `VIIRS_NOAA21_NRT`. SP equivalents (`*_SP`) are used only by `monthly_reconcile`.
- Window: always re-fetch today and yesterday (UTC), because late granules keep arriving. If the
  last successful poll for a source is older, backfill from its watermark in ≤5-day windows.
  Cost: the region fits one 60°×60° tile, so 2 transactions per day per call. A normal poll is
  about 16 transactions, against a limit of 5,000 per 10 minutes.
- Availability: call `data_availability` once per poll and store per-source min/max dates. A
  source whose max date stops advancing, or a source past its catalogue `coverage_end`, is
  reported on the job (not an error).
- Raw provenance: store each response's sha256, row count, source, window and fetch time
  (`firms_fetches`). Raw payloads go to `backend/data/raw/firms/<source>/<YYYY-MM-DD>/<fetch_id>.csv.gz`
  (gitignored).
- Tests never call FIRMS. Use recorded fixtures under `backend/tests/fixtures/firms/`.

## 5. Scientific rules (Python, deterministic)

Keep these identical to the existing pipeline unless a phase brief says otherwise.

- **Filters:** latitude ≥ 34; MODIS confidence ≥ 30 (numeric); VIIRS confidence in {n, h,
  nominal, high}; SP rows: `type == 0`. NRT rows have no `type`; drop any NRT detection that
  falls inside the static-source mask (below). Implement this in ONE shared function and use it
  everywhere. The two existing ingest scripts disagree on NRT rows; neither behaviour is kept.
- **Static-source mask:** derived from historical SP rows with `type` in {1, 2, 3}. Mask cells are
  0.01° squares with at least N such detections (N configurable, default 3). Versioned like the
  calibration. NRT rows are replaced by SP rows in `monthly_reconcile`, which restores real `type`.
- **Grid:** 0.5° cells, `cy = floor(lat / 0.5)`, `cx = floor(lon / 0.5)` (matches
  `pipeline/ingest.py`, `api/core.py` and the existing `harmonized.parquet`). Months are UTC.
- **Counts:** per (cy, cx, year, month, sensor): `n` detections and `days` distinct `acq_date`.
- **Harmonized value:** before 2012 it is `MODIS × k(cy, cx, calendar month)` from the approved
  calibration version. From 2012 it is the VIIRS reference count:
  - S-NPP count while S-NPP has coverage for that month.
  - Afterwards, `NOAA-20 count × r`, where `r` is the S-NPP/NOAA-20 factor of the approved
    calibration version. Until an approved `r` exists, the value is null with flag
    `reference_pending`, never 0.
  - A month that straddles the switch uses S-NPP days up to its coverage end and scaled NOAA-20
    days after it; the record notes the mix.
- **Inter-VIIRS factor `r`:** fitted on the 2018-04 → S-NPP-end overlap with the same shrinkage
  approach as `calibrate.fit` (per calendar month, shrunk toward the regional value) and
  validated on held-out years, reporting % error like `calibrate.validate`. Reference: in the
  Australia exploration NOAA-20/S-NPP has a median of 0.98 (IQR 0.97–0.99). A regional result far
  from that must be explained before approval.
- **Partial months:** a month is partial until it has ended (UTC) and every contributing source's
  availability covers its last day. Partial months get `is_partial = true` and flag `partial`. No
  z-score is computed for them, because comparing an incomplete month with full months creates
  false "low" anomalies.
- **Anomalies:** unchanged from `api/core.calendar_for_bbox`: robust z against the same calendar
  month in other years, |z| ≥ 3, MIN_COUNT 10, `low_confidence` for pre-2012 support < 30.
- **Drift monitor:** for complete months where both MODIS and the VIIRS reference exist, compare
  `MODIS × k` with the observed reference at region-month level. Report % error on the job. A
  value above `DRIFT_WARN_PCT` (default 40) is a soft flag, not a block.

## 6. Incremental processing (`firms_poll`)

1. Fetch the windows (§4) → parse → normalise.
2. Natural key: (source, satellite, latitude, longitude, acq_date, acq_time), built from the raw
   string values as delivered (trimmed). Content hash: sha256 of all delivered columns in a fixed
   order. Classify each row as `new`, `modified` (same key, different hash) or `unchanged`. Do not
   collapse genuine repeated observations that differ in key.
   Inserting or modifying a row sets `raw_detections.needs_processing = true`. Only a successful
   promotion (§8) clears it, inside the promotion transaction. Rows from a failed, held or rejected
   run therefore stay pending and are picked up by the next run.
3. If there are no new or modified rows **and** no pending rows (`needs_processing`): record the
   counts, set status `no_change`, stop. CrewAI is not imported.
4. Affected set = distinct (cy, cx, year, month) of all pending rows.
5. Run the CrewAI flow (§7) over these deterministic stages: filter → aggregate affected
   cell-months → harmonize with the frozen calibration → partial-month and anomaly fields →
   hard gates → drift monitor. Write results to staging under the job id.
6. Publish (§8) or reject.

`monthly_reconcile`: for closed months whose SP coverage now exists and which still hold NRT
rows, delete the NRT rows for the whole (source family, month, bbox) window and insert the SP rows.
This is window replacement, not row matching, because NRT and SP positions can differ. Then
recompute those cell-months through the same stages. Optionally propose a new calibration version
(k, r, mask); it is approved only if its held-out `error_pct_cell_month_ratio` is no worse than
the current version's by more than `RECAL_TOLERANCE_PP` (default 0.5).

## 7. CrewAI orchestration and OpenRouter

- A CrewAI **Flow** defines the stage order in code. Agents do not choose which stages run.
- Three agents, each with read-only tools returning typed results from the deterministic stages:
  Filter reviewer, Harmonization reviewer, Validation reviewer.
- Output: a Pydantic verdict `{decision: approve | hold | reject, reasons: [str],
  flagged_keys: [(cy, cx, year, month)]}`. Agents receive summaries (counts, metrics, flagged
  keys), never raw detection rows, with a size cap.
- Final decision = deterministic hard gates AND verdict. The verdict can only downgrade
  (approve → hold or reject), never upgrade a failed gate. `hold` keeps the staging rows unpublished
  for review.
- LLM access goes only through `backend/worker/llm.py`: OpenRouter with `OPENROUTER_MODELS` tried
  in order, each with a timeout and bounded retries. Every attempt is recorded (`llm_attempts`:
  model, start, latency, outcome, error class, token counts if returned).
  - All models failed → D3 (publish on hard gates, `ai_review = "unavailable"`).
  - `OPENROUTER_MODELS` empty → `ai_review = "disabled"`.
- Tests use a stub LLM. No test calls OpenRouter.

## 8. Persistence (PostgreSQL, SQLAlchemy 2 + psycopg 3, Alembic)

Tables (names fixed; columns are a minimum; phase briefs may add more):

| Table | Purpose |
|---|---|
| `schedules` | job kind, interval, `next_run_at`, enabled |
| `processing_jobs` | id, kind, status, trigger (`schedule`/`manual`), lease owner, `lease_expires_at`, heartbeat, started/finished, counts (fetched/new/modified/unchanged/affected), `dataset_version_id`, `ai_review`, error |
| `job_steps` | job id, step name, status, started/finished, summary JSON |
| `llm_attempts` | job id, step, model, outcome, latency, error class |
| `firms_fetches` | source, window, fetched_at, sha256, rows, raw path, availability snapshot |
| `firms_availability` | job id, source, min_date, max_date (one snapshot per poll) |
| `raw_detections` | natural key (unique), content hash, all delivered columns, product (`NRT`/`SP`), first_seen/last_seen fetch, `needs_processing` |
| `sensor_day_coverage` | source, date, status (`available`/`missing`/`unknown`) |
| `calibration_versions` | id, status, k table ref, r factors, mask version, validation report JSON, approved_at |
| `dataset_versions` | id, parent id, job id, calibration version, status (`approved`/`superseded`), created/approved, summary |
| `cell_month` | **approved** rows: cy, cx, year, month, modis, viirs_ref, harmonized, k, support, days, is_partial, quality_flags, dataset_version_id |
| `cell_month_staging` | same columns + job id |
| `cell_month_history` | previous approved values, replaced_by_version, for rollback |

Table ownership: Phase 1 creates `schedules`, `processing_jobs`, `job_steps`, `firms_fetches`,
`firms_availability` and `raw_detections`. Phase 4 creates the rest. Every schema change is an
Alembic migration under `backend/migrations/`.

Promotion runs in one transaction:
1. Copy the approved rows being replaced into `cell_month_history`.
2. Upsert the staging rows into `cell_month`.
3. Insert the new `dataset_versions` row and mark the previous one `superseded`.
4. Mark the job `succeeded`.

Any exception rolls back everything. Rejected or held staging rows are kept for inspection, with a
retention limit.

Bootstrap: approved dataset version 1 and calibration version 1 are loaded from the existing
`harmonized.parquet` and `harmonized_validation.json`, so the API keeps serving the same numbers.

## 9. Scheduler and jobs

- Job kinds: `firms_poll`, `monthly_reconcile`. Statuses: `queued`, `running`, `succeeded`,
  `no_change`, `held`, `rejected`, `failed`, `abandoned`.
- Claim: `SELECT … FROM schedules WHERE next_run_at <= now() FOR UPDATE SKIP LOCKED`, then insert
  the job. A partial unique index on `processing_jobs(kind) WHERE status IN ('queued','running')`
  enforces I4, backed by `pg_try_advisory_lock(kind)`.
- Lease: the running worker renews `lease_expires_at` (heartbeat). At startup and on every tick,
  jobs with an expired lease become `abandoned`, and their kind becomes due again.
- Missed runs collapse: after downtime, one run happens and `next_run_at = now + interval`. There
  is no backlog storm.
- Interval: `FIRMS_POLL_INTERVAL_HOURS` (default 3), read at startup and written to `schedules`.
- `monthly_reconcile` runs on `RECONCILE_DAY_OF_MONTH` (default 5) at 03:00 UTC. Its schedule row
  exists from Phase 1 but stays disabled until its handler lands in Phase 2.
- Handlers are registered per kind. A due kind with no handler is skipped and logged, never run.

## 10. API

- Existing endpoints keep their response fields (backend README: only additive changes).
  `api/core.py` keeps its calendar maths; only `get_data()` changes source, from parquet to the
  approved `cell_month` table, cached per approved `dataset_version_id` (checked at most every
  `API_VERSION_CHECK_S`, default 30).
- New, all GET unless noted:
  - `/processing/status`: scheduler state, last and next run per kind, current approved dataset
    and calibration versions, last error.
  - `/processing/jobs`: paginated, filterable by kind and status.
  - `/processing/jobs/{id}`: steps, counts, verdict, `ai_review`, LLM attempts, gate results.
  - `/processing/datasets`: version history.
  - `POST /processing/jobs`: admin token required (`ADMIN_TOKEN`); queues a manual run. It still
    obeys I4.

## 11. Frontend

`/dashboard/processing` (new sidebar entry): status header, job list, and a job detail drawer
(steps timeline, counts, verdict, LLM attempts and fallbacks, gate results). Data comes from the
`/processing/*` GETs via React Query (`refetchInterval` 30 s while visible). There is no "run"
button by default. The other dashboard pages are not changed in these phases; they still use mock
data.

## 12. Configuration

All new code reads settings through one module, `backend/settings.py`, which reads environment
variables with typed defaults. Scattered `os.environ` reads are not allowed in new code; existing
`EMBER_DATA` handling in `api/core.py` stays. `.env` is loaded only for local development; docker
compose reads it for interpolation. Variable names are the ones in `.env.example`. Add new ones
there with a one-line comment.

## 13. Testing rules

- No live network in tests (FIRMS, OpenRouter, Open-Meteo): recorded fixtures and stubs only. No
  real keys.
- Every invariant in §3 has a test. Scientific functions are tested against small hand-computed
  fixtures.
- PostgreSQL tests: the local user cannot access the Docker daemon yet. Phases that need a
  database must make PG tests skip cleanly when no database is reachable (`TEST_DATABASE_URL`),
  never fall back to SQLite for behaviour that depends on PostgreSQL (SKIP LOCKED, advisory locks,
  transactions).
- Frontend checks need Node ^20.19 or ≥22.12 (local Node is too old).

## 14. Phases

| # | Phase | Acceptance (verified by the architect, not self-reported) |
|---|---|---|
| 0 | Integration base | Monorepo layout; 46 passed + 2 xfailed; compose parses; no other ref changed |
| 1 | Scheduler + FIRMS ingestion | I4, I5, interval config; fetch + availability against fixtures; restart and duplicate scenarios |
| 2 | Incremental processing | Natural-key diff; shared filter + mask; affected-only recompute equals full recompute; partial months; reference switch; I3, I6, I7 |
| 3 | CrewAI + OpenRouter | Flow order fixed; typed verdict; fallback chain with stub models; downgrade-only; I8 |
| 4 | Persistence + job tracking | Alembic migrations; atomic promotion; forced mid-promotion failure leaves approved identical (I2); rollback; bootstrap |
| 5 | FastAPI | Existing contracts unchanged; `/processing/*`; I1; passport int-year bug fixed; dynamic end year |
| 6 | Processing Center | Builds on Node 22; GET-only network traffic; renders real job data |
| 7 | Integration + docs | End-to-end on PostgreSQL with recorded FIRMS data and stub LLM; runbook |

## 15. Known issues carried into the phases

- `api/passport.py` passes `'2001-01'` strings to `calendar_for_bbox` (int years expected). This
  is the cause of the 2 strict xfails. Fix in Phase 5.
- `/calendar` defaults to `end=2025` and the passport limits years to 2001–2025. New data needs a
  dynamic end year (Phase 5). The defaults change only in a backward-compatible way.
- `calibrate.harmonize` refits on 2012–2021, which includes the validation test years. The
  published k therefore differs from the one scored in the validation report. Document it; a
  calibration version records both.
- The frontend's other pages use mock data, and its region list does not match the backend's
  areas. Wiring them up is a separate phase after Phase 5.
