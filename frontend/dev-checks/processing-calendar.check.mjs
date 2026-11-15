// Dev check (not shipped, not a test runner): the Processing Center's job-detail mapping and the
// calendar's coverage handling (spec I7: a "none" month is no data, never a zero), over sample JSON
// written from backend/README.md ("GET /calendar", "Processing endpoints"). SYNTHETIC values.
// Run from frontend/:  node dev-checks/processing-calendar.check.mjs
import assert from 'node:assert/strict'
import { calendarEndYear, calendarToComparison, calendarToMatrix, calendarToRows } from '../src/utils/calendarMatrix.js'
import { historicalMedian, latestObservation } from '../src/utils/stats.js'
import {
  AI_REVIEW,
  fmtDuration,
  fmtKey,
  jobDetailView,
  jobListParams,
  keysByMonth,
  processingUnavailable,
  relTime,
  reviewView,
} from '../src/utils/processing.js'

let n = 0
const check = (name, fn) => {
  fn()
  n++
  console.log(`ok - ${name}`)
}

// ───────────────────────── calendar coverage ─────────────────────────

/** A /calendar response for 2001..2026: every month 100 (harmonized), except as listed. */
function calendar({ coverage = {}, values = {}, withField = true } = {}) {
  const months = []
  for (let year = 2001; year <= 2026; year++) {
    for (let month = 1; month <= 12; month++) {
      const key = `${year}-${month}`
      const cov = coverage[key] ?? (year === 2026 ? 'none' : 'full')
      const v = cov === 'none' ? 0 : values[key] ?? 100
      const row = { year, month, modis_raw: v / 2, viirs_raw: year < 2012 ? 0 : v, harmonized: v, support: 50, z: cov === 'none' ? -1.2 : 0, flag: cov === 'none' ? 'low' : 'normal' }
      if (withField) row.coverage = cov
      months.push(row)
    }
  }
  return { months, meta: { cells: 3, critical_months: [7, 8, 9] } }
}
const nrt = calendar({ coverage: { '2026-10': 'partial', '2026-11': 'partial' }, values: { '2026-10': 35, '2026-11': 25.6 } })

check('the matrix ends at the response’s last year (2026), 2025 only for an empty response', () => {
  const mat = calendarToMatrix(nrt)
  assert.equal(mat.years.at(-1), 2026)
  assert.equal(mat.meta.endYear, 2026)
  assert.equal(calendarEndYear({ months: [] }), 2025)
  const empty = calendarToMatrix({ months: [], meta: { cells: 0, note: 'no detections in this area' } })
  assert.equal(empty.years.at(-1), 2025)
  assert.ok(empty.meta.empty && empty.data.every((row) => row.every((v) => v === 0)))
})

check('"none" months are null (no data), not zeros; partial months keep their counts and are listed', () => {
  const mat = calendarToMatrix(nrt)
  const yi = mat.years.indexOf(2026)
  assert.deepEqual(mat.data[yi], [null, null, null, null, null, null, null, null, null, 35, 25.6, null])
  assert.deepEqual(mat.coverage[yi], ['none', 'none', 'none', 'none', 'none', 'none', 'none', 'none', 'none', 'partial', 'partial', 'none'])
  assert.equal(mat.z[yi][0], null) // the API's z and flag of a "none" month are dropped
  assert.equal(mat.flags[yi][0], null)
  assert.equal(mat.flags[yi][9], null) // a partial month is not an anomaly
  assert.equal(mat.z[yi][9], 0)
  assert.deepEqual(mat.meta.partialMonths, [{ year: 2026, month: 9 }, { year: 2026, month: 10 }])
  assert.equal(mat.data[mat.years.indexOf(2025)][11], 100)
})

check('VIIRS before 2012 stays null; an API without `coverage` means every month is full', () => {
  const viirs = calendarToMatrix(nrt, 'viirs')
  assert.equal(viirs.data[0][0], null)
  assert.equal(viirs.data[viirs.years.indexOf(2012)][0], 100)
  const old = calendarToMatrix(calendar({ withField: false }))
  const yi = old.years.indexOf(2026)
  assert.equal(old.data[yi][0], 0) // the old contract: a zero-filled grid
  assert.equal(old.coverage[yi][0], 'full')
})

check('latest observation = the latest month with data, marked partial; never a "none" month', () => {
  const mat = calendarToMatrix(nrt)
  assert.deepEqual(latestObservation(mat), { yearIdx: mat.years.indexOf(2026), year: 2026, month: 10, partial: true })
  const closed = calendarToMatrix(calendar()) // 2026 entirely "none"
  assert.deepEqual(latestObservation(closed), { yearIdx: closed.years.indexOf(2025), year: 2025, month: 11, partial: false })
  const nothing = calendarToMatrix(calendar({ coverage: Object.fromEntries(Array.from({ length: 26 * 12 }, (_, i) => [`${2001 + Math.floor(i / 12)}-${(i % 12) + 1}`, 'none'])) }))
  assert.equal(latestObservation(nothing), null)
})

