/**
 * GET /fires/live → the map's active-fire feed (pure functions, no imports, so a plain node
 * script can check them; see frontend/dev-checks/).
 *
 * The API returns individual NRT hotspot detections. The map, the fire details panel, the pass
 * chart and the reports page all work on "fires", so detections are grouped here:
 *
 *  1. Each detection falls in a grid cell of CELL_DEG degrees (~5 km).
 *  2. Occupied cells that touch (8 neighbours) form one cluster, so hotspots up to roughly
 *     2 × CELL_DEG apart join and a fire front crossing a cell edge is not split.
 *  3. Inside a cluster, detections from the same satellite less than PASS_GAP_MS apart are one
 *     overpass ("pass").
 *
 * Every field of a fire is computed from those detections. Things the satellite feed does not
 * carry (fire weather, FWI, burned area, spread, land cover, place names) are null: the UI shows
 * them as unknown or hides them, never invents them.
 */

export const CELL_DEG = 0.05
export const PASS_GAP_MS = 30 * 60 * 1000

const SNPP = new Set(['N', 'NPP', 'SUOMI NPP', 'S-NPP'])
const NOAA20 = new Set(['N20', 'NOAA-20', 'NOAA20', 'J1', 'JPSS-1', 'JPSS1', '1'])
const NOAA21 = new Set(['N21', 'NOAA-21', 'NOAA21', 'J2', 'JPSS-2', 'JPSS2', '2'])

/** Display name of the satellite (the keys of SAT_COLORS in components/map/mapConfig.js). */
export function satelliteName(source, satellite) {
  const src = String(source ?? '').toUpperCase()
  const sat = String(satellite ?? '').trim().toUpperCase()
  if (src.includes('SNPP') || SNPP.has(sat)) return 'S-NPP'
  if (src.includes('NOAA20') || NOAA20.has(sat)) return 'NOAA-20'
  if (src.includes('NOAA21') || NOAA21.has(sat)) return 'NOAA-21'
  if (sat === 'T' || sat === 'TERRA') return 'Terra'
  if (sat === 'A' || sat === 'AQUA') return 'Aqua'
  return String(satellite ?? '').trim() || String(source ?? '').trim() || 'unknown'
}

/**
 * FIRMS confidence as 'low' | 'nominal' | 'high', or null when absent.
 * VIIRS gives l/n/h; MODIS gives 0-100 (FIRMS classes: <30 low, 30-79 nominal, ≥80 high).
 */
export function confidenceLevel(value) {
  if (value == null || value === '') return null
  const n = typeof value === 'number' ? value : Number(value)
  if (Number.isFinite(n)) return n >= 80 ? 'high' : n < 30 ? 'low' : 'nominal'
  const s = String(value).trim().toLowerCase()
  if (s === 'h' || s === 'high') return 'high'
  if (s === 'n' || s === 'nominal') return 'nominal'
  if (s === 'l' || s === 'low') return 'low'
  return null
}

const finite = (v) => (v == null || v === '' ? null : Number.isFinite(+v) ? +v : null)

/** API detections → the map's detection points. Rows without a position or a time are skipped. */
export function toFeedDetections(rows = []) {
  const out = []
  for (const r of rows) {
    const lat = finite(r.lat)
    const lon = finite(r.lon)
    const t = Date.parse(r.acq_time)
    if (lat == null || lon == null || !Number.isFinite(t)) continue
    const sensor = String(r.sensor ?? '').toLowerCase() === 'modis' ? 'modis' : 'viirs'
    out.push({
      id: r.id != null ? String(r.id) : null,
      lat,
      lon,
      t,
      sensor,
      sat: satelliteName(r.source, r.satellite),
      frp: finite(r.frp),
      confidence: confidenceLevel(r.confidence),
      daynight: r.daynight === 'D' || r.daynight === 'N' ? r.daynight : null,
      fireId: null,
    })
  }
  return out
}

