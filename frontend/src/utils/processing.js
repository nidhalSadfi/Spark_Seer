/**
 * GET /processing/* responses → what the Processing Center shows (spec §7, §10, §11).
 * Pure functions, no imports, so dev-checks/processing.check.mjs runs them in plain Node.
 * Response shapes: backend/README.md, "Processing endpoints".
 */

export const JOB_KINDS = ['firms_poll', 'monthly_reconcile']
export const JOB_STATUSES = ['queued', 'running', 'succeeded', 'no_change', 'held', 'rejected', 'failed', 'abandoned']

export const KIND_LABEL = { firms_poll: 'FIRMS poll', monthly_reconcile: 'Monthly reconcile' }
export const kindLabel = (k) => KIND_LABEL[k] ?? k

/** Badge tone per job status (tones of components/ui/primitives Badge). */
export const STATUS_TONE = {
  queued: 'info',
  running: 'info',
  succeeded: 'ok',
  no_change: 'neutral',
  held: 'warn',
  rejected: 'fire',
  failed: 'fire',
  abandoned: 'fire',
}
export const STATUS_LABEL = { no_change: 'no change' }
export const statusLabel = (s) => STATUS_LABEL[s] ?? s

export const DECISION_TONE = { approve: 'ok', hold: 'warn', reject: 'fire' }

/** What `ai_review` means, in one plain sentence each (spec §7 decision table). */
export const AI_REVIEW = {
  completed: {
    tone: 'ok',
    label: 'AI review completed',
    text: "The three reviewer agents ran and the Validation reviewer's verdict was used in the decision.",
  },
  unavailable: {
    tone: 'warn',
    label: 'AI review unavailable',
    text: 'Every model failed (error, timeout, rate limit or invalid answer) or the daily free quota was used up, so the run was decided on the hard gates alone.',
  },
  disabled: {
    tone: 'neutral',
    label: 'AI review disabled',
    text: 'No models are configured (OPENROUTER_MODELS is empty), so no LLM was called and the run was decided on the hard gates alone.',
  },
}
/** The rule behind every decision, whatever the review did. */
export const AI_RULE =
  'The AI can only make a decision stricter (approve → hold or reject); it can never approve a run whose hard gate failed.'

export function aiReviewInfo(value) {
  if (value == null) return { tone: 'neutral', label: 'No AI review', text: 'The job made no decision, so there was nothing to review.' }
  return AI_REVIEW[value] ?? { tone: 'neutral', label: value, text: '' }
}

export const BASIS = {
  verdict: "Decided by the Validation reviewer's verdict, after every hard gate passed.",
  gates_only: 'Every hard gate passed and no usable AI review was available, so the run was published on the gates alone.',
  hard_gate_failed: 'A hard gate failed, so the run was rejected without asking the AI.',
}
export const basisText = (b) => (b == null ? 'The job made no decision.' : BASIS[b] ?? b)

export const GATE_LABEL = {
  G1_schema_range: { short: 'G1', label: 'Schema and value ranges' },
  G2_conservation: { short: 'G2', label: 'Detections conserved through filter and aggregation' },
  G3_missing_not_zero: { short: 'G3', label: 'Missing is not zero (I7)' },
  G4_frozen_calibration: { short: 'G4', label: 'Frozen calibration (I6)' },
}

export const SOFT_FLAG_TEXT = {
  partial: 'Month not complete yet (near-real-time); published with the flag.',
  modis_partial: 'MODIS lacks some days of the month, so its count is a floor.',
  modis_unavailable: 'MODIS has no fetched day this month: its count is unknown (null), not zero.',
  mixed_reference: 'The VIIRS reference mixes S-NPP up to its end with scaled NOAA-20 after it.',
  reference_unavailable: 'The VIIRS reference sensor has no fetched day this month: unknown, not zero.',
  reference_pending: 'NOAA-20 is needed as reference but no approved ratio exists yet.',
  drift_above_warn: 'Monthly totals drifted above the warning threshold.',
  drop_share_high: "The filter dropped a much larger share than in recent runs.",
}

/** The three CrewAI reviewer agents (backend/worker/review.py), in the order they run. */
export const REVIEWERS = [
  { id: 'filter', name: 'Filter reviewer', reviews: 'Filter drops per rule and the kept and aggregated counts per sensor family.' },
  {
    id: 'harmonization',
    name: 'Harmonization reviewer',
    reviews: 'Calibration, reference sensors, quality flags, partial months and drift.',
  },
  {
    id: 'validation',
    name: 'Validation reviewer',
    reviews: "Decides approve, hold or reject from the hard gates, soft flags, affected keys and the other two reviewers' notes.",
    decides: true,
  },
]

