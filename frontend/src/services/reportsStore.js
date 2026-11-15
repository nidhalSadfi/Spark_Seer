/**
 * Community fire reports: a tiny browser-local store.
 *
 * The EmberLedger API has no endpoint for user reports yet, so they live in this browser's
 * localStorage (they survive reloads and sync between tabs, but are NOT shared between users or
 * devices). Everything goes through the functions below, so moving to an API later only means
 * changing this file.
 *
 *   report = { id, lat, lon, observed, note, createdAt, status, reviewedAt }
 *   status = 'pending' (shown as "?") | 'confirmed' (shown as a fire) | 'false' (dismissed)
 */
import { NOTE_MAX } from '../utils/reports'

const KEY = 'spark-seer.fire-reports.v1'
const STATUSES = ['pending', 'confirmed', 'false']
const EMPTY = Object.freeze([])

const isReport = (r) =>
  r &&
  typeof r.id === 'string' &&
  Number.isFinite(r.lat) &&
  Math.abs(r.lat) <= 90 &&
  Number.isFinite(r.lon) &&
  Math.abs(r.lon) <= 180 &&
  STATUSES.includes(r.status) &&
  Number.isFinite(r.createdAt)

function read() {
  try {
    const parsed = JSON.parse(localStorage.getItem(KEY) ?? '[]')
    return Array.isArray(parsed) ? parsed.filter(isReport) : []
  } catch {
    return [] // storage blocked, or the stored value is not valid JSON
  }
}

let current = null // parsed list, replaced (never mutated) on every change so React can compare it
const listeners = new Set()
const emit = () => listeners.forEach((l) => l())

export const getReports = () => (current ??= read())
export const getServerReports = () => EMPTY

export function subscribe(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function save(list) {
  current = list
  try {
    localStorage.setItem(KEY, JSON.stringify(list))
  } catch {
    /* storage full or blocked: the reports stay available until the tab is closed */
  }
  emit()
}

// another tab changed the reports
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key === KEY || e.key === null) {
      current = read()
      emit()
    }
  })
}

const newId = () => `rep-${(globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`).slice(0, 13)}`
const round = (n) => +n.toFixed(5)

/** Create an unverified alert. Throws when the coordinates are out of range. */
export function addReport({ lat, lon, observed = 'unsure', note = '' }) {
  if (!(Number.isFinite(lat) && Math.abs(lat) <= 90 && Number.isFinite(lon) && Math.abs(lon) <= 180)) {
    throw new RangeError('Latitude must be between -90 and 90 and longitude between -180 and 180.')
  }
  const report = {
    id: newId(),
    lat: round(lat),
    lon: round(lon),
    observed,
    note: String(note).trim().slice(0, NOTE_MAX),
    createdAt: Date.now(),
    status: 'pending',
    reviewedAt: null,
  }
  save([report, ...getReports()])
  return report
}

const update = (id, patch) => save(getReports().map((r) => (r.id === id ? { ...r, ...patch } : r)))

/** Mark a report as a real fire (`true`) or a false alarm (`false`). */
export const reviewReport = (id, isFire) => update(id, { status: isFire ? 'confirmed' : 'false', reviewedAt: Date.now() })

/** Put a reviewed report back to unverified. */
export const reopenReport = (id) => update(id, { status: 'pending', reviewedAt: null })

export const deleteReport = (id) => save(getReports().filter((r) => r.id !== id))