check('the historical median skips "none" and partial months instead of counting them as 0', () => {
  // November: 100 every year except two "none" years (zero-filled by the API) and one partial year.
  const cal = calendar({
    coverage: { '2003-11': 'none', '2004-11': 'none', '2020-11': 'partial', '2026-11': 'partial' },
    values: { '2020-11': 1, '2026-11': 25.6 },
  })
  const mat = calendarToMatrix(cal)
  assert.equal(historicalMedian(mat, 2026, 10), 100)
  assert.equal(historicalMedian(mat, 2006, 10), 100) // 2001, 2002, 2005
  // Counting the zero-filled "none" months, 2005 would get median(100, 100, 0, 0) = 50; here only
  // 2001 and 2002 are usable, fewer than 3 years, so there is no baseline.
  assert.equal(historicalMedian(mat, 2005, 10), null)
  assert.equal(historicalMedian(mat, 2021, 10), 100) // the partial 2020 (value 1) is left out too
})

check('yearly totals skip incomplete years; Data Explorer rows show "none" months as empty, not 0', () => {
  const cmp = calendarToComparison(nrt)
  assert.equal(cmp.totals.harmonized[cmp.years.indexOf(2025)], 1200)
  assert.equal(cmp.totals.harmonized[cmp.years.indexOf(2026)], null)
  assert.deepEqual(cmp.incompleteYears, [2026])
  const rows = calendarToRows(nrt)
  assert.equal(rows[0].period, '2026-12')
  assert.deepEqual([rows[0].harmonized, rows[0].modis_raw, rows[0].z, rows[0].flag, rows[0].coverage], [null, null, null, null, 'none'])
  const nov = rows.find((r) => r.period === '2026-11')
  assert.deepEqual([nov.harmonized, nov.coverage], [25.6, 'partial'])
})

// ───────────────────────── processing ─────────────────────────

const job = {
  id: 4, kind: 'firms_poll', status: 'succeeded', trigger: 'schedule',
  created_at: '2026-11-15T03:42:43.184Z', started_at: '2026-11-15T03:42:43.184Z', finished_at: '2026-11-15T03:43:43.571Z',
  duration_s: 60.387,
  counts: { fetched: 176, new: 0, modified: 0, unchanged: 176, pending: 3744, affected: 181, detections: 3744, staged: 181 },
  decision: 'approve', ai_review: 'completed', dataset_version_id: 2, calibration_version_id: 1, error: null,
  lease: { owner: 'host:1', expires_at: null, heartbeat_at: null },
  decision_basis: 'verdict',
  gates: { passed: true, results: [
    { name: 'G1_schema_range', passed: true, detail: { rows: 181, failures: {}, affected_keys: 181 } },
    { name: 'G2_conservation', passed: true, detail: { failures: {} } },
    { name: 'G3_missing_not_zero', passed: false, detail: { failures: { null_zero: 2 } } },
    { name: 'G4_frozen_calibration', passed: true, detail: {} },
  ] },
  soft_flags: [
    { flag: 'partial', rows: 181, keys: [[68, -5, 2026, 11], [68, 0, 2026, 10]] },
    { flag: 'drift_above_warn', warn_pct: 40, months: ['2026-10'] },
  ],
  verdict: { decision: 'approve', reasons: ['All hard gates passed.'], flagged_keys: [[68, -5, 2026, 11], [69, 1, 2026, 11], [68, 0, 2026, 10]] },
  review: { model: 'free/b', attempts: 2, reason: null },
  steps: [
    { id: 1, name: 'availability', status: 'succeeded', started_at: '2026-11-15T03:42:43Z', finished_at: null, duration_s: 1.084, summary: { sources: {} } },
    { id: 2, name: 'review', status: 'succeeded', started_at: '2026-11-15T03:42:50Z', finished_at: null, duration_s: 53.532,
      summary: { ai_review: 'completed', models: 3, attempts: 2, model: 'free/b', payload_chars: { filter: 1485, harmonization: 2170, validation: 4520 }, quota_remaining: 44 } },
    { id: 3, name: 'publish', status: 'skipped', started_at: '2026-11-15T03:43:43Z', finished_at: null, duration_s: 0.003, summary: null },
  ],
  llm_attempts: [
    { id: 7, step: 'review', attempt_no: 1, model: 'free/a', started_at: '2026-11-15T03:42:50Z', latency_ms: 30000, outcome: 'timeout',
      error_class: 'APITimeoutError', error: 'timed out', tokens: { prompt: null, completion: null, total: null } },
    { id: 8, step: 'review', attempt_no: 2, model: 'free/b', started_at: '2026-11-15T03:43:20Z', latency_ms: 23053, outcome: 'ok',
      error_class: null, error: null, tokens: { prompt: 6595, completion: 8053, total: 14648 } },
  ],
  dataset_version: { id: 2, status: 'approved' },
}

