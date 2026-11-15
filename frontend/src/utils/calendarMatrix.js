/**
 * GET /calendar response → UI shapes. Pure functions (no fetch, no import.meta), so
 * dev-checks/calendar-coverage.check.mjs can run them in Node.
 *
 * Coverage (backend README, `/calendar`): every month carries `coverage` = "full" | "partial" |
 * "none". The API zero-fills its grid, so a "none" month (no data loaded at all, or a future month)
 * reads 0 there. Here it becomes null: "no data", like VIIRS before 2012, never a zero (spec I7).
 * A "partial" month (near-real-time, still being observed) keeps its count and is marked.
 * An older API without the field means every month is "full".
 *
 * Year span: DATA_START_YEAR up to the last year the response contains (the API's default `end`
 * is its last year with data, e.g. 2026). DATA_END_YEAR (2025) is only the fallback for an empty
 * response, and what the offline mock covers.
 */
import { DATA_END_YEAR, DATA_START_YEAR, VIIRS_START_YEAR } from '../services/constants.js'

const SENSOR_FIELD = { harmonized: 'harmonized', modis: 'modis_raw', viirs: 'viirs_raw' }
export const COVERAGE = ['full', 'partial', 'none']

/** A month's coverage: the API's value, "full" when absent (older API, mock). */
export const coverageOf = (r) => (COVERAGE.includes(r?.coverage) ? r.coverage : 'full')

/** Last year of the response (DATA_END_YEAR when it has no months). */
export function calendarEndYear(cal) {
  let end = null
  for (const r of cal?.months ?? []) if (end == null || r.year > end) end = r.year
  return end ?? DATA_END_YEAR
}

/**
 * Year × month matrix for one sensor.
 *   data[yearIdx][monthIdx]   count, null = no data ("none" month, or VIIRS before 2012)
 *   coverage[yearIdx][monthIdx]  'full' | 'partial' | 'none' (null outside the response)
 *   z / flags / support       per-cell values from the API (harmonized series only); z and flag
 *                             are null for "none" months, flag also for "partial" ones (an
 *                             incomplete month is not an anomaly)
 *
 * The API returns a zero-filled grid, so an empty `months` means "no detections in this area":
 * we return an all-zero matrix with `meta.empty = true` rather than an empty one.
 */
export function calendarToMatrix(cal, sensor = 'harmonized') {
  const field = SENSOR_FIELD[sensor] ?? 'harmonized'
  const endYear = calendarEndYear(cal)
  const years = []
  for (let y = DATA_START_YEAR; y <= endYear; y++) years.push(y)
  const grid = () => years.map(() => Array(12).fill(null))
  const data = grid()
  const z = grid()
  const flags = grid()
  const support = grid()
  const coverage = grid()
  const partialMonths = []

  const empty = !cal.months?.length
  if (empty) data.forEach((row) => row.fill(0))
  for (const r of cal.months ?? []) {
    const yi = r.year - DATA_START_YEAR
    if (yi < 0 || yi >= years.length) continue
    const m = r.month - 1
    const cov = coverageOf(r)
    coverage[yi][m] = cov
    support[yi][m] = r.support ?? null
    if (cov === 'none') continue // unknown, not zero: data, z and flag stay null
    if (cov === 'partial') partialMonths.push({ year: r.year, month: m })
    // no VIIRS record before 2012: the API zero-fills, we show "no data" instead of a fake zero
    data[yi][m] = sensor === 'viirs' && r.year < VIIRS_START_YEAR ? null : (r[field] ?? null)
    z[yi][m] = r.z ?? null
    flags[yi][m] = cov === 'partial' ? null : (r.flag ?? null)
  }

  return {
    years,
    data,
    z,
    flags,
    support,
    coverage,
    meta: {
      source: cal.meta?.source,
      fallbackReason: cal.meta?.fallbackReason,
      cells: cal.meta?.cells ?? null,
      criticalMonths: cal.meta?.critical_months ?? [],
      validation: cal.meta?.validation ?? null,
      note: cal.meta?.note ?? null,
      sensor,
      empty,
      endYear,
      partialMonths,
    },
  }
}

/** True when every month of the year is "full" (a year total over it is complete). */
const completeYear = (mat, yi) => mat.coverage[yi].every((c) => c == null || c === 'full')

/**
 * Yearly totals for MODIS, VIIRS and harmonized (raw vs harmonized comparison chart).
 * A year with a "none" or "partial" month has no total (null): its sum would undercount.
 */
export function calendarToComparison(cal) {
  const mats = {
    modis: calendarToMatrix(cal, 'modis'),
    viirs: calendarToMatrix(cal, 'viirs'),
    harmonized: calendarToMatrix(cal, 'harmonized'),
  }
  const years = mats.modis.years
  const total = (m, yi) =>
    !completeYear(m, yi) || m.data[yi].every((v) => v == null) ? null : m.data[yi].reduce((a, b) => a + (b ?? 0), 0)
  return {
    years,
    totals: Object.fromEntries(Object.entries(mats).map(([k, m]) => [k, years.map((_, yi) => total(m, yi))])),
    incompleteYears: years.filter((_, yi) => !completeYear(mats.harmonized, yi)),
    meta: mats.harmonized.meta,
  }
}

/**
 * Flat monthly rows (newest first) for the Data Explorer table and CSV export. A "none" month has
 * null counts, z and flag (no data), so neither the table nor the CSV shows a fake zero.
 */
export function calendarToRows(cal) {
  return (cal.months ?? [])
    .map((r) => {
      const coverage = coverageOf(r)
      const none = coverage === 'none'
      return {
        year: r.year,
        month: r.month,
        period: `${r.year}-${String(r.month).padStart(2, '0')}`,
        modis_raw: none ? null : r.modis_raw,
        viirs_raw: none || r.year < VIIRS_START_YEAR ? null : r.viirs_raw,
        harmonized: none ? null : r.harmonized,
        support: r.support,
        z: none ? null : r.z,
        flag: none ? null : r.flag,
        coverage,
      }
    })
    .sort((a, b) => b.year - a.year || b.month - a.month)
}
