/**
 * Client for the fire incident agents (python -m incident_agents serve, backend/harmonization-modis-viirs).
 *
 * A separate local service from the EmberLedger API: it holds the social-media keys and makes paid
 * calls, so it listens on 127.0.0.1 only and the dashboard reads it with GET requests (tickets).
 * Base URL: VITE_INCIDENTS_URL (see .env.example), default http://127.0.0.1:8765.
 */
import { ApiError } from './http'
import { ticketsFromApi } from '../utils/incidents'

export const INCIDENTS_BASE = (import.meta.env.VITE_INCIDENTS_URL || 'http://127.0.0.1:8765').replace(/\/+$/, '')

const TIMEOUT_MS = 15_000

/** All tickets, most recently updated first. */
export async function getIncidentTickets(limit = 200) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  let res
  try {
    res = await fetch(`${INCIDENTS_BASE}/api/tickets?limit=${encodeURIComponent(limit)}`, { signal: ctrl.signal })
  } catch (e) {
    throw new ApiError(e.name === 'AbortError' ? 'The incident agents took too long to respond.' : 'No response from the incident agents.', 0)
  } finally {
    clearTimeout(timer)
  }
  if (!res.ok) throw new ApiError(res.statusText || 'The incident agents answered with an error.', res.status)
  return ticketsFromApi(await res.json())
}
