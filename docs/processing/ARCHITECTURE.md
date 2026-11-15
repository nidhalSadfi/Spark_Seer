# EmberGuard automated FIRMS processing — architecture spec

Status: approved 2026-11-14 (Phase 0). This file is the contract every implementation phase
follows. If code and this spec disagree, stop and raise it; do not silently pick one.

## 1. Goal

Every 3 hours (configurable), check NASA FIRMS MODIS and VIIRS near-real-time (NRT) data for the
EmberGuard region, detect new or modified detections, recompute only the affected cell-months,
let a CrewAI flow orchestrate filtering, harmonization and validation over deterministic Python
tools, and publish to PostgreSQL only what passes validation. FastAPI serves the approved data;
the React Processing Center shows what the pipeline actually did.

Region: Tunisia + Algeria north of 34°N (the existing EmberLedger scope). The query bbox is
configurable (`FIRMS_BBOX`). Its default, `-2.5,34.0,11.5,38.0`, covers that whole region padded to
0.5° cells: the Algeria–Morocco border lies near 1.8°W and the Kerkennah islands reach about
11.4°E. Polling only the demo areas would leave new months of every other historical cell
unfetched, and the API would show them as zeros (violates I7). The box still fits one 60° FIRMS
tile, so the cost is unchanged. It includes a strip of Morocco and some sea; the country clip in §5
removes them.

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
  The community-report endpoints (§10) write only to `community_reports`, never to processing or
  approved tables (amendment 2026-11-15).
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
- **Country clip:** keep only detections inside the Tunisia or Algeria land boundary. The historical
  series was built from FIRMS per-country files, so bbox data must be clipped the same way to stay
  comparable. Use a committed, public-domain boundary file (Natural Earth admin-0, 1:10m) with its
  source and version recorded. Small differences from FIRMS's own country assignment near borders
  are expected; report how many detections the clip removes per run.
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
  validated on held-out years, reporting % error like `calibrate.validate`. Default split: train on
  full years 2019–2023, test on 2024–2025 (configurable). Months containing a known outage day of
  either sensor are excluded from fit and test, because a dark satellite looks like "no fires". Known
  outages live in a committed file with their source (initially the satellite-level gaps from the
  Australia exploration: S-NPP 2022-07-27..08-10, 2024-05-30..06-03, 2024-07-10..07-15,
  2024-07-25..07-28; NOAA-20 2023-07-19..07-22), plus S-NPP 2024-11 as a whole month. The
  exploration attributes that outlier month to an S-NPP outage without listing its days. The list
  holds only the longest runs (30 of S-NPP's 41 dark days). Phase 2b cross-checks it against the
  FIRMS missing-data endpoint once a MAP_KEY is available. Reference: in the Australia exploration
  NOAA-20/S-NPP has a median of 0.98 (IQR 0.97–0.99), so r ≈ 1.02. A regional result far from that
  must be explained before approval.
  - **Fit pool (decision 2026-11-15).** Fitted on Tunisia + Algeria alone, r = 1.063, and on
    2024–2025 it does worse than no correction (18.9% vs 13.4% error), because the region's counts
    are small and noisy. The two satellites share an orbit and an instrument, so r is fitted on a
    wider pool instead: the yearly country files of Morocco, Spain, Portugal, Italy, Greece and
    Turkey, with the same filter (north of 34°N), train years and outage exclusions. It is then
    validated on Tunisia + Algeria over the test years, which is out of region and out of time.
    Result: r_global = 0.983 (per-country values 0.95–0.99), with an error of 12.6% on Tunisia +
    Algeria 2024–2025.
  - **Acceptance rule.** A fitted r is approved only if its validation error on Tunisia + Algeria
    is lower than r = 1's. Otherwise r = 1 is approved. Only the global r is used; the per-month
    values are reported but not applied, because they overfit.
  - **Calibration version 1** carries r_global = 0.983 (status `approved`), with the fit report
    (pool countries, file list, errors for r = 1, the global r and the per-month r) stored in its
    validation report.
- **Complete vs partial months:** a cell-month is complete only if (a) the month has ended (UTC)
  and (b) every sensor family that the **harmonized value depends on** has stored fetches
  (`firms_fetches` windows, NRT or SP) covering each day it is expected, and that family's
  availability `max_date` is on or after its last expected day. Those families are MODIS before
  2012 and the reference family or families from 2012. In the month that contains `snpp_end`,
  S-NPP is expected only up to `snpp_end`, and NOAA-20 from the next day. Otherwise the month is
  partial: `is_partial = true`, flag `partial`. This also covers the first poll, whose 30-day
  backfill starts mid-month: that month is not complete from NRT alone, and only becomes complete
  once SP data fills it (`monthly_reconcile`).
  MODIS does not decide completeness from 2012 onward, so the end of Terra and Aqua cannot leave
  every month partial forever. MODIS coverage only qualifies the `modis` column:
  - some days uncovered → count kept, flag `modis_partial`;
  - no day covered → null, flag `modis_unavailable`.
- **Anomalies:** still computed at request time by `api/core.calendar_for_bbox` over the summed
  area (robust z against the same calendar month in other years, |z| ≥ 3, MIN_COUNT 10,
  `low_confidence` for pre-2012 support < 30). Cell rows store no z. Phase 5 makes the API exclude
  partial months from the z baseline and give them flag `partial`, because comparing an incomplete
  month with full months creates false "low" anomalies.
- **Drift monitor:** for complete months whose `modis` is fully covered (no `modis_partial` or
  `modis_unavailable`) and whose reference exists, compare `Σ MODIS × k` with `Σ viirs_ref` over the
  cells in the run. Report % error on the job. A value above `DRIFT_WARN_PCT` (default 40) is a soft
  flag, not a block. Months whose `Σ viirs_ref` is below `DRIFT_MIN_COUNT` (default 50) are reported
  as `insufficient_data` and never flagged: small counts swing by chance. Drift is job-level, not a
  row flag.

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
5. Load **all** stored detections of the affected cell-months, not only the pending ones: a
   cell-month's count is recomputed from everything in it.
6. Run the CrewAI flow (§7) over these deterministic stages: filter → aggregate affected
   cell-months → harmonize with the frozen calibration → completeness (`is_partial`) → hard gates
   → drift monitor. Write results to staging under the job id.

Hard gates (any failure blocks publication):
- G1 schema and range: counts are non-negative integers; keys are unique; every affected key
  appears exactly once.
- G2 conservation: for every affected cell-month **and** sensor family, the staged count equals an
  independent recount of the detections that passed the filter there. Totals per family are also
  reported. Comparing only totals would let a dropped detection in one cell cancel a duplicated
  one in another.
- G3 missing ≠ zero (I7): no harmonized value is computed where the needed reference or MODIS data
  is unavailable; such rows carry a null and a flag.
- G4 frozen calibration (I6): every staged row uses the calibration version passed in.

Soft flags (published, visible on the job and the row): `partial`, `reference_pending`,
`mixed_reference`, drift above `DRIFT_WARN_PCT`, and a country-clip or mask drop share far above
its recent level.
7. Publish (§8) or reject.

`monthly_reconcile` is an SP ingestion job; processing is shared with `firms_poll`. It runs over
every closed month after the approved dataset's last complete month whose SP data now exists. That
includes the 2026 months missing from the historical dataset, which ends in 2025. For each such
month and source family, it deletes the NRT rows for the whole (source family, month, bbox) window
and inserts the SP rows, marking them pending. This is window replacement, not row matching,
because NRT and SP positions can differ. The pending rows then go through the same stages. Optionally propose a new calibration version
(k, r, mask); it is approved only if its held-out `error_pct_cell_month_ratio` is no worse than
the current version's by more than `RECAL_TOLERANCE_PP` (default 0.5).

## 7. CrewAI orchestration and OpenRouter

- A CrewAI **Flow** defines the stage order in code. Agents do not choose which stages run.
- Three agents, each given a typed, read-only summary of the deterministic stages: Filter
  reviewer, Harmonization reviewer, Validation reviewer. The summary is put directly into the
  task prompt, not behind a tool call (see the free-tier budget below). "Tool results" in I8 means
  these summaries.
- Output: a Pydantic verdict `{decision: approve | hold | reject, reasons: [str],
  flagged_keys: [(cy, cx, year, month)]}`. Agents receive summaries (counts, metrics, flagged
  keys), never raw detection rows, with a size cap.
- The reviewers are told that `partial`, `modis_partial` and `mixed_reference` are expected in
  near-real-time runs and published with the row (§6), so on their own they are no reason to hold
  (decision 2026-11-15, after real runs held every partial month).
- Final decision = deterministic hard gates AND verdict. The verdict can only downgrade
  (approve → hold or reject), never upgrade a failed gate. `hold` keeps the staging rows unpublished
  for review.
- LLM access goes only through `backend/worker/llm.py`: OpenRouter with `OPENROUTER_MODELS` tried
  in order, each with a timeout and bounded retries. The whole review deadline is
  `REVIEW_DEADLINE_S`. Every attempt is recorded (`llm_attempts`: model, start, latency, outcome,
  error class, token counts if returned). Phase 3 returns attempts as data; Phase 4 persists them.
- An attempt fails on an error, a timeout, a rate limit, or **invalid output**: a verdict that does
  not parse, flagged keys outside the affected set, or reasons containing a number not present in
  the tool results (I8, reusing `check_numbers` from `backend/api/passport.py`). A failed attempt
  moves on to the next model.
- Decision (deterministic, `decide()`):

  | Situation | Decision | `ai_review` |
  |---|---|---|
  | Any hard gate failed | `reject` (the LLM cannot upgrade) | as it happened |
  | Gates passed, valid verdict | the verdict's `approve` / `hold` / `reject` | `completed` |
  | Gates passed, every model failed | `approve` (D3) | `unavailable` |
  | Gates passed, `OPENROUTER_MODELS` empty | `approve`; crewai is not imported | `disabled` |

- **Free-tier budget (decision 2026-11-15).** The project runs on OpenRouter's free models only.
  On the free tier a key gets 50 free-model requests per day, and 8 polls a day share them.
  - **Three requests per attempt at most.** Each agent's summary goes directly into its task
    prompt, with no tool round trip, and each agent answers once (`max_iter = 1`). The verdict is
    parsed from the Validation reviewer's raw answer by `validate_verdict`, with no CrewAI
    conversion call. A failure moves on to the next model.
  - **Quota check before the review.** `llm.py` reads the key's remaining free-model requests
    from OpenRouter's key endpoint (`GET /api/v1/key`, `free_model_daily_requests.remaining`). If
    fewer than 3 remain, no model is tried. The step records the reason `daily_quota`, and the run
    is decided as "every model failed" (`approve` on the gates, `ai_review = "unavailable"`). If the
    check itself fails, the review runs anyway.
  - **Stop the chain on a daily-quota 429.** When a model fails with OpenRouter's daily free-quota
    429, rather than a per-model upstream rate limit, the remaining models are skipped.
  - **Models (2026-11-15, probed live).** `nvidia/nemotron-3.5-super-vl:free`, then
    `inclusionai/ling-3.2-flash:free`, then `nvidia/nemotron-3-ultra-550b-a55b:free`. The list lives
    in `.env`. Free models come and go, so it is re-probed when attempts keep failing.
  - **Availability guarantee.** Because of D3, the quota, a rate limit or an outage of every model
    never stops publication or the API. Only the review is missing for that run, and the
    Processing Center shows it.

- Privacy and isolation: CrewAI telemetry is switched off (`CREWAI_DISABLE_TELEMETRY=true`,
  `OTEL_SDK_DISABLED=true`) before `crewai` is imported. Agent memory, knowledge, planning and
  embeddings stay off. Every agent gets an explicit OpenRouter LLM, so nothing falls back to a
  default provider. Prompts carry stage summaries only: no raw detection rows, no personal data.
- Dependencies: `crewai` goes in `backend/requirements-worker.txt` (which includes
  `requirements.txt`), never in the API's `requirements.txt`.
- Tests use a stub LLM, and sockets are blocked during flow tests. No test calls OpenRouter or any
  telemetry endpoint.

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
- **Live fires and community reports (amendment 2026-11-15).** The fire map and the reports page
  move off mock and browser-local data:
  - `GET /fires/live?hours=48&bbox=minLon,minLat,maxLon,maxLat&source=`: NRT detections from
    `raw_detections` whose acquisition time (UTC, from `acq_date` + `acq_time`) is within `hours`
    (1-168, default 48) of the newest stored detection, so the map is never empty just because
    FIRMS is a few hours behind. Response:
    `{"snapshot": ISO, "window": {"start": ISO, "end": ISO}, "detections": [{"id", "lat", "lon",
    "acq_time": ISO, "source", "satellite", "sensor": "modis"|"viirs", "confidence", "frp",
    "daynight"}], "sources": [{"source", "max_acq_date"}], "meta": {"count", "product": "NRT",
    "hours", "bbox"}}`. 400 for a bad bbox or hours, 503 when the database is unreachable.
    Detections are read-only data; the endpoint never calls FIRMS.
  - `GET /reports` → `{"reports": [report]}`, newest first, at most 500.
  - `POST /reports` with `{"lat", "lon", "observed": "smoke"|"flames"|"both"|"unsure",
    "note"}` (note trimmed, at most 280 characters; lat/lon validated) → 201 and the report.
  - `PATCH /reports/{id}` with `{"status": "pending"|"confirmed"|"false"}` → the report;
    `reviewedAt` is set for confirmed/false and cleared for pending. `DELETE /reports/{id}` → 204.
  - `report = {"id", "lat", "lon", "observed", "note", "status", "createdAt": ISO,
    "reviewedAt": ISO|null}`; ids are server-generated.
  - PATCH and DELETE require the `X-Admin-Token` header when `REPORTS_ADMIN_TOKEN` is set; when it
    is unset (local development) they are open. POST is always open, with the validation above.
  - New Alembic migration for `community_reports`. These endpoints never touch processing tables.
- Database access in the API: when `DATABASE_URL` is unset or unreachable, the existing endpoints
  keep working from the parquet file (Render demo), and `/processing/*`, `/fires/live` and
  `/reports` answer 503 with a clear message.

## 11. Frontend

`/dashboard/processing` (new sidebar entry): status header, job list, and a job detail drawer
(steps timeline, counts, verdict, LLM attempts and fallbacks, gate results). Data comes from the
`/processing/*` GETs via React Query (`refetchInterval` 30 s while visible). There is no "run"
button by default.

Amendment 2026-11-15: the fire map (`getActiveFires`) reads `GET /fires/live` and clusters the
detections client-side into the existing feed shape; it falls back to the mock feed only when the
API is unreachable, and says so (as `getCalendar` does). The reports store reads and writes
`/reports`, falling back to browser-local storage with a visible notice when the API is
unreachable.

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
| 2a | Processing science (pure Python) | Shared filter + clip + mask; recompute of selected cell-months equals the full `calibrate.harmonize` result; completeness; reference switch; inter-VIIRS fit; gates G1–G4; I6, I7 |
| 2b | Processing data flow (PostgreSQL) | Pending → affected keys → all detections of those cell-months; `monthly_reconcile` SP ingestion with window replacement; coverage from `firms_fetches`; I3 |
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
- Bootstrap mapping (Phase 4): `harmonized.parquet` stores VIIRS = 0 before 2012. In the
  database it becomes NULL (I7: the sensor did not exist). Phase 5 decides how `/calendar`'s
  existing `viirs_raw` field reports those months without breaking its contract (it is 0 today).
- January 2012 counts as a full S-NPP month (S-NPP starts 2012-01-20), as in
  `calibrate.harmonize`. This is kept for consistency with the published history.
- **Real data (2026-11-15).** The FIRMS yearly SP country files for Tunisia and Algeria are now
  under `backend/data/raw/`, which is gitignored. They cover MODIS 2001–2025, S-NPP 2012–2025 and
  NOAA-20 2018–2025. They produced:
  - `data/harmonized.parquet` and `harmonized_validation.json`: per-cell-month error 11.5%,
    k_global 3.48;
  - `data/static_mask.json`: 1,310 squares;
  - `data/boundary_tun_dza.geojson`: Natural Earth 10m v5.1.2, TUN + DZA.

  The neighbour files used for the r pool are not stored in the repo; the README gives the
  `firms_tool archive` command that downloads them. A live FIRMS `data_availability` check confirmed that S-NPP NRT ends on 2026-11-02. Tests
  still use synthetic data only.

## 16. World coverage (decision 2026-11-15)

The user chose to expand from Tunisia + Algeria to the whole world. Rules:

- **History.** FIRMS SP archives for the world: MODIS C6.1 2001-2025, S-NPP 2012-2025 and
  NOAA-20 2018-2025 (for r), under `backend/data/world/` (gitignored, never committed). The world
  historical dataset is built with the **same filter rules as the live processing** (§5), so history
  and live runs are comparable: no 34°N cut, the static-source mask instead (a world mask built from
  the world archive), and no country clip (`COUNTRY_BOUNDARY_FILE` unset; the mask handles
  persistent offshore and industrial sources).
- **Calibration.** k per cell and calendar month from the world archive, as today. r is fitted
  world-wide on the S-NPP / NOAA-20 overlap and approved only if it beats r = 1 on held-out years
  (§5 rule), with the validation reported **per continent and per latitude band** as well as
  globally, because global totals hide regional errors. Regions where the harmonization error is
  high are listed in the validation report and shown as such, never hidden.
- **Live polling.** `FIRMS_BBOX=world` is allowed. About 290 FIRMS transactions per 2-day poll of 4
  sources (limit 5000 per 10 minutes). The first world poll's backfill is capped by
  `FIRMS_MAX_BACKFILL_DAYS`.
- **Processing at scale.** A 3-hourly run may touch every active cell of the current month
  worldwide (millions of detections by month end). The run must stay within a documented memory
  and time budget on this machine (30 GB RAM, 8 cores): heavy counting may move to SQL or chunked
  pandas, but the numbers must stay equal to the existing pure-Python stages (oracle tests).
- **API.** `/calendar` already takes any bbox. `/fires/live` caps its payload: above a detection
  limit (or for a bbox wider than a threshold) it returns a 0.25° or 0.5° aggregated grid
  (`{"mode": "grid", "cells": [{lat, lon, count, frp_sum, ...}]}`) instead of individual
  detections, with `mode: "detections"` otherwise (additive; the current shape is
  `mode: "detections"`). `/areas` gains world fire regions next to the four North Africa areas.
- **Frontend.** The map defaults to the world, queries `/fires/live` for the visible viewport, and
  renders the grid mode when zoomed out. Area pickers list the world regions.
- **Cut-over.** Bootstrapping the real database with the world dataset replaces the approved North
  Africa dataset, so it needs the user's approval and is done by the architect, after the world
  data and the scaled backend are verified.
