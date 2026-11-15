/**
 * Client for the fire incident agents (backend/harmonization-modis-viirs/incident_agents).
 *
 * The backend starts them with the API, as a separate process (backend/api/incidents.py): they hold the
 * social-media keys (from the repo-root .env) and make paid calls, so they listen on 127.0.0.1 only.
 * The Social Alerts page reads their tickets and runs its agent console through them (start a run,
 * schedules, live step events).
 * Base URL: VITE_INCIDENTS_URL (see .env.example), default http://127.0.0.1:8765 (the backend's
 * INCIDENT_PORT). The agents list the dashboard's origin in INCIDENT_CORS_ORIGINS (localhost:5173 and
 * :4173 by default).
 */
import { ApiError, apiGet } from './http'
import { eventFromApi, runFromApi, scheduleFromApi } from '../utils/agentFlow'
import { ticketsFromApi } from '../utils/incidents'

export const INCIDENTS_BASE = (import.meta.env.VITE_INCIDENTS_URL || 'http://127.0.0.1:8765').replace(/\/+$/, '')

const TIMEOUT_MS = 15_000

async function request(path, { method = 'GET', body } = {}) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  let res
  try {
    res = await fetch(`${INCIDENTS_BASE}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    })
  } catch (e) {
    throw new ApiError(e.name === 'AbortError' ? 'The incident agents took too long to respond.' : 'No response from the incident agents.', 0)
  } finally {
    clearTimeout(timer)
  }
  if (!res.ok) {
    let detail = res.statusText || 'The incident agents answered with an error.'
    try {
      const j = await res.json()
      // FastAPI: {"detail": "..."} for our errors, {"detail": [{msg, loc}, ...]} for validation errors
      detail = typeof j.detail === 'string' ? j.detail : Array.isArray(j.detail) ? j.detail.map((d) => d.msg).join('; ') : detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, res.status)
  }
  return res.json()
}

const list = (payload, what) => {
  if (!Array.isArray(payload)) throw new Error(`The incident agents answered with ${what} this page cannot read.`)
  return payload
}

/** All tickets, most recently updated first. */
export async function getIncidentTickets(limit = 200) {
  return ticketsFromApi(await request(`/api/tickets?limit=${encodeURIComponent(limit)}`))
}

/** Which keys the agents can see (booleans only) and which .env files exist: `{ keys: {groq, x, bluesky, firms}, env_files, model }`. */
export const getAgentsStatus = () => request('/api/status')

/**
 * Did the backend start the agents? GET /incidents/status on the main API (not on the agents), so it still
 * answers when the agents are down: `{ autostart, running, managed, port, url, gave_up, last_error, ... }`.
 */
export const getBackendIncidents = () => apiGet('/incidents/status')

/** Recent runs, newest first. */
export async function getIncidentRuns(limit = 12) {
  return list(await request(`/api/runs?limit=${encodeURIComponent(limit)}`), 'runs').map(runFromApi).filter(Boolean)
}

export async function getIncidentSchedules() {
  return list(await request('/api/schedules'), 'schedules').map(scheduleFromApi).filter(Boolean)
}

/**
 * Start a run in the background: `{ query, hours }` for a live run, `{ query, replay_from, replay_to }`
 * (YYYY-MM-DD) to replay past dates. The server refuses a second run at once (409).
 */
export const startIncidentRun = (body) => request('/api/runs', { method: 'POST', body })

/** `{ query, hours, interval_min }`: live runs on a schedule (the server enforces a 15 min floor). */
export const addIncidentSchedule = (body) => request('/api/schedules', { method: 'POST', body })

export const removeIncidentSchedule = (id) => request(`/api/schedules/${encodeURIComponent(id)}`, { method: 'DELETE' })

/**
 * Live step events (server-sent events). The first connection replays the latest 300 events; the
 * browser reconnects by itself and resumes from the last event it saw. Returns a function that closes it.
 *   onEvent(event)  every parsed event;   onState('open' | 'error')  connection changes
 */
export function openIncidentStream({ onEvent, onState }) {
  const source = new EventSource(`${INCIDENTS_BASE}/api/events/stream?after=0`)
  source.onopen = () => onState?.('open')
  source.onerror = () => onState?.('error') // the browser keeps retrying
  source.onmessage = (m) => {
    let e = null
    try {
      e = eventFromApi(JSON.parse(m.data))
    } catch {
      /* a malformed line is skipped, not fatal */
    }
    if (e) onEvent(e)
  }
  return () => source.close()
}
