import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  addIncidentSchedule,
  getAgentsStatus,
  getBackendIncidents,
  getIncidentRuns,
  getIncidentSchedules,
  getIncidentTickets,
  openIncidentStream,
  removeIncidentSchedule,
  startIncidentRun,
} from '../services/incidentsApi'

/**
 * Incident tickets from the social-media agents. The backend starts them, but they may still be
 * starting or not running at all, so there is no retry: while the agents do not answer the page looks
 * again every few seconds and shows what is wrong.
 */
export function useIncidentTickets() {
  return useQuery({
    queryKey: ['incident-tickets'],
    queryFn: () => getIncidentTickets(200),
    staleTime: 30 * 1000,
    refetchInterval: (query) => (query.state.status === 'error' ? 5 * 1000 : 60 * 1000),
    retry: false,
  })
}

/** Which keys the agents see (booleans only). Pass `enabled: false` while they are unreachable. */
export function useAgentsStatus(enabled = true) {
  return useQuery({
    queryKey: ['incident-agents-status'],
    queryFn: getAgentsStatus,
    enabled,
    staleTime: 30 * 1000,
    refetchInterval: 60 * 1000,
    retry: false,
  })
}

/** Whether the backend started the agents (asked of the main API, so it works while the agents are down). */
export function useBackendIncidents(enabled = true) {
  return useQuery({
    queryKey: ['incidents-backend-status'],
    queryFn: getBackendIncidents,
    enabled,
    staleTime: 0,
    refetchInterval: 5 * 1000,
    retry: false,
  })
}

/** Recent runs (newest first). Pass `enabled: false` while the agents are unreachable. */
export function useIncidentRuns(enabled = true) {
  return useQuery({
    queryKey: ['incident-runs'],
    queryFn: () => getIncidentRuns(12),
    enabled,
    staleTime: 10 * 1000,
    refetchInterval: 30 * 1000,
    retry: false,
  })
}

export function useIncidentSchedules(enabled = true) {
  return useQuery({
    queryKey: ['incident-schedules'],
    queryFn: getIncidentSchedules,
    enabled,
    staleTime: 10 * 1000,
    refetchInterval: 30 * 1000,
    retry: false,
  })
}

/** Start a run: `mutate({ query, hours })` or `mutate({ query, replay_from, replay_to })`. */
export function useStartRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: startIncidentRun,
    onSettled: () => qc.invalidateQueries({ queryKey: ['incident-runs'] }),
  })
}

export function useAddSchedule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: addIncidentSchedule,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['incident-schedules'] }),
  })
}

export function useRemoveSchedule() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: removeIncidentSchedule,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['incident-schedules'] }),
  })
}

const KEEP_EVENTS = 400 // the server replays the latest 300 on connect

/**
 * Live step events of the agents (server-sent events), kept in memory (the latest 400).
 * Connected only while `enabled`. `onEvent` is called for every event as it arrives, including the
 * backlog the server replays when the stream opens, so callers should look at the event's time.
 * Returns `{ events, state }` with state 'connecting' | 'open' | 'error' (the browser keeps retrying).
 */
export function useIncidentEvents({ enabled, onEvent }) {
  const [events, setEvents] = useState([])
  const [state, setState] = useState('connecting')
  const callback = useRef(onEvent)
  useEffect(() => {
    callback.current = onEvent
  })

  useEffect(() => {
    if (!enabled) return undefined
    let buffer = []
    let timer = null
    // a burst (the backlog on connect) becomes one render instead of hundreds
    const flush = () => {
      timer = null
      const batch = buffer
      buffer = []
      setEvents((prev) => {
        const seen = new Set(prev.map((e) => e.id))
        const fresh = batch.filter((e) => !seen.has(e.id))
        return fresh.length ? [...prev, ...fresh].sort((a, b) => a.id - b.id).slice(-KEEP_EVENTS) : prev
      })
    }
    const close = openIncidentStream({
      onEvent: (e) => {
        buffer.push(e)
        callback.current?.(e)
        timer ??= setTimeout(flush, 120)
      },
      onState: setState,
    })
    return () => {
      close()
      clearTimeout(timer)
    }
  }, [enabled])

  return { events, state: enabled ? state : 'connecting' }
}
