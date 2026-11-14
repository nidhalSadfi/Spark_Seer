import { useQuery } from '@tanstack/react-query'
import { useFilters } from '../context/FilterContext'
import {
  getActiveFires,
  getHotspotRecords,
  getMonthlyMatrix,
  getRegionSummaries,
  getSensorComparison,
} from '../services/fireApi'

const opts = { staleTime: 5 * 60 * 1000, placeholderData: (prev) => prev }

export function useMonthlyMatrix(overrides = {}) {
  const { region, sensor } = useFilters()
  const params = { region, sensor, ...overrides }
  return useQuery({ queryKey: ['matrix', params], queryFn: () => getMonthlyMatrix(params), ...opts })
}

export function useSensorComparison() {
  const { region } = useFilters()
  return useQuery({
    queryKey: ['sensor-comparison', region],
    queryFn: () => getSensorComparison({ region }),
    ...opts,
  })
}

export function useRegionSummaries() {
  const { sensor } = useFilters()
  return useQuery({
    queryKey: ['region-summaries', sensor],
    queryFn: () => getRegionSummaries({ sensor }),
    ...opts,
  })
}

export function useHotspotRecords(params) {
  return useQuery({
    queryKey: ['records', params],
    queryFn: () => getHotspotRecords(params),
    ...opts,
  })
}

/** Near-real-time active fires for the map (region + sensor from the global filters). */
export function useActiveFires() {
  const { region, sensor } = useFilters()
  return useQuery({
    queryKey: ['active-fires', region, sensor],
    queryFn: () => getActiveFires({ region, sensor }),
    ...opts,
  })
}
