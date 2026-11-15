/**
 * Community fire reports: GET/POST/PATCH/DELETE /reports, with a browser-local fallback.
 *
 *   report = { id, lat, lon, observed, note, createdAt, status, reviewedAt }   (times in epoch ms)
 *   status = 'pending' (shown as "?") | 'confirmed' (shown as a fire) | 'false' (dismissed)
 *
 * `loadReports()` asks the API first. When the API is unreachable (network error, 5xx such as
 * 503 "database unavailable", or a backend without the endpoint) it answers from this browser's
 * localStorage instead, with `source: 'local'`; the pages then say that reports are only saved in
 * this browser. Writes go where the list came from (`source`), so a report is never saved half on
 * the server and half locally. The hook (hooks/useReports.js) wraps this with React Query.
 *
 * Reviewing (PATCH) and deleting (DELETE) need the X-Admin-Token header when the server sets
 * REPORTS_ADMIN_TOKEN. The token a reviewer enters is kept in sessionStorage for the tab.
 */
import { apiDelete, apiGet, apiPatch, apiPost, ApiError, isUnavailable } from './http'
import { NOTE_MAX } from '../utils/reports'
import { createBody, isReport, reportFromApi, reportsFromApi } from '../utils/reportMapping'

// ───────────────────────── errors ─────────────────────────

/** PATCH/DELETE refused (401/403): the server wants a reviewer token. */
export class ReportsAuthError extends Error {
  constructor(hadToken) {
    super(
      hadToken
        ? 'The reviewer token was not accepted. Check it with the site administrator and enter it again.'
        : 'Only reviewers can change reports on this server. Enter the reviewer token to review or delete reports.',
    )
    this.name = 'ReportsAuthError'
  }
}

function readable(e, action) {
  if (e instanceof ApiError) {
    if (e.status === 0) return new Error(`Could not ${action}: the server did not answer. Nothing was changed; try again.`)
    if (e.status === 404) return new Error(`Could not ${action}: the report no longer exists on the server (someone may have deleted it).`)
    if (e.status >= 500) return new Error(`Could not ${action}: the server is unavailable (${e.message}). Nothing was changed.`)
    return new Error(`Could not ${action}: ${e.message}`)
  }
  return e
}

// ───────────────────────── reviewer token ─────────────────────────

const TOKEN_KEY = 'spark-seer.reports-admin-token'

export function getAdminToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function setAdminToken(token) {
  try {
    if (token?.trim()) sessionStorage.setItem(TOKEN_KEY, token.trim())
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage blocked: the token is simply not remembered */
  }
}

const adminHeaders = () => {
  const t = getAdminToken()
  return t ? { 'X-Admin-Token': t } : undefined
}

// ───────────────────────── browser-local fallback ─────────────────────────

const KEY = 'spark-seer.fire-reports.v1'

function readLocal() {
  try {
    const parsed = JSON.parse(localStorage.getItem(KEY) ?? '[]')
    return Array.isArray(parsed) ? parsed.filter(isReport) : []
  } catch {
    return [] // storage blocked, or the stored value is not valid JSON
  }
}

function writeLocal(list) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list))
  } catch {
    /* storage full or blocked: the change is lost on reload */
  }
}

/** Calls `listener` when another tab changes the browser-local reports. Returns an unsubscribe. */
export function subscribeLocal(listener) {
  if (typeof window === 'undefined') return () => {}
  const onStorage = (e) => {
    if (e.key === KEY || e.key === null) listener()
  }
  window.addEventListener('storage', onStorage)
  return () => window.removeEventListener('storage', onStorage)
}

const newId = () => `rep-${(globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`).slice(0, 13)}`
const round = (n) => +n.toFixed(5)

function checkCoords({ lat, lon }) {
  if (!(Number.isFinite(lat) && Math.abs(lat) <= 90 && Number.isFinite(lon) && Math.abs(lon) <= 180)) {
    throw new RangeError('Latitude must be between -90 and 90 and longitude between -180 and 180.')
  }
}

const local = {
  create(values) {
    const body = createBody(values, NOTE_MAX)
    const report = { id: newId(), ...body, lat: round(body.lat), lon: round(body.lon), createdAt: Date.now(), status: 'pending', reviewedAt: null }
    writeLocal([report, ...readLocal()])
    return report
  },
  setStatus(id, status) {
    let updated = null
    writeLocal(
      readLocal().map((r) => {
        if (r.id !== id) return r
        updated = { ...r, status, reviewedAt: status === 'pending' ? null : Date.now() }
        return updated
      }),
    )
    if (!updated) throw new Error('This report no longer exists in this browser.')
    return updated
  },
  remove(id) {
    writeLocal(readLocal().filter((r) => r.id !== id))
  },
}

// ───────────────────────── API ─────────────────────────

/**
 * All reports, newest first: `{ reports, source: 'api' | 'local', fallbackReason? }`.
 * Throws only for errors that are not "the server is unreachable" (e.g. a 4xx).
 */
export async function loadReports() {
  try {
    const payload = await apiGet('/reports')
    return { reports: reportsFromApi(payload), source: 'api' }
  } catch (e) {
    const missing = e instanceof ApiError && e.status === 404 // a backend deployed before the endpoint existed
    if (!isUnavailable(e) && !missing) throw e
    return {
      reports: readLocal().sort((a, b) => b.createdAt - a.createdAt),
      source: 'local',
      fallbackReason: missing ? 'This API has no /reports endpoint yet.' : e.message,
    }
  }
}

/** File an unverified alert where the list came from. Resolves to the new report. */
export async function createReport(values, source) {
  checkCoords(values)
  if (source === 'local') return local.create(values)
  let created
  try {
    created = await apiPost('/reports', createBody(values, NOTE_MAX))
  } catch (e) {
    throw readable(e, 'file the report')
  }
  const report = reportFromApi(created)
  if (!report) throw new Error('The server saved the report but answered with data this page cannot read. Reload to see it.')
  return report
}

/** Set the status ('pending' | 'confirmed' | 'false'). Resolves to the updated report. */
export async function setReportStatus(id, status, source) {
  if (source === 'local') return local.setStatus(id, status)
  const headers = adminHeaders()
  let updated
  try {
    updated = await apiPatch(`/reports/${encodeURIComponent(id)}`, { status }, headers)
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) throw new ReportsAuthError(Boolean(headers))
    throw readable(e, status === 'pending' ? 'reopen the report' : 'save the review')
  }
  const report = reportFromApi(updated)
  if (!report) throw new Error('The server saved the review but answered with data this page cannot read. Reload to see it.')
  return report
}

/** Delete a report. */
export async function removeReport(id, source) {
  if (source === 'local') return local.remove(id)
  const headers = adminHeaders()
  try {
    await apiDelete(`/reports/${encodeURIComponent(id)}`, headers)
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) throw new ReportsAuthError(Boolean(headers))
    throw readable(e, 'delete the report')
  }
}
