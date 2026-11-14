/**
 * Spark Seer data service.
 *
 * ILLUSTRATIVE MOCK DATA. Every function here is async and returns plain
 * JSON-shaped data, so swapping in the real backend only means replacing the
 * bodies of these functions. All figures are generated from a seeded PRNG and
 * are NOT real MODIS/VIIRS observations.
 */
import { DATA_START_YEAR, REGIONS, VIIRS_START_YEAR } from './constants'
import { anomalyPct, historicalMedian, latestObservation } from '../utils/stats'

const END_YEAR = 2026
const END_MONTH = 9 // data runs through Oct 2026 (0-indexed)
const VIIRS_FACTOR = 2.4 // VIIRS (375 m) detects more, smaller fires than MODIS (1 km)
const HARMONIZATION_VERSION = 'placeholder-v0'

const latency = (ms = 220) => new Promise((r) => setTimeout(r, ms))

function mulberry32(seed) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function hash(str) {
  let h = 2166136261
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

// scale = typical monthly MODIS count at peak, peak = peak month (0-indexed), width = season spread
const PROFILES = {
  amazon: { scale: 5200, peak: 8, width: 1.4, lat: [-15, 2], lon: [-70, -50] },
  'central-africa': { scale: 8200, peak: 6, width: 1.8, lat: [-14, 2], lon: [14, 32] },
  'australia-north': { scale: 2600, peak: 9, width: 1.6, lat: [-18, -11], lon: [125, 140] },
  'western-us': { scale: 900, peak: 7, width: 1.2, lat: [34, 48], lon: [-123, -110] },
  siberia: { scale: 1500, peak: 6, width: 1.3, lat: [52, 64], lon: [90, 130] },
  indonesia: { scale: 1900, peak: 9, width: 1.5, lat: [-8, 2], lon: [100, 118] },
  mediterranean: { scale: 650, peak: 7, width: 1.2, lat: [36, 44], lon: [-8, 30] },
}

const monthDist = (a, b) => {
  const d = Math.abs(a - b)
  return Math.min(d, 12 - d)
}

const matrixCache = new Map()

/** Raw MODIS-style monthly counts for one concrete region. */
function baseRegionMatrix(regionId) {
  const p = PROFILES[regionId]
  const rng = mulberry32(hash(`region:${regionId}`))
  const years = []
  const data = []
  for (let y = DATA_START_YEAR; y <= END_YEAR; y++) {
    years.push(y)
    // year-level multiplier: mild decline trend, random swing, and a few extreme years
    const trend = 1 - 0.012 * (y - DATA_START_YEAR)
    const swing = Math.exp((rng() - 0.5) * 0.7)
    const extreme = rng() < 0.12 ? 1.5 + rng() * 0.6 : 1
    const yearFactor = trend * swing * extreme
    const row = []
    for (let m = 0; m < 12; m++) {
      if (y === END_YEAR && m > END_MONTH) {
        row.push(null)
        continue
      }
      const season = 0.06 + 0.94 * Math.exp(-(monthDist(m, p.peak) ** 2) / (2 * p.width ** 2))
      const noise = 0.85 + rng() * 0.3
      row.push(Math.max(0, Math.round(p.scale * season * yearFactor * noise)))
    }
    data.push(row)
  }
  return { years, data }
}

function sensorMatrix(regionId, sensor) {
  const key = `${regionId}:${sensor}`
  if (matrixCache.has(key)) return matrixCache.get(key)

  let result
  if (regionId === 'global') {
    // Global = sum of the modelled regions, plus ~35% for everywhere else.
    const parts = Object.keys(PROFILES).map((id) => sensorMatrix(id, sensor))
    const years = parts[0].years
    const data = years.map((_, yi) =>
      Array.from({ length: 12 }, (_, m) => {
        const vals = parts.map((p) => p.data[yi][m])
        if (vals.every((v) => v == null)) return null
        return Math.round(vals.reduce((a, b) => a + (b ?? 0), 0) * 1.35)
      }),
    )
    result = { years, data }
  } else {
    const modis = baseRegionMatrix(regionId)
    const years = modis.years
    const data = years.map((y, yi) =>
      modis.data[yi].map((v) => {
        if (v == null) return null
        const viirs = y >= VIIRS_START_YEAR ? Math.round(v * VIIRS_FACTOR) : null
        if (sensor === 'modis') return v
        if (sensor === 'viirs') return viirs
        // Placeholder harmonization: rescale MODIS to a VIIRS-equivalent, average where both exist.
        const rescaled = v * 2.1
        return Math.round(viirs == null ? rescaled : (rescaled + viirs) / 2)
      }),
    )
    result = { years, data }
  }
  matrixCache.set(key, result)
  return result
}

function describe(regionId, sensor) {
  return {
    region: regionId,
    sensor,
    harmonizationVersion: sensor === 'harmonized' ? HARMONIZATION_VERSION : null,
    illustrative: true,
  }
}

/** Year × month matrix of hotspot counts. data[yearIdx][monthIdx], null = no data. */
export async function getMonthlyMatrix({ region = 'global', sensor = 'harmonized' } = {}) {
  await latency()
  const m = sensorMatrix(region, sensor)
  return { ...m, meta: describe(region, sensor) }
}

/** Yearly totals for MODIS, VIIRS and harmonized, for the raw-vs-harmonized comparison. */
export async function getSensorComparison({ region = 'global' } = {}) {
  await latency()
  const ids = ['modis', 'viirs', 'harmonized']
  const mats = Object.fromEntries(ids.map((s) => [s, sensorMatrix(region, s)]))
  const years = mats.modis.years
  const totals = Object.fromEntries(
    ids.map((s) => [
      s,
      years.map((_, yi) => {
        const row = mats[s].data[yi]
        return row.every((v) => v == null) ? null : row.reduce((a, b) => a + (b ?? 0), 0)
      }),
    ]),
  )
  return { years, totals, meta: describe(region, 'harmonized') }
}

/** Latest-month snapshot for every concrete region (used for rankings and alerts). */
export async function getRegionSummaries({ sensor = 'harmonized' } = {}) {
  await latency()
  return REGIONS.filter((r) => r.id !== 'global').map((r) => {
    const mat = sensorMatrix(r.id, sensor)
    const last = latestObservation(mat)
    const current = mat.data[last.yearIdx][last.month]
    const baseline = historicalMedian(mat, last.year, last.month)
    return {
      id: r.id,
      name: r.name,
      year: last.year,
      month: last.month,
      current,
      baseline,
      anomaly: anomalyPct(current, baseline),
    }
  })
}

const CONF = ['low', 'nominal', 'high']

/** Individual (mock) hotspot detections for the Data Explorer. */
export async function getHotspotRecords({
  region = 'global',
  sensor = 'harmonized',
  confidence = 'all',
  from,
  to,
} = {}) {
  await latency(300)
  const fromT = new Date(from).getTime()
  const toT = new Date(to).getTime()
  if (!Number.isFinite(fromT) || !Number.isFinite(toT) || toT < fromT) return []

  const rng = mulberry32(hash(`rec:${region}:${sensor}:${from}:${to}`))
  const profiles = region === 'global' ? Object.values(PROFILES) : [PROFILES[region]]
  const span = Math.max(1, (toT - fromT) / 86400000)
  const n = Math.min(600, Math.round(120 + span * 2))
  const rows = []
  for (let i = 0; i < n; i++) {
    const p = profiles[Math.floor(rng() * profiles.length)]
    const date = new Date(fromT + rng() * (toT - fromT))
    const src =
      sensor === 'harmonized' ? (rng() < 0.55 && date.getUTCFullYear() >= VIIRS_START_YEAR ? 'viirs' : 'modis') : sensor
    if (src === 'viirs' && date.getUTCFullYear() < VIIRS_START_YEAR) continue
    const conf = CONF[Math.floor(rng() * rng() * 3 * 1.0 + (rng() < 0.4 ? 1 : 0)) % 3]
    if (confidence !== 'all' && conf !== confidence) continue
    rows.push({
      id: `H${String(100000 + i)}`,
      date: date.toISOString().slice(0, 10),
      lat: +(p.lat[0] + rng() * (p.lat[1] - p.lat[0])).toFixed(4),
      lon: +(p.lon[0] + rng() * (p.lon[1] - p.lon[0])).toFixed(4),
      sensor: src.toUpperCase(),
      confidence: conf,
      frp: +(2 + rng() * rng() * 180).toFixed(1),
      brightness: +(300 + rng() * 60).toFixed(1),
    })
  }
  rows.sort((a, b) => (a.date < b.date ? 1 : -1))
  return rows
}

export const DATA_RANGE = {
  startYear: DATA_START_YEAR,
  endYear: END_YEAR,
  endDate: `${END_YEAR}-${String(END_MONTH + 1).padStart(2, '0')}-31`,
}
