/**
 * Minimal fetch wrapper for the EmberLedger API.
 *
 * Base URL: VITE_API_URL (see .env.example), default http://localhost:8000.
 * The API allows any origin, so no dev proxy is needed.
 */
export const API_BASE = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/+$/, '')

// Render free instances sleep after ~15 min idle and can take about a minute to wake.
const TIMEOUT_MS = 65_000

export class ApiError extends Error {
  /** status 0 = the server could not be reached (network error / timeout). */
  constructor(message, status = 0) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** True when the backend itself is down/unreachable/missing data, as opposed to a bad request. */
export const isUnavailable = (e) => e instanceof ApiError && (e.status === 0 || e.status >= 500)

async function request(path, { method = 'GET', params, body, headers } = {}) {
  const url = new URL(API_BASE + path)
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v)
  }

  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  let res
  try {
    res = await fetch(url, {
      method,
      headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers },
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    })
  } catch (e) {
    // A fetch failure is also what the browser reports for a 500 without CORS headers, so keep it generic.
    throw new ApiError(e.name === 'AbortError' ? 'The service took too long to answer. Try again in a moment.' : 'The service is not responding right now.', 0)
  } finally {
    clearTimeout(timer)
  }

  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      // FastAPI: {"detail": "..."} for our errors, {"detail": [{msg, loc}, ...]} for validation errors
      detail = typeof j.detail === 'string' ? j.detail : Array.isArray(j.detail) ? j.detail.map((d) => d.msg).join('; ') : detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, res.status)
  }
  if (res.status === 204) return null // No Content (e.g. DELETE)
  return res.json()
}

export const apiGet = (path, params) => request(path, { params })
export const apiPost = (path, body, headers) => request(path, { method: 'POST', body, headers })
export const apiPatch = (path, body, headers) => request(path, { method: 'PATCH', body, headers })
export const apiDelete = (path, headers) => request(path, { method: 'DELETE', headers })