const cellOf = (d, deg) => [Math.floor(d.lat / deg), Math.floor(d.lon / deg)]

/**
 * Group detections into clusters of touching grid cells. Returns
 * [{ key, detections }] where `key` is the smallest cell key of the cluster ("cy_cx"), stable as
 * long as that cell keeps a detection, so a selected fire survives a refresh.
 */
export function clusterDetections(dets, deg = CELL_DEG) {
  const cells = new Map() // "cy_cx" → { cy, cx, dets }
  for (const d of dets) {
    const [cy, cx] = cellOf(d, deg)
    const k = `${cy}_${cx}`
    if (!cells.has(k)) cells.set(k, { cy, cx, dets: [] })
    cells.get(k).dets.push(d)
  }

  // union-find over occupied cells
  const parent = new Map([...cells.keys()].map((k) => [k, k]))
  const find = (k) => {
    while (parent.get(k) !== k) {
      parent.set(k, parent.get(parent.get(k)))
      k = parent.get(k)
    }
    return k
  }
  for (const [k, c] of cells) {
    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        const n = `${c.cy + dy}_${c.cx + dx}`
        if (n !== k && cells.has(n)) {
          const a = find(k)
          const b = find(n)
          if (a !== b) parent.set(a, b)
        }
      }
    }
  }

  const groups = new Map() // root → { keys, dets }
  for (const [k, c] of cells) {
    const r = find(k)
    if (!groups.has(r)) groups.set(r, { keys: [], detections: [] })
    const g = groups.get(r)
    g.keys.push(c)
    g.detections.push(...c.dets)
  }
  return [...groups.values()].map((g) => {
    const min = g.keys.reduce((a, b) => (b.cy < a.cy || (b.cy === a.cy && b.cx < a.cx) ? b : a))
    return { key: `${min.cy}_${min.cx}`, detections: g.detections }
  })
}

const sum = (xs) => xs.reduce((a, b) => a + b, 0)
const round1 = (v) => (v == null ? null : Math.round(v * 10) / 10)

/** Satellite overpasses within one cluster, oldest first: { t, sat, sensor, frp, det, daynight }. */
export function groupPasses(dets) {
  const bySat = new Map()
  for (const d of dets) {
    if (!bySat.has(d.sat)) bySat.set(d.sat, [])
    bySat.get(d.sat).push(d)
  }
  const passes = []
  for (const [sat, list] of bySat) {
    list.sort((a, b) => a.t - b.t)
    let cur = null
    for (const d of list) {
      if (!cur || d.t - cur.last > PASS_GAP_MS) {
        cur = { sat, sensor: d.sensor, first: d.t, last: d.t, dets: [] }
        passes.push(cur)
      }
      cur.last = d.t
      cur.dets.push(d)
    }
  }
  return passes
    .map((p) => {
      const frps = p.dets.map((d) => d.frp).filter((v) => v != null)
      const dn = p.dets.map((d) => d.daynight).find(Boolean) ?? null
      return {
        t: p.first,
        sat: p.sat,
        sensor: p.sensor,
        // total radiative power seen in this overpass; null when no pixel had an FRP value
        frp: frps.length ? round1(sum(frps)) : null,
        det: p.dets.length,
        daynight: dn,
      }
    })
    .sort((a, b) => a.t - b.t)
}

const fmtDeg = (v, pos, neg) => `${Math.abs(v).toFixed(2)}°${v >= 0 ? pos : neg}`

const inBbox = (bbox, lat, lon) => bbox && lon >= bbox[0] && lon <= bbox[2] && lat >= bbox[1] && lat <= bbox[3]