export const ATTEMPT_OUTCOME_TONE = {
  ok: 'ok',
  timeout: 'warn',
  rate_limited: 'warn',
  quota_exhausted: 'warn',
  http_error: 'fire',
  invalid_output: 'fire',
  error: 'fire',
}

export const REVIEW_REASON = {
  daily_quota: "The OpenRouter key's free daily requests were used up, so no model was tried.",
}

export const COUNT_LABELS = [
  ['fetched', 'Fetched from FIRMS'],
  ['new', 'New'],
  ['modified', 'Modified'],
  ['unchanged', 'Unchanged'],
  ['pending', 'Pending detections'],
  ['affected', 'Affected cell-months'],
  ['detections', 'Detections processed'],
  ['staged', 'Rows staged'],
]

// ───────────────────────── formatting ─────────────────────────

export function fmtDuration(s) {
  if (s == null || !Number.isFinite(s)) return '—'
  if (s < 1) return `${Math.round(s * 1000)} ms`
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`
  const m = Math.floor(s / 60)
  const r = Math.round(s - m * 60)
  if (m < 60) return r ? `${m} min ${r} s` : `${m} min`
  const h = Math.floor(m / 60)
  return `${h} h ${m - h * 60} min`
}

export const fmtMs = (ms) => (ms == null ? '—' : fmtDuration(ms / 1000))

/** "3 min ago" / "in 2 h" relative to `now` (ms). */
export function relTime(iso, now = Date.now()) {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return '—'
  const d = Math.round((t - now) / 1000)
  const a = Math.abs(d)
  let v
  if (a < 45) return d >= 0 ? 'in a moment' : 'just now'
  if (a < 3600) v = `${Math.max(1, Math.round(a / 60))} min`
  else if (a < 2 * 86400) v = `${Math.round(a / 3600)} h`
  else v = `${Math.round(a / 86400)} days`
  return d >= 0 ? `in ${v}` : `${v} ago`
}

/** Absolute UTC time, "15 Nov 2026, 03:43 UTC". */
export function fmtUtc(iso) {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return '—'
  const d = new Date(t)
  const pad = (n) => String(n).padStart(2, '0')
  const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getUTCMonth()]
  return `${d.getUTCDate()} ${mon} ${d.getUTCFullYear()}, ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`
}

/** Cadence of a schedule row: "every 3 h", "monthly, day 5". */
export function cadenceLabel(k) {
  if (k.cadence === 'interval' && k.interval_seconds) {
    const h = k.interval_seconds / 3600
    return Number.isInteger(h) ? `every ${h} h` : `every ${fmtDuration(k.interval_seconds)}`
  }
  if (k.cadence === 'monthly') return `monthly, day ${k.day_of_month ?? '?'}`
  return k.cadence ?? '—'
}

/**
 * A cell-month key [cy, cx, year, month] (0.5° cell indices, as in cell_month), with the cell's
 * south-west corner: { key: "68,-5,2026-11", period: "2026-11", place: "34.0°N 2.5°W" }.
 */
export function fmtKey(key) {
  if (!Array.isArray(key) || key.length !== 4) return { key: String(key), period: '', place: '' }
  const [cy, cx, year, month] = key
  const lat = cy * 0.5
  const lon = cx * 0.5
  const deg = (v, pos, neg) => `${Math.abs(v).toFixed(1)}°${v >= 0 ? pos : neg}`
  const period = `${year}-${String(month).padStart(2, '0')}`
  return { key: `${cy},${cx},${period}`, period, place: `${deg(lat, 'N', 'S')} ${deg(lon, 'E', 'W')}` }
}

/** Months of a list of keys, counted: [{ period: "2026-10", n: 12 }, ...], oldest first. */
export function keysByMonth(keys) {
  const counts = new Map()
  for (const k of keys ?? []) {
    const { period } = fmtKey(k)
    if (period) counts.set(period, (counts.get(period) ?? 0) + 1)
  }
  return [...counts].map(([period, n]) => ({ period, n })).sort((a, b) => a.period.localeCompare(b.period))
}

// ───────────────────────── errors ─────────────────────────

/**
 * True when /processing/* cannot be served: no response (status 0), or the API's 503 (database
 * not configured, unreachable, or not migrated: the Render demo has no database). Not a 400/404.
 */
export const processingUnavailable = (e) => e != null && (e.status === 0 || e.status === 503 || e.status >= 500)

// ───────────────────────── job detail ─────────────────────────

const stepNamed = (steps, name) => (steps ?? []).find((s) => s.name === name) ?? null

/** The multi-agent review of one job, for the drawer's review panel. */
export function reviewView(job) {
  const step = stepNamed(job.steps, 'review')
  const summary = step?.summary && typeof step.summary === 'object' ? step.summary : {}
  const attempts = (job.llm_attempts ?? [])
    .filter((a) => a.step == null || a.step === 'review')
    .map((a) => ({
      id: a.id,
      no: a.attempt_no,
      model: a.model,
      outcome: a.outcome,
      ok: a.outcome === 'ok',
      tone: ATTEMPT_OUTCOME_TONE[a.outcome] ?? 'neutral',
      startedAt: a.started_at,
      latencyMs: a.latency_ms,
      tokens: a.tokens ?? { prompt: null, completion: null, total: null },
      errorClass: a.error_class,
      error: a.error,
    }))
  const failed = attempts.filter((a) => !a.ok)
  const payload = summary.payload_chars ?? {}
  const verdict = job.verdict
  const reason = job.review?.reason ?? summary.reason ?? null
  return {
    aiReview: job.ai_review ?? null,
    info: aiReviewInfo(job.ai_review),
    rule: AI_RULE,
    ran: attempts.length > 0,
    agents: REVIEWERS.map((r) => ({ ...r, payloadChars: payload[r.id] ?? null })),
    verdict: verdict
      ? {
          decision: verdict.decision,
          tone: DECISION_TONE[verdict.decision] ?? 'neutral',
          reasons: verdict.reasons ?? [],
          flaggedKeys: verdict.flagged_keys ?? [],
          flaggedByMonth: keysByMonth(verdict.flagged_keys),
        }
      : null,
    model: job.review?.model ?? summary.model ?? null,
    modelsConfigured: summary.models ?? null,
    quotaRemaining: summary.quota_remaining ?? null,
    reason,
    reasonText: reason ? REVIEW_REASON[reason] ?? reason : null,
    attempts,
    // Fallback: a model failed and the next one was tried (or none answered).
    fallback: failed.length > 0,
    chain: attempts.map((a) => `${a.model} (${a.outcome})`).join(' → '),
  }
}

/** Everything the job drawer shows, from GET /processing/jobs/{id}. */
export function jobDetailView(job) {
  const gates = job.gates ?? { passed: null, results: [] }
  return {
    id: job.id,
    kind: job.kind,
    kindLabel: kindLabel(job.kind),
    status: job.status,
    statusTone: STATUS_TONE[job.status] ?? 'neutral',
    trigger: job.trigger,
    createdAt: job.created_at,
    startedAt: job.started_at,
    finishedAt: job.finished_at,
    duration: fmtDuration(job.duration_s),
    error: job.error ?? null,
    counts: COUNT_LABELS.map(([key, label]) => ({ key, label, value: job.counts?.[key] ?? null })),
    decision: {
      value: job.decision ?? null,
      tone: DECISION_TONE[job.decision] ?? 'neutral',
      basis: job.decision_basis ?? null,
      basisText: basisText(job.decision_basis),
      gatesPassed: gates.passed ?? null,
    },
    gates: {
      passed: gates.passed ?? null,
      results: (gates.results ?? []).map((g) => {
        const failures = g.detail && typeof g.detail === 'object' && g.detail.failures && typeof g.detail.failures === 'object'
          ? Object.keys(g.detail.failures)
          : []
        return { name: g.name, ...(GATE_LABEL[g.name] ?? { short: g.name, label: g.name }), passed: g.passed, detail: g.detail ?? null, failures }
      }),
    },
    // Row flags carry rows and sampled keys; job-level flags (drift, drop share) carry other fields.
    softFlags: (job.soft_flags ?? []).map(({ flag, rows, keys, ...extra }) => ({
      flag,
      rows: rows ?? null,
      keys: keys ?? [],
      text: SOFT_FLAG_TEXT[flag] ?? null,
      extra: Object.keys(extra).length ? extra : null,
    })),
    review: reviewView(job),
    steps: (job.steps ?? []).map((s) => ({
      id: s.id,
      name: s.name,
      status: s.status,
      tone: s.status === 'succeeded' ? 'ok' : s.status === 'failed' ? 'fire' : s.status === 'running' ? 'info' : 'neutral',
      startedAt: s.started_at,
      duration: fmtDuration(s.duration_s),
      durationS: s.duration_s,
      summary: s.summary ?? null,
    })),
    datasetVersion: job.dataset_version ?? null,
  }
}

/** Query string params of the job list filters (empty filters are left out). */
export function jobListParams({ kind = 'all', status = 'all', page = 0, pageSize = 20 } = {}) {
  return {
    ...(kind && kind !== 'all' ? { kind } : {}),
    ...(status && status !== 'all' ? { status } : {}),
    limit: pageSize,
    offset: page * pageSize,
  }
}
