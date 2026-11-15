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
  { id: 'bizerte', name: 'Bizerte / NW Tunisia', bbox: [8.3, 36.0, 9.8, 37.2], group: 'north_africa' },
  { id: 'kabylie', name: 'Kabylie', bbox: [3.7, 36.3, 5.2, 37.0], group: 'north_africa' },
  { id: 'ne_algeria', name: 'NE Algeria', bbox: [7.0, 36.4, 8.6, 37.0], group: 'north_africa' },
  { id: 'petite_kabylie', name: 'Petite Kabylie', bbox: [5.3, 36.4, 6.9, 37.1], group: 'north_africa' },
]

/** The "All areas" choice of the Area menu: everything the data covers, as one whole-world box. */
export const ALL_AREAS = 'all'
export const WORLD_BBOX = [-180, -90, 180, 90]

/** Headings for the `group` field of GET /areas in the Area menu. */
export const AREA_GROUPS = { north_africa: 'Tunisia and Algeria', world: 'World' }

/** Outline of a "partial" calendar month (near-real-time, still being observed); yellow-300. */
export const PARTIAL_BORDER = '#fde047'

/** Calendar flags returned by GET /calendar. */
export const FLAGS = {
  high: { label: 'Unusually active', short: 'High', tone: 'fire' },
  low: { label: 'Unusually quiet', short: 'Low', tone: 'info' },
  low_confidence: { label: 'Low confidence', short: 'Low conf.', tone: 'neutral' },
  normal: { label: 'Within the usual range', short: 'Normal', tone: 'ok' },
}

export const DATA_START_YEAR = 2001
/**
 * Last year of the offline mock, and the fallback while GET /health has not given `data_end_year`
 * (FilterContext `dataEndYear`). With the API, the dashboard ends at the API's last year with data.
 */
export const DATA_END_YEAR = 2025
export const VIIRS_START_YEAR = 2012

/** The whole FIRMS polling region [minLon, minLat, maxLon, maxLat] (Tunisia + northern Algeria). */
export const FIRMS_BBOX = [-2.5, 34, 11.5, 38]

/** Hours of near-real-time detections the fire map shows (its time slider and "24 h" counts). */
export const LIVE_WINDOW_H = 24
