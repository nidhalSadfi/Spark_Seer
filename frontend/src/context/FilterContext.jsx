import { createContext, useContext, useMemo, useState } from 'react'
import { DATA_RANGE } from '../services/fireApi'

/**
 * Global filter state, preserved across pages (region/AOI, sensor, year range).
 *
 * MAP INTEGRATION: the map module should call `setAoi(geojsonFeature | null)`
 * when the user draws or clears an AOI. `region` is the placeholder selector
 * used until the map exists; `aoi` is carried along for API calls.
 */
const FilterContext = createContext(null)

export function FilterProvider({ children }) {
  const [region, setRegion] = useState('global')
  const [sensor, setSensor] = useState('harmonized')
  const [startYear, setStartYear] = useState(DATA_RANGE.startYear)
  const [endYear, setEndYear] = useState(DATA_RANGE.endYear)
  const [aoi, setAoi] = useState(null)

  const value = useMemo(
    () => ({
      region,
      setRegion,
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
    }),
    [region, sensor, startYear, endYear, aoi],
  )
  return <FilterContext.Provider value={value}>{children}</FilterContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useFilters() {
  const ctx = useContext(FilterContext)
  if (!ctx) throw new Error('useFilters must be used inside <FilterProvider>')
  return ctx
}
