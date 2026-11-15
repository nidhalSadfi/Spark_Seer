/**
 * Community report records: API shape ↔ UI shape (pure functions, no imports, so a plain node
 * script can check them; see frontend/dev-checks/).
 *
 *   API (GET/POST/PATCH /reports): { id, lat, lon, observed, note, status,
 *                                    createdAt: ISO, reviewedAt: ISO | null }
 *   UI  (pages, map, localStorage): the same fields, with createdAt / reviewedAt in epoch ms
 *                                   (timeAgo, fmtLocal and sorting work on numbers)
 */

export const STATUSES = ['pending', 'confirmed', 'false']
const OBSERVED_IDS = ['smoke', 'flames', 'both', 'unsure']

/** A UI report the pages can render safely. */
export const isReport = (r) =>
  r != null &&
  typeof r.id === 'string' &&
  r.id !== '' &&
  Number.isFinite(r.lat) &&
  Math.abs(r.lat) <= 90 &&
  Number.isFinite(r.lon) &&
  Math.abs(r.lon) <= 180 &&
  STATUSES.includes(r.status) &&
  Number.isFinite(r.createdAt)

const toMs = (iso) => {
  if (iso == null || iso === '') return null
  const t = typeof iso === 'number' ? iso : Date.parse(iso)
  return Number.isFinite(t) ? t : null
}

/** One API report → UI report, or null when it cannot be shown (bad id, position, status or date). */
export function reportFromApi(r) {
  if (r == null || typeof r !== 'object') return null
  const report = {
    id: r.id == null ? '' : String(r.id),
    lat: typeof r.lat === 'number' ? r.lat : Number(r.lat),
    lon: typeof r.lon === 'number' ? r.lon : Number(r.lon),
    observed: OBSERVED_IDS.includes(r.observed) ? r.observed : 'unsure',
    note: typeof r.note === 'string' ? r.note : '',
    status: r.status,
    createdAt: toMs(r.createdAt),
    reviewedAt: r.status === 'pending' ? null : toMs(r.reviewedAt),
  }
  return isReport(report) ? report : null
}

/** GET /reports payload (`{reports: [...]}` or a bare list) → UI reports, newest first. */
export function reportsFromApi(payload) {
  const list = Array.isArray(payload) ? payload : (payload?.reports ?? [])
  return list
    .map(reportFromApi)
    .filter(Boolean)
    .sort((a, b) => b.createdAt - a.createdAt)
}

/** The POST /reports body for a new alert (the server trims the note and checks lat/lon too). */
export const createBody = ({ lat, lon, observed = 'unsure', note = '' }, noteMax = 280) => ({
  lat,
  lon,
  observed: OBSERVED_IDS.includes(observed) ? observed : 'unsure',
  note: String(note ?? '').trim().slice(0, noteMax),
})
