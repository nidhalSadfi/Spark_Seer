/**
 * Social-media fire detection ("incident agents", backend/harmonization-modis-viirs/incident_agents).
 *
 * The agents search public posts and news for fires, merge the mentions into one ticket per
 * incident and check each ticket against satellite detections. A ticket is an UNVERIFIED public
 * mention, never an official alert. These helpers map the agents' GET /api/tickets JSON to the
 * shapes the Social Alerts page uses, and build the community report filed from a ticket.
 *
 * No imports on purpose: dev-checks/incidents.check.mjs runs this file in plain Node.
 */

/** Ticket status words (set by the agents' satellite check). */
export const TICKET_STATUS = {
  'Satellite overlap': { tone: 'ok', hint: 'A satellite detection was found near this place and time.' },
  'No satellite support': { tone: 'warn', hint: 'The satellite check ran and found nothing. That is not evidence that there is no fire.' },
  Mentioned: { tone: 'neutral', hint: 'No satellite check could run.' },
}
export const statusInfo = (status) => TICKET_STATUS[status] ?? { tone: 'neutral', hint: '' }

export const CATEGORY_LABEL = {
  active_fire_report: 'Fire reported',
  smoke_or_possible_fire: 'Smoke reported',
  news_about_fire: 'Fire in the news',
  satellite: 'Satellite fire cluster',
}
export const categoryLabel = (c) => CATEGORY_LABEL[c] ?? 'Fire mention'

export const SOURCE_LABEL = {
  bluesky: 'Bluesky',
  x: 'X',
  gdelt: 'GDELT news',
  kanari_signal: 'Kanari signal',
  kanari_satellite: 'Kanari satellite',
}
export const sourceLabel = (s) => SOURCE_LABEL[s] ?? String(s ?? 'Source')

/** The `observed` value (what a community report says was seen) that best matches a ticket. */
export function observedFor(category) {
  if (category === 'active_fire_report') return 'flames'
  if (category === 'smoke_or_possible_fire') return 'smoke'
  return 'unsure'
}