check('job detail: decision and basis, gates with failures, soft flags, counts, steps', () => {
  const v = jobDetailView(job)
  assert.deepEqual(v.decision, { value: 'approve', tone: 'ok', basis: 'verdict', basisText: v.decision.basisText, gatesPassed: true })
  assert.match(v.decision.basisText, /Validation reviewer/)
  assert.deepEqual(v.gates.results.map((g) => [g.short, g.passed, g.failures]), [['G1', true, []], ['G2', true, []], ['G3', false, ['null_zero']], ['G4', true, []]])
  assert.deepEqual(v.softFlags.map((f) => [f.flag, f.rows, f.keys.length, f.extra]), [
    ['partial', 181, 2, null],
    ['drift_above_warn', null, 0, { warn_pct: 40, months: ['2026-10'] }],
  ])
  assert.deepEqual(v.counts.find((c) => c.key === 'affected'), { key: 'affected', label: 'Affected cell-months', value: 181 })
  assert.deepEqual(v.steps.map((s) => [s.name, s.tone, s.duration]), [['availability', 'ok', '1.1 s'], ['review', 'ok', '54 s'], ['publish', 'neutral', '3 ms']])
  assert.equal(v.duration, '1 min')
})

check('review: three agents with their prompt sizes, the verdict, the model and every attempt (fallback visible)', () => {
  const r = reviewView(job)
  assert.deepEqual(r.agents.map((a) => [a.name, a.payloadChars, !!a.decides]), [
    ['Filter reviewer', 1485, false], ['Harmonization reviewer', 2170, false], ['Validation reviewer', 4520, true],
  ])
  assert.equal(r.info, AI_REVIEW.completed)
  assert.match(r.rule, /never approve a run whose hard gate failed/)
  assert.deepEqual([r.verdict.decision, r.verdict.tone, r.verdict.reasons.length], ['approve', 'ok', 1])
  assert.deepEqual(r.verdict.flaggedByMonth, [{ period: '2026-10', n: 1 }, { period: '2026-11', n: 2 }])
  assert.deepEqual([r.model, r.modelsConfigured, r.quotaRemaining], ['free/b', 3, 44])
  assert.deepEqual(r.attempts.map((a) => [a.no, a.model, a.outcome, a.tone, a.errorClass]), [
    [1, 'free/a', 'timeout', 'warn', 'APITimeoutError'], [2, 'free/b', 'ok', 'ok', null],
  ])
  assert.equal(r.fallback, true)
  assert.equal(r.chain, 'free/a (timeout) → free/b (ok)')
})

check('review unavailable on the daily quota, disabled, and a job without a decision', () => {
  const quota = reviewView({ ...job, ai_review: 'unavailable', verdict: null, llm_attempts: [], review: { model: null, attempts: 0, reason: 'daily_quota' } })
  assert.equal(quota.info.label, 'AI review unavailable')
  assert.equal(quota.verdict, null)
  assert.match(quota.reasonText, /free daily requests/)
  assert.equal(quota.fallback, false)
  assert.equal(reviewView({ ...job, ai_review: 'disabled', verdict: null, llm_attempts: [] }).info, AI_REVIEW.disabled)
  const none = jobDetailView({ id: 9, kind: 'firms_poll', status: 'failed', counts: {}, gates: { passed: null, results: [] }, soft_flags: [], verdict: null, review: null, steps: [], llm_attempts: [], error: 'FIRMS answered 500' })
  assert.equal(none.review.info.label, 'No AI review')
  assert.equal(none.decision.basisText, 'The job made no decision.')
  assert.equal(none.error, 'FIRMS answered 500')
})

check('formatting: durations, relative times, cell-month keys, list params', () => {
  assert.deepEqual([fmtDuration(0.003), fmtDuration(9.95), fmtDuration(116.7), fmtDuration(null)], ['3 ms', '9.9 s', '1 min 57 s', '—'])
  const now = Date.parse('2026-11-15T04:00:00Z')
  assert.equal(relTime('2026-11-15T03:43:43Z', now), '16 min ago')
  assert.equal(relTime('2026-11-15T06:42:43Z', now), 'in 3 h')
  assert.deepEqual(fmtKey([68, -5, 2026, 11]), { key: '68,-5,2026-11', period: '2026-11', place: '34.0°N 2.5°W' })
  assert.deepEqual(keysByMonth([[1, 1, 2026, 11], [2, 2, 2026, 11]]), [{ period: '2026-11', n: 2 }])
  assert.deepEqual(jobListParams({ kind: 'all', status: 'held', page: 2, pageSize: 20 }), { status: 'held', limit: 20, offset: 40 })
})

check('503 and no response are "not connected"; 400/404 are not', () => {
  assert.equal(processingUnavailable({ status: 503 }), true)
  assert.equal(processingUnavailable({ status: 0 }), true)
  assert.equal(processingUnavailable({ status: 404 }), false)
  assert.equal(processingUnavailable({ status: 400 }), false)
  assert.equal(processingUnavailable(null), false)
})

console.log(`\n${n} checks passed`)
