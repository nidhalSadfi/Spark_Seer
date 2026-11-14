import { MONTHS } from '../../utils/stats'

/** Colour per satellite, used for pass markers in the map panel and chart. */
export const SAT_COLORS = {
  'NOAA-20': '#7dd3fc',
  'NOAA-21': '#a5b4fc',
  'S-NPP': '#c4b5fd',
  Terra: '#fcd34d',
  Aqua: '#86efac',
}

/** Fire Weather Index danger classes (EFFIS-style thresholds). */
export const FWI_CLASSES = [
  { max: 11.2, label: 'Low', color: '#2f8f5b' },
  { max: 21.3, label: 'Moderate', color: '#c9b13b' },
  { max: 38, label: 'High', color: '#e9832b' },
  { max: 50, label: 'Very high', color: '#e0412a' },
  { max: Infinity, label: 'Extreme', color: '#9b1b4f' },
]

/** Danger class for an FWI value, plus its position (0–1) on an equal-width class gauge. */
export function fwiClass(fwi) {
  const i = Math.max(0, FWI_CLASSES.findIndex((c) => fwi < c.max))
  const c = FWI_CLASSES[i]
  const lo = i ? FWI_CLASSES[i - 1].max : 0
  const hi = c.max === Infinity ? 70 : c.max
  const within = Math.min(1, Math.max(0, (fwi - lo) / (hi - lo)))
  return { ...c, index: i, pos: (i + within) / FWI_CLASSES.length }
}

const pad = (n) => String(n).padStart(2, '0')

/** "31 Oct · 13:42 UTC", or just "13:42" with { date: false }. */
export function fmtUtc(t, { date = true, utc = true } = {}) {
  const d = new Date(t)
  const hm = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
  if (!date) return utc ? `${hm} UTC` : hm
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} · ${hm}${utc ? ' UTC' : ''}`
}

export const fmtCoord = (lat, lon) =>
  `${Math.abs(lat).toFixed(4)}°${lat >= 0 ? 'N' : 'S'}, ${Math.abs(lon).toFixed(4)}°${lon >= 0 ? 'E' : 'W'}`