/** Only http(s) links from the feeds are ever rendered as links (never javascript: and the like). */
export function safeUrl(u) {
  try {
    const url = new URL(String(u))
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}

const isoMs = (s) => {
  const t = Date.parse(s)
  return Number.isFinite(t) ? t : null
}
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const str = (v) => (typeof v === 'string' ? v : '')

function sourceFromApi(s) {
  if (!s || typeof s !== 'object') return null
  return {
    source: str(s.source),
    kind: str(s.kind),
    url: safeUrl(s.url),
    author: str(s.author) || null,
    publishedAt: isoMs(s.published_at),
    fetchedAt: isoMs(s.fetched_at),
    excerpt: str(s.excerpt),
    placePhrase: str(s.place_phrase) || null,
  }
}

/**
 * One ticket from GET /api/tickets, or null when it has no id.
 * Times become epoch ms; tickets without coordinates are kept but cannot be reported.
 */
export function ticketFromApi(raw) {
  if (!raw || typeof raw !== 'object' || !Number.isInteger(raw.id)) return null
  const lat = num(raw.lat)
  const lon = num(raw.lon)
  const sat = raw.satellite && typeof raw.satellite === 'object' ? raw.satellite : null
  const sources = (Array.isArray(raw.sources) ? raw.sources : []).map(sourceFromApi).filter(Boolean)
  const hist = raw.history && typeof raw.history === 'object' ? raw.history : null
  return {
    id: raw.id,
    title: str(raw.title) || 'Fire mention',
    place: str(raw.place),
    country: str(raw.country),
    lat,
    lon,
    hasLocation: lat != null && lon != null && Math.abs(lat) <= 90 && Math.abs(lon) <= 180,
    precision: str(raw.precision),
    radiusKm: num(raw.radius_km),
    firstSeen: isoMs(raw.first_seen),
    lastSeen: isoMs(raw.last_seen),
    createdAt: str(raw.created_at),
    updatedAt: isoMs(raw.updated_at),
    status: str(raw.status) || 'Mentioned',
    category: str(raw.category),
    replay: raw.mode === 'replay',
    sources,
    satellite: sat && {
      checked: Array.isArray(sat.checked) ? sat.checked.map(String) : [],
      radiusKm: num(sat.radius_km),
      matches: (Array.isArray(sat.matches) ? sat.matches : []).filter((m) => m && typeof m === 'object'),
      lead: sat.lead && typeof sat.lead === 'object' ? sat.lead : null,
    },
    history: hist && str(hist.text) ? { text: hist.text, cellId: str(hist.cell_id) } : null,
  }
}

export function ticketsFromApi(payload) {
  if (!Array.isArray(payload)) throw new Error('The incident agents answered with data this page cannot read.')
  return payload.map(ticketFromApi).filter(Boolean)
}

/** Earliest source that has a link (the post or article that started the ticket). */
export function firstLink(ticket) {
  const withUrl = ticket.sources.filter((s) => s.url)
  withUrl.sort((a, b) => (a.publishedAt ?? Infinity) - (b.publishedAt ?? Infinity))
  return withUrl[0]?.url ?? null
}

/** A location that is only known to a wide area, so the pin marks its centre rather than the fire. */
export const isApproximate = (t) => t.precision === 'region' || (t.radiusKm ?? 0) > 25

// ───────────────────────── filing a community report from a ticket ─────────────────────────

/** Text every report filed from a ticket starts with. It is how the page recognises "already reported". */
export const reportPrefix = (ticket) => `Social #${ticket.id}:`

/** The note of the community report: starts with the prefix, then what the agents found (at most `max` chars). */
export function reportNote(ticket, max = 280) {
  const n = ticket.sources.length
  const parts = [
    `${reportPrefix(ticket)} ${ticket.title}`,
    ticket.radiusKm != null ? `±${Math.round(ticket.radiusKm)} km` : null,
    `${n} public ${n === 1 ? 'mention' : 'mentions'}`,
    ticket.status !== 'Mentioned' ? ticket.status.toLowerCase() : null,
    firstLink(ticket),
  ].filter(Boolean)
  const note = parts.join(' · ')
  return note.length <= max ? note : `${note.slice(0, max - 1)}…`
}

/** The values for `addReport` (lat, lon, observed, note). Throws when the ticket has no usable location. */
export function reportValues(ticket, max = 280) {
  if (!ticket.hasLocation) throw new Error('This ticket has no coordinates, so it cannot be placed on the map.')
  return { lat: ticket.lat, lon: ticket.lon, observed: observedFor(ticket.category), note: reportNote(ticket, max) }
}

/** The community report previously filed from this ticket, if any (same prefix, same place). */
export function reportOf(ticket, reports) {
  if (!ticket.hasLocation) return null
  const prefix = reportPrefix(ticket)
  return (
    reports.find(
      (r) => typeof r.note === 'string' && r.note.startsWith(prefix) && Math.abs(r.lat - ticket.lat) < 0.001 && Math.abs(r.lon - ticket.lon) < 0.001,
    ) ?? null
  )
}

// ───────────────────────── wording ─────────────────────────

const fmtMin = (m) => (m >= 120 ? `${(m / 60).toFixed(1)} h` : `${m} min`)

/** "posts 47 min before" / "satellite 2.0 h before" / "same time" for a signed lead (positive: posts first). */
export function leadWords(minutes) {
  if (minutes == null) return null
  if (minutes > 0) return `posts ${fmtMin(minutes)} before`
  if (minutes < 0) return `satellite ${fmtMin(-minutes)} before`
  return 'same time'
}

/** One line for a satellite match: "kanari Calenzana · 3.2 km". */
export function matchLine(m) {
  const what = [m.source, m.place || m.sensor].filter(Boolean).join(' ')
  const km = typeof m.km === 'number' ? ` · ${m.km} km` : ''
  return `${what}${km}`
}
