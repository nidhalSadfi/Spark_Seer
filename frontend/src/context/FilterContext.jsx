import { createContext, useContext, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { DATA_RANGE } from '../services/fireApi'
import { getAreas, getHealth } from '../services/emberApi'
import { FALLBACK_AREAS } from '../services/constants'
import { bboxOf } from '../utils/geo'

/**
 * Global filter state, preserved across pages.
 *
 *  - `region`   id of a named area from GET /areas (placeholder list until the API answers)
 *  - `aoi`      GeoJSON feature drawn on the map; when set it overrides `region` and the
 *               pages query the API by bbox instead
 *  - `target`   what the data hooks send to the API: { area } or { bbox }, plus a stable `key`
 *  - `sensor`, `startYear`, `endYear`
 *  - `dataEndYear` last year with data: GET /health `data_end_year` (the API's default calendar
 *               end, e.g. 2026 with the database). DATA_RANGE.endYear (2025) only while /health
 *               has not answered, when it is unreachable (offline mock) or from an older API.
 *               `endYear` follows it until the user picks a year range.
 */
const FilterContext = createContext(null)
const DEFAULT_REGION = 'bizerte'

export function FilterProvider({ children }) {
  const [region, setRegion] = useState(DEFAULT_REGION)
  const [sensor, setSensor] = useState('harmonized')
  const [startYear, setStartYear] = useState(DATA_RANGE.startYear)
  const [pickedEndYear, setEndYear] = useState(null) // null: follow dataEndYear
  const [aoi, setAoi] = useState(null)

  const { data: apiAreas } = useQuery({
    queryKey: ['areas'],
    queryFn: getAreas,
    staleTime: Infinity,
    retry: 1,
  })
  const areas = apiAreas?.length ? apiAreas : FALLBACK_AREAS

  // Same query as useDataMode's badge (one request); retry off so offline falls back at once.
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: getHealth, retry: false, staleTime: 30 * 1000 })
  const dataEndYear = Number.isInteger(health?.data_end_year) ? health.data_end_year : DATA_RANGE.endYear
  const endYear = pickedEndYear ?? dataEndYear

  const value = useMemo(() => {
    const activeRegion = areas.some((a) => a.id === region) ? region : areas[0].id
    const aoiBbox = aoi ? bboxOf(aoi) : null
    const target = aoiBbox
      ? { bbox: aoiBbox, key: `bbox:${aoiBbox.join(',')}`, name: 'Custom area' }
      : { area: activeRegion, key: `area:${activeRegion}`, name: areas.find((a) => a.id === activeRegion)?.name ?? activeRegion }
    return {
      areas,
      areaName: (id) => areas.find((a) => a.id === id)?.name ?? id,
      region: activeRegion,
      // picking a named area replaces any AOI drawn on the map
      setRegion: (id) => {
        setRegion(id)
        setAoi(null)
      },
      sensor,
      setSensor,
      startYear,
      endYear,
      dataEndYear,
      setYearRange: (s, e) => {
        setStartYear(Math.min(s, e))
        setEndYear(Math.max(s, e))
      },
      aoi,
      setAoi,
      target,
    }
  }, [areas, region, sensor, startYear, endYear, dataEndYear, aoi])

  return <FilterContext.Provider value={value}>{children}</FilterContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useFilters() {
  const ctx = useContext(FilterContext)
  if (!ctx) throw new Error('useFilters must be used inside <FilterProvider>')
  return ctx
}
