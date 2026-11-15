/**
 * EmberLedger API client (backend branch `backend-core`).
 *
 * Endpoints: GET /health, /areas, /calendar, /validation, /weather · POST /investigate
 * (the Processing Center's /processing/* client is services/processingApi.js).
 * `getCalendar` falls back to the offline mock (src/services/fireApi.js) when the backend is
 * unreachable; its `meta.source` says which one answered ('api' | 'mock').
 */
import { apiGet, apiPost, isUnavailable } from './http'
import { mockCalendar } from './fireApi'
import { DATA_START_YEAR } from './constants'

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
 *
 * `end` is left to the API by default: its default is the last year with data (GET /health
 * `data_end_year`, e.g. 2026 with the database), so the dashboard follows new near-real-time
 * months without a code change. The offline mock covers DATA_START_YEAR..DATA_END_YEAR (2025).
 */
export async function getCalendar({ area, bbox, start = DATA_START_YEAR, end }) {
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

// Adapters API calendar → UI shapes live in utils/calendarMatrix.js (pure, checked in Node).
export { calendarEndYear, calendarToComparison, calendarToMatrix, calendarToRows } from '../utils/calendarMatrix'
