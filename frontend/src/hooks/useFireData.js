import { useCallback } from 'react'
import { useMutation, useQueries, useQuery } from '@tanstack/react-query'
import { useFilters } from '../context/FilterContext'
import { getActiveFires } from '../services/fireApi'
import {
  calendarToComparison,
  calendarToMatrix,
  calendarToRows,
  getCalendar,
  getHealth,
  getPointWeather,
  getValidation,
  getWeather,
  investigate,
} from '../services/emberApi'
import { anomalyPct, historicalMedian, latestObservation } from '../utils/stats'

const opts = { staleTime: 5 * 60 * 1000, placeholderData: (prev) => prev }

// ───────────────────────── calendar (one request feeds several views) ─────────────────────────

/** Raw GET /calendar response for the current area or AOI. Views derive from it via `select`. */
function useCalendarQuery(select) {
  const { target } = useFilters()
  return useQuery({
    queryKey: ['calendar', target.key],
    queryFn: () => getCalendar(target),
    select,
    ...opts,
  })
}

/** Year × month matrix for the selected sensor (see calendarToMatrix for the shape). */
export function useMonthlyMatrix() {
  const { sensor } = useFilters()
  return useCalendarQuery(useCallback((cal) => calendarToMatrix(cal, sensor), [sensor]))
}

export function useSensorComparison() {
  return useCalendarQuery(calendarToComparison)
}

/** Flat monthly rows (all sensors) for the Data Explorer. */
export function useCalendarRows() {
  return useCalendarQuery(calendarToRows)
}

function summarize(cal, area, sensor) {
  const mat = calendarToMatrix(cal, sensor)
  const last = latestObservation(mat)
  const current = mat.data[last.yearIdx][last.month]
  const baseline = historicalMedian(mat, last.year, last.month)
  return { id: area.id, name: area.name, year: last.year, month: last.month, current, baseline, anomaly: anomalyPct(current, baseline) }
}

const combineSummaries = (results) => ({
  data: results.every((r) => r.data) ? results.map((r) => r.data) : undefined,
  isLoading: results.some((r) => r.isPending),
})

/** Latest-month snapshot per named area, for rankings and anomaly alerts. */
export function useRegionSummaries() {
  const { areas, sensor } = useFilters()
  return useQueries({
    queries: areas.map((a) => ({
      queryKey: ['calendar', `area:${a.id}`], // shared with the per-area calendar queries
      queryFn: () => getCalendar({ area: a.id }),
      select: (cal) => summarize(cal, a, sensor),
      staleTime: opts.staleTime,
    })),
    combine: combineSummaries,
  })
}

// ───────────────────────── other endpoints ─────────────────────────

export function useValidation() {
  return useQuery({ queryKey: ['validation'], queryFn: getValidation, staleTime: 30 * 60 * 1000, retry: false })
}

/** 3-day weather context. The endpoint is keyed by named area, so an AOI falls back to the selected area. */
export function useWeather() {
  const { region } = useFilters()
  return useQuery({
    queryKey: ['weather', region],
    queryFn: () => getWeather(region),
    staleTime: 30 * 60 * 1000,
    retry: false,
  })
}

/**
 * Weather + location context for a community fire report. It only runs once the report has been
 * confirmed as a real fire: that is what triggers the lookup for its coordinates.
 * The backend rounds to ~1 km, so the query key does too (nearby reports share one forecast).
 */
export function usePointWeather(report) {
  const confirmed = report?.status === 'confirmed'
  return useQuery({
    queryKey: ['weather-point', report?.lat.toFixed(2), report?.lon.toFixed(2)],
    queryFn: () => getPointWeather(report.lat, report.lon),
    enabled: confirmed,
    staleTime: 30 * 60 * 1000,
    retry: false,
  })
}

/** Evidence Passport for the current area/AOI. Call `.mutate({ year, month })` with month 1-12. */
export function usePassport() {
  const { target } = useFilters()
  return useMutation({
    mutationFn: ({ year, month }) => investigate({ area: target.area, bbox: target.bbox, year, month }),
  })
}

/**
 * Where the numbers come from:
 *   connecting | live | synthetic (backend serving make_fake_data.py output) | offline (mock fallback)
 */
export function useDataMode() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    retry: false,
    staleTime: 30 * 1000,
    refetchInterval: 60 * 1000,
  })
  const validation = useValidation()
  let mode = 'live'
  if (health.isPending) mode = 'connecting'
  else if (health.isError) mode = 'offline'
  else if (validation.data?.synthetic) mode = 'synthetic'
  return { mode, rows: health.data?.rows ?? null, validation: validation.data ?? null }
}

/** Near-real-time active fires for the map. Mock feed: the backend has no endpoint for this yet. */
export function useActiveFires() {
  const { region, sensor } = useFilters()
  return useQuery({
    queryKey: ['active-fires', region, sensor],
    queryFn: () => getActiveFires({ region, sensor }),
    ...opts,
  })
}
