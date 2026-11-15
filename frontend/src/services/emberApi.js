/**
 * EmberLedger API client (backend branch `backend-core`).
 *
 * Endpoints: GET /health, /areas, /calendar, /validation, /weather · POST /investigate.
 * `getCalendar` falls back to the offline mock (src/services/fireApi.js) when the backend is
 * unreachable; its `meta.source` says which one answered ('api' | 'mock').
 */
import { apiGet, apiPost, isUnavailable } from './http'
import { mockCalendar } from './fireApi'
import { DATA_END_YEAR, DATA_START_YEAR, VIIRS_START_YEAR } from './constants'

export const getHealth = () => apiGet('/health')
export const getAreas = async () => (await apiGet('/areas')).areas
export const getValidation = () => apiGet('/validation')
export const getWeather = (area) => apiGet('/weather', { area })
/** Same 3-day weather context for any point (used for confirmed community fire reports). */
export const getPointWeather = (lat, lon) => apiGet('/weather', { lat, lon })

/** Evidence Passport for one area (or bbox) and month. `month` is 1-12. */
export const investigate = ({ area, bbox, year, month }) =>
  apiPost('/investigate', { ...(bbox ? { bbox } : { area }), year, month })

/**
 * Monthly harmonized calendar for a named area or a bbox [minLon, minLat, maxLon, maxLat].
 * Always resolves to the API's response shape, with `meta.source` added.
 */
export async function getCalendar({ area, bbox, start = DATA_START_YEAR, end = DATA_END_YEAR }) {
  try {
    const cal = await apiGet('/calendar', { area, bbox: bbox?.join(','), start, end })
    return { ...cal, meta: { ...cal.meta, source: 'api' } }
  } catch (e) {
    if (!isUnavailable(e)) throw e // bad request (400): surface it, don't hide it behind mock data
    console.warn(`[EmberLedger] ${e.message} Showing offline sample data instead.`)
    const cal = mockCalendar(area ?? `bbox:${bbox}`)
    return { ...cal, meta: { ...cal.meta, source: 'mock', fallbackReason: e.message } }
  }
}

// ───────────────────────── adapters: API calendar → UI shapes ─────────────────────────

const SENSOR_FIELD = { harmonized: 'harmonized', modis: 'modis_raw', viirs: 'viirs_raw' }

/**
 * Year × month matrix for one sensor.
 *   data[yearIdx][monthIdx]  count (null = no record, e.g. VIIRS before 2012)
 *   z / flags / support      per-cell values from the API (harmonized series only)
 *
 * The API returns a zero-filled grid, so an empty `months` means "no detections in this area":
 * we return an all-zero matrix with `meta.empty = true` rather than an empty one.
 */
export function calendarToMatrix(cal, sensor = 'harmonized') {
  const field = SENSOR_FIELD[sensor] ?? 'harmonized'
  const years = []
  for (let y = DATA_START_YEAR; y <= DATA_END_YEAR; y++) years.push(y)
  const grid = () => years.map(() => Array(12).fill(null))
  const data = grid()
  const z = grid()
  const flags = grid()
  const support = grid()

  const empty = !cal.months?.length
  if (empty) data.forEach((row) => row.fill(0))
  for (const r of cal.months ?? []) {
    const yi = r.year - DATA_START_YEAR
    if (yi < 0 || yi >= years.length) continue
    const m = r.month - 1
    // no VIIRS record before 2012: the API zero-fills, we show "no data" instead of a fake zero
    data[yi][m] = sensor === 'viirs' && r.year < VIIRS_START_YEAR ? null : (r[field] ?? null)
    z[yi][m] = r.z ?? null
    flags[yi][m] = r.flag ?? null
    support[yi][m] = r.support ?? null
  }

  return {
    years,
    data,
    z,
    flags,
    support,
    meta: {
      source: cal.meta?.source,
      fallbackReason: cal.meta?.fallbackReason,
      cells: cal.meta?.cells ?? null,
      criticalMonths: cal.meta?.critical_months ?? [],
      validation: cal.meta?.validation ?? null,
      note: cal.meta?.note ?? null,
      sensor,
      empty,
    },
  }
}

/** Yearly totals for MODIS, VIIRS and harmonized (raw vs harmonized comparison chart). */
export function calendarToComparison(cal) {
  const mats = {
    modis: calendarToMatrix(cal, 'modis'),
    viirs: calendarToMatrix(cal, 'viirs'),
    harmonized: calendarToMatrix(cal, 'harmonized'),
  }
  const years = mats.modis.years
  const total = (m, yi) => (m.data[yi].every((v) => v == null) ? null : m.data[yi].reduce((a, b) => a + (b ?? 0), 0))
  return {
    years,
    totals: Object.fromEntries(Object.entries(mats).map(([k, m]) => [k, years.map((_, yi) => total(m, yi))])),
    meta: mats.harmonized.meta,
  }
}

/** Flat monthly rows (newest first) for the Data Explorer table and CSV export. */
export function calendarToRows(cal) {
  return (cal.months ?? [])
    .map((r) => ({
      year: r.year,
      month: r.month,
      period: `${r.year}-${String(r.month).padStart(2, '0')}`,
      modis_raw: r.modis_raw,
      viirs_raw: r.year < VIIRS_START_YEAR ? null : r.viirs_raw,
      harmonized: r.harmonized,
      support: r.support,
      z: r.z,
      flag: r.flag,
    }))
    .sort((a, b) => b.year - a.year || b.month - a.month)
}