/** One fire from one cluster. `areas` = [{id, name, bbox}] names the study area it lies in. */
export function fireFromCluster({ key, detections }, { region = null, areas = [] } = {}) {
  const lat = sum(detections.map((d) => d.lat)) / detections.length
  const lon = sum(detections.map((d) => d.lon)) / detections.length
  const passes = groupPasses(detections)
  const last = passes.at(-1)
  // compare like with like: MODIS 1 km and VIIRS 375 m FRP sums are not comparable
  const prev = passes.slice(0, -1).findLast((p) => p.sensor === last.sensor) ?? null
  const frps = detections.map((d) => d.frp).filter((v) => v != null)
  const levels = detections.map((d) => d.confidence).filter(Boolean)
  const confidence = levels.includes('high') ? 'high' : levels.includes('nominal') ? 'nominal' : levels.length ? 'low' : null
  const area = areas.find((a) => inBbox(a.bbox, lat, lon))
  const id = `fire-${key}`
  for (const d of detections) d.fireId = id

  return {
    id,
    region,
    name: `Hotspot cluster ${fmtDeg(lat, 'N', 'S')}, ${fmtDeg(lon, 'E', 'W')}`,
    place: area?.name ?? null,
    lat: +lat.toFixed(4),
    lon: +lon.toFixed(4),
    frp: last.frp,
    frpDelta: prev && last.frp != null && prev.frp != null ? round1(last.frp - prev.frp) : null,
    frpMax: frps.length ? round1(Math.max(...frps)) : null,
    frpTotal: frps.length ? round1(sum(frps)) : null,
    lastPass: { t: last.t, sat: last.sat, daynight: last.daynight },
    prevPass: prev ? { t: prev.t, sat: prev.sat } : null,
    firstDetected: Math.min(...detections.map((d) => d.t)),
    lastDetected: Math.max(...detections.map((d) => d.t)),
    detections24h: {
      viirs: detections.filter((d) => d.sensor === 'viirs').length,
      modis: detections.filter((d) => d.sensor === 'modis').length,
    },
    count: detections.length,
    persistence: passes.length,
    confidence,
    satellites: [...new Set(passes.map((p) => p.sat))],
    passes,
    // not in the satellite feed: unknown, never made up
    fwi: null,
    weather: null,
    hourly: null,
    burnedHa: null,
    spread: null,
    landCover: null,
  }
}

/** [[south, west], [north, east]] Leaflet bounds for [minLon, minLat, maxLon, maxLat]. */
export const bboxBounds = (b) => [
  [b[1], b[0]],
  [b[3], b[2]],
]

/**
 * The whole GET /fires/live response → the map feed:
 *   { fires, detections, snapshot, window, chartRange, bounds, meta }
 * Times are epoch ms. With a raw sensor ('modis' | 'viirs') only its detections are clustered.
 */
export function liveFeed(res, { sensor = 'harmonized', bbox, region = null, areas = [] } = {}) {
  let detections = toFeedDetections(res?.detections)
  if (sensor === 'modis' || sensor === 'viirs') detections = detections.filter((d) => d.sensor === sensor)

  const fires = clusterDetections(detections)
    .map((c) => fireFromCluster(c, { region, areas }))
    .sort((a, b) => (b.frp ?? -1) - (a.frp ?? -1) || b.count - a.count)

  const latest = detections.length ? Math.max(...detections.map((d) => d.t)) : null
  const end = Date.parse(res?.window?.end) || latest || Date.parse(res?.snapshot) || Date.now()
  const hours = finite(res?.meta?.hours) ?? 24
  const start = Date.parse(res?.window?.start) || end - hours * 3600000
  const snapshot = Date.parse(res?.snapshot) || end
  const box = bbox ?? res?.meta?.bbox ?? null

  return {
    fires,
    detections,
    snapshot,
    window: { start, end },
    chartRange: { start, end }, // no forecast in the satellite feed
    bounds: box ? bboxBounds(box) : null,
    meta: {
      source: 'api',
      region,
      sensor,
      illustrative: false,
      harmonizationVersion: null,
      product: res?.meta?.product ?? 'NRT',
      hours,
      bbox: box,
      count: res?.meta?.count ?? detections.length,
      sources: (res?.sources ?? []).map((s) => ({ source: s.source, maxAcqDate: s.max_acq_date ?? null })),
    },
  }
}
