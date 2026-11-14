export const SENSORS = [
  { id: 'harmonized', label: 'Harmonized', description: 'Harmonized MODIS + VIIRS estimate' },
  { id: 'modis', label: 'MODIS', description: 'Raw MODIS observations (2000–present)' },
  { id: 'viirs', label: 'VIIRS', description: 'Raw VIIRS observations (2012–present)' },
]

export const sensorLabel = (id) => SENSORS.find((s) => s.id === id)?.label ?? id

/**
 * Placeholder region list. The map teammate will replace this with a real AOI
 * (polygon / rectangle) pushed into FilterContext via `setAoi`.
 */
export const REGIONS = [
  { id: 'global', name: 'Global' },
  { id: 'amazon', name: 'Amazon Basin' },
  { id: 'central-africa', name: 'Central Africa' },
  { id: 'australia-north', name: 'Northern Australia' },
  { id: 'western-us', name: 'Western United States' },
  { id: 'siberia', name: 'Siberia' },
  { id: 'indonesia', name: 'Indonesia' },
  { id: 'mediterranean', name: 'Mediterranean Basin' },
]

export const regionName = (id) => REGIONS.find((r) => r.id === id)?.name ?? id

export const CONFIDENCE_LEVELS = [
  { id: 'all', label: 'All confidence' },
  { id: 'high', label: 'High' },
  { id: 'nominal', label: 'Nominal' },
  { id: 'low', label: 'Low' },
]

export const DATA_START_YEAR = 2001
export const VIIRS_START_YEAR = 2012
