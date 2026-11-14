export const SENSORS = [
  { id: 'harmonized', label: 'Harmonized', description: 'Harmonized MODIS + VIIRS estimate (VIIRS-equivalent detections)' },
  { id: 'modis', label: 'MODIS', description: 'Raw MODIS observations (2001–present)' },
  { id: 'viirs', label: 'VIIRS', description: 'Raw VIIRS 375 m observations (2012–present)' },
]

export const sensorLabel = (id) => SENSORS.find((s) => s.id === id)?.label ?? id

/**
 * Demo areas, mirroring GET /areas (data/areas.json in the backend). Used only until /areas
 * answers, or when the backend is unreachable, so the region selector is never empty.
 * bbox = [minLon, minLat, maxLon, maxLat].
 */
export const FALLBACK_AREAS = [
  { id: 'bizerte', name: 'Bizerte', bbox: [9.0, 36.8, 10.3, 37.4] },
  { id: 'nw_tunisia', name: 'Northwest Tunisia', bbox: [8.3, 36.2, 9.2, 37.0] },
  { id: 'kabylie', name: 'Kabylie', bbox: [3.7, 36.3, 5.2, 37.0] },
  { id: 'ne_algeria', name: 'Northeast Algeria', bbox: [7.0, 36.4, 8.6, 37.0] },
]

/** Calendar flags returned by GET /calendar. */
export const FLAGS = {
  high: { label: 'Unusually active', short: 'High', tone: 'fire' },
  low: { label: 'Unusually quiet', short: 'Low', tone: 'info' },
  low_confidence: { label: 'Low confidence', short: 'Low conf.', tone: 'neutral' },
  normal: { label: 'Within the usual range', short: 'Normal', tone: 'ok' },
}

export const DATA_START_YEAR = 2001
export const DATA_END_YEAR = 2025
export const VIIRS_START_YEAR = 2012
