import { haversineKm } from './geo'

/**
 * Shared vocabulary for community fire reports (see services/reportsStore.js).
 *
 * Lifecycle: a report starts `pending` (shown as "?" on the map), then a reviewer marks it
 * `confirmed` (shown as a fire, with weather and location info) or `false` (a false alarm, hidden).
 */
export const STATUS = {
  pending: { label: 'Unverified', short: 'Unverified', tone: 'warn' },
  confirmed: { label: 'Confirmed fire', short: 'Confirmed', tone: 'fire' },
  false: { label: 'False alarm', short: 'False alarm', tone: 'neutral' },
}

export const OBSERVED = [
  { id: 'smoke', label: 'Smoke' },
  { id: 'flames', label: 'Flames' },
  { id: 'both', label: 'Smoke + flames' },
  { id: 'unsure', label: 'Not sure' },
]
export const observedLabel = (id) => OBSERVED.find((o) => o.id === id)?.label ?? 'Not sure'

/** Radius used to look for satellite hotspots around a reported point. */
export const CORROBORATION_KM = 10

export const NOTE_MAX = 280

const pad = (n) => String(n).padStart(2, '0')

/** "36.7012°N, 10.8049°E" */
export const fmtLatLon = (lat, lon) =>
  `${Math.abs(lat).toFixed(4)}°${lat >= 0 ? 'N' : 'S'}, ${Math.abs(lon).toFixed(4)}°${lon >= 0 ? 'E' : 'W'}`

/** "just now", "12 min ago", "3 h ago", or the date for anything older than two days. */
export function timeAgo(t, now = Date.now()) {
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 45) return 'just now'
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  if (s < 2 * 86400) return 'yesterday'
  return fmtLocal(t, { time: false })
}

/** Local date and time, e.g. "15 Nov 2026, 14:05". */
export function fmtLocal(t, { time = true } = {}) {
  const d = new Date(t)
  const date = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
  return time ? `${date}, ${pad(d.getHours())}:${pad(d.getMinutes())}` : date
}

/** Parse a coordinate typed by a user; returns a finite number inside [-limit, limit], or null. */
export function parseCoord(text, limit) {
  if (text == null || String(text).trim() === '') return null
  const n = Number(String(text).trim().replace(',', '.'))
  return Number.isFinite(n) && Math.abs(n) <= limit ? n : null
}

/**
 * What the satellite feed shows around a reported point: hotspot detections inside the radius
 * (last 24 h) and the nearest fire cluster. `feed` is the useActiveFires() payload.
 */
export function satelliteCheck(report, feed, radiusKm = CORROBORATION_KM) {
  if (!report || !feed) return null
  const within = feed.detections.filter((d) => haversineKm(report.lat, report.lon, d.lat, d.lon) <= radiusKm)
  let nearest = null
  for (const f of feed.fires) {
    const km = haversineKm(report.lat, report.lon, f.lat, f.lon)
    if (!nearest || km < nearest.km) nearest = { id: f.id, name: f.name, km }
  }
  return { radiusKm, detections: within, nearest }
}

/** An existing open report within `km` of a point (to warn about duplicates). */
export function nearbyReport(reports, lat, lon, km = 1) {
  let best = null
  for (const r of reports) {
    if (r.status === 'false') continue
    const d = haversineKm(lat, lon, r.lat, r.lon)
    if (d <= km && (!best || d < best.km)) best = { report: r, km: d }
  }
  return best
}
