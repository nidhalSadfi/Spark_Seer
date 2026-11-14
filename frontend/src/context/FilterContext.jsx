import { createContext, useContext, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { DATA_RANGE } from '../services/fireApi'
import { getAreas } from '../services/emberApi'
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
 */
const FilterContext = createContext(null)
const DEFAULT_REGION = 'bizerte'

export function FilterProvider({ children }) {
  const [region, setRegion] = useState(DEFAULT_REGION)
  const [sensor, setSensor] = useState('harmonized')
  const [startYear, setStartYear] = useState(DATA_RANGE.startYear)
  const [endYear, setEndYear] = useState(DATA_RANGE.endYear)
  const [aoi, setAoi] = useState(null)

  const { data: apiAreas } = useQuery({
    queryKey: ['areas'],
    queryFn: getAreas,
    staleTime: Infinity,
    retry: 1,
  })
  const areas = apiAreas?.length ? apiAreas : FALLBACK_AREAS

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
      setYearRange: (s, e) => {
        setStartYear(Math.min(s, e))
        setEndYear(Math.max(s, e))
      },
      aoi,
      setAoi,
      target,
    }
  }, [areas, region, sensor, startYear, endYear, aoi])

  return <FilterContext.Provider value={value}>{children}</FilterContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useFilters() {
  const ctx = useContext(FilterContext)
  if (!ctx) throw new Error('useFilters must be used inside <FilterProvider>')
  return ctx
}
