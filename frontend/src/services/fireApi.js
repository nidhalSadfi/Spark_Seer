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

// ───────────────────────── Near-real-time active fires (map) ─────────────────────────

const HOUR = 3600000
/** "Now" for the mock NRT feed: the last day of the mock record. */
const SNAPSHOT = Date.UTC(END_YEAR, END_MONTH, 31, 14, 0)
const NRT_WINDOW_H = 24 // detections shown on the map
const CHART_BACK_H = 36 // pass history per fire
const FORECAST_H = 12

/** Satellites with their approximate local equator-crossing times (hours). */
export const SATELLITES = [
  { id: 'Terra', sensor: 'modis', day: 10.5, night: 22.5 },
  { id: 'Aqua', sensor: 'modis', day: 13.5, night: 1.5 },
  { id: 'S-NPP', sensor: 'viirs', day: 13.5, night: 1.5 },
  { id: 'NOAA-20', sensor: 'viirs', day: 12.7, night: 0.7 },
  { id: 'NOAA-21', sensor: 'viirs', day: 14.2, night: 2.2 },
]

/** Plausible fire locations per region: [name, place, lat, lon]. Fires are synthetic. */
const FIRE_SITES = {
  mediterranean: [
    ['Sierra de la Culebra', 'Zamora, Spain', 41.95, -6.35], ['Serra da Estrela', 'Guarda, Portugal', 40.33, -7.62],
    ['Las Hurdes', 'Cáceres, Spain', 40.4, -6.25], ['Monchique', 'Faro, Portugal', 37.32, -8.55],
    ['Ourense', 'Galicia, Spain', 42.25, -7.75], ['Attica', 'Greece', 38.1, 23.75], ['Evia', 'Greece', 38.75, 23.6],
    ['Muğla', 'Türkiye', 37.2, 28.35], ['Kroumirie', 'Jendouba, Tunisia', 36.75, 8.7], ['Gallura', 'Sardinia, Italy', 40.9, 9.2],
    ['Madonie', 'Sicily, Italy', 37.85, 14.05], ['Massif des Maures', 'Var, France', 43.3, 6.35],
    ['Peloponnese', 'Greece', 37.4, 22.2], ['Tizi Ouzou', 'Kabylie, Algeria', 36.65, 4.2],
  ],
  amazon: [
    ['Novo Progresso', 'Pará, Brazil', -7.05, -55.4], ['Apuí', 'Amazonas, Brazil', -7.2, -59.9],
    ['Porto Velho', 'Rondônia, Brazil', -8.9, -63.8], ['Lábrea', 'Amazonas, Brazil', -7.25, -64.8],
    ['São Félix do Xingu', 'Pará, Brazil', -6.65, -51.95], ['Altamira', 'Pará, Brazil', -3.6, -52.6],
    ['Colniza', 'Mato Grosso, Brazil', -9.4, -59.0], ['Sinop', 'Mato Grosso, Brazil', -11.85, -55.5],
    ['Riberalta', 'Beni, Bolivia', -11.0, -66.07], ['Itaituba', 'Pará, Brazil', -4.6, -56.3],
    ['Feijó', 'Acre, Brazil', -8.15, -69.9],
  ],
  'central-africa': [
    ['Kasai', 'DR Congo', -5.9, 22.4], ['Lunda Norte', 'Angola', -8.5, 19.5], ['Kolwezi', 'DR Congo', -10.7, 25.5],
    ['Mwinilunga', 'Zambia', -11.7, 24.4], ['Kasama', 'Zambia', -10.2, 31.2], ['Moxico', 'Angola', -12.5, 20.5],
    ['Bandundu', 'DR Congo', -3.3, 17.4], ['Kabinda', 'DR Congo', -6.1, 24.5], ['Mbala', 'Zambia', -8.8, 31.4],
    ['Malanje', 'Angola', -9.5, 16.3], ['Kalemie', 'DR Congo', -5.9, 29.2],
  ],
  'australia-north': [
    ['Kakadu', 'Northern Territory', -12.8, 132.5], ['Arnhem Land', 'Northern Territory', -12.9, 134.5],
    ['Kimberley', 'Western Australia', -16.5, 126.5], ['Katherine', 'Northern Territory', -14.5, 132.3],
    ['Gulf Country', 'Queensland', -17.0, 139.0], ['Daly River', 'Northern Territory', -13.7, 130.7],
    ['Tiwi Islands', 'Northern Territory', -11.6, 130.9], ['Kununurra', 'Western Australia', -15.8, 128.7],
    ['Roper River', 'Northern Territory', -14.7, 134.4],
  ],
  'western-us': [
    ['Sierra Nevada', 'California, USA', 38.6, -120.4], ['Shasta-Trinity', 'California, USA', 40.9, -122.8],
    ['Klamath', 'Oregon, USA', 42.2, -123.5], ['Okanogan', 'Washington, USA', 48.4, -119.6],
    ['Boise NF', 'Idaho, USA', 44.0, -115.7], ['Bitterroot', 'Montana, USA', 46.0, -114.2],
    ['Tonto NF', 'Arizona, USA', 34.0, -111.3], ['Plumas', 'California, USA', 39.9, -121.0],
    ['Deschutes', 'Oregon, USA', 43.9, -121.6], ['Los Padres', 'California, USA', 34.7, -119.6],
  ],
  siberia: [
    ['West Yakutia', 'Sakha, Russia', 62.0, 118.0], ['Evenkia', 'Krasnoyarsk, Russia', 62.5, 98.0],
    ['North Irkutsk', 'Irkutsk, Russia', 57.5, 106.0], ['Zabaykalsky', 'Russia', 52.8, 116.0],
    ['Buryatia', 'Russia', 54.5, 110.5], ['Lena basin', 'Sakha, Russia', 60.5, 122.0],
    ['Angara', 'Krasnoyarsk, Russia', 58.3, 99.5], ['North Amur', 'Amur, Russia', 55.0, 126.0],
  ],
  indonesia: [
    ['Ogan Komering Ilir', 'South Sumatra', -3.4, 105.3], ['Pulang Pisau', 'Central Kalimantan', -2.8, 114.0],
    ['Ketapang', 'West Kalimantan', -1.8, 110.3], ['Jambi peatlands', 'Jambi', -1.2, 103.9],
    ['Pelalawan', 'Riau', 0.3, 102.2], ['Kapuas', 'Central Kalimantan', -2.2, 114.4],
    ['Musi Banyuasin', 'South Sumatra', -2.5, 104.0], ['Kotawaringin', 'Central Kalimantan', -2.5, 111.7],
  ],
}

const LAND_COVER = {
  mediterranean: ['Shrubland · pine forest', 'Mediterranean scrub', 'Pine forest', 'Cork oak woodland'],
  amazon: ['Tropical forest edge', 'Pasture · cleared forest', 'Degraded forest'],
  'central-africa': ['Miombo woodland', 'Savanna grassland', 'Wooded savanna'],
  'australia-north': ['Tropical savanna', 'Spinifex grassland', 'Eucalypt woodland'],
  'western-us': ['Conifer forest', 'Chaparral', 'Sagebrush · grassland'],
  siberia: ['Boreal larch forest', 'Taiga', 'Peat · bog'],
  indonesia: ['Peatland', 'Oil palm on peat', 'Degraded peat forest'],
}

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
const localHour = (t, lon) => ((((t / HOUR + lon / 15) % 24) + 24) % 24)
const diurnal = (t, lon, peak = 15) => Math.cos((2 * Math.PI * (localHour(t, lon) - peak)) / 24)

/** Satellite overpasses over a point between two instants (approximate, ±40 min jitter). */
function overpasses(lat, lon, from, to, rng) {
  const out = []
  const day0 = Math.floor(from / 86400000) * 86400000 - 86400000
  for (let d = day0; d <= to + 86400000; d += 86400000) {
    for (const s of SATELLITES) {
      for (const local of [s.day, s.night]) {
        const t = Math.round(d + (local - lon / 15) * HOUR + (rng() - 0.5) * 80 * 60000)
        if (t >= from && t <= to) out.push({ t, sat: s.id, sensor: s.sensor })
      }
    }
  }
  return out.sort((a, b) => a.t - b.t)
}

const nrtCache = new Map()

/** Active fires, their satellite passes and individual detections for one concrete region. */
function regionActiveFires(regionId) {
  if (nrtCache.has(regionId)) return nrtCache.get(regionId)
  const p = PROFILES[regionId]
  const rng = mulberry32(hash(`nrt:${regionId}`))
  const season = 0.06 + 0.94 * Math.exp(-(monthDist(END_MONTH, p.peak) ** 2) / (2 * p.width ** 2))
  const sites = FIRE_SITES[regionId].filter(() => rng() < 0.35 + 0.65 * season)
  const winStart = SNAPSHOT - NRT_WINDOW_H * HOUR
  const fires = []
  const detections = []

  sites.forEach(([name, place, lat, lon], i) => {
    const first = SNAPSHOT - (6 + rng() * 60) * HOUR
    const peak = (0.4 + season) * (12 + rng() * rng() * 110)
    const frpAt = (t) => {
      const age = (t - first) / HOUR
      const env = peak * (1 - Math.exp(-age / 10))
      return Math.max(1.5, env * (0.4 + 0.6 * (0.5 + 0.5 * diurnal(t, lon, 14.5))) * (0.85 + rng() * 0.3))
    }
    const passes = overpasses(lat, lon, Math.max(first, SNAPSHOT - CHART_BACK_H * HOUR), SNAPSHOT, rng)
      .map((ps) => {
        const frp = frpAt(ps.t)
        const det =
          ps.sensor === 'viirs'
            ? Math.max(1, Math.round((1 + frp / 6) * (0.7 + rng() * 0.6)))
            : Math.round((frp / 22) * (0.7 + rng() * 0.6)) // MODIS (1 km) misses small fires
        return { ...ps, frp: +frp.toFixed(1), det }
      })
      .filter((ps) => ps.det > 0)
    if (!passes.length) return
    const last = passes[passes.length - 1]
    const prev = passes[passes.length - 2]

    const fwi = Math.min(62, Math.max(4, 8 + 40 * season * (0.55 + rng() * 0.6)))
    const coldBias = regionId === 'siberia' ? -9 : 0
    const weather = {
      temp: +(16 + fwi * 0.42 + rng() * 4 + coldBias).toFixed(1),
      rh: Math.round(Math.min(90, Math.max(8, 72 - fwi * 1.15 + rng() * 8))),
      wind: Math.round(8 + fwi * 0.55 + rng() * 10),
      windFrom: COMPASS[Math.floor(rng() * 8)],
      rain72: fwi > 25 ? 0 : rng() < 0.4 ? +(rng() * 6).toFixed(1) : 0,
    }
    const spreadTo = COMPASS[(COMPASS.indexOf(weather.windFrom) + 4) % 8]

    // hourly weather / FWI: observed before SNAPSHOT, forecast after
    const hourly = []
    for (let t = SNAPSHOT - CHART_BACK_H * HOUR; t <= SNAPSHOT + FORECAST_H * HOUR; t += HOUR) {
      const k = diurnal(t, lon) - diurnal(SNAPSHOT, lon)
      hourly.push({
        t,
        temp: +(weather.temp + 6 * k).toFixed(1),
        rh: Math.round(Math.min(98, Math.max(5, weather.rh - 16 * k))),
        wind: Math.max(2, Math.round(weather.wind + 7 * k)),
        fwi: +Math.max(1, fwi + 9 * k).toFixed(1),
        rain: weather.rain72 && t < SNAPSHOT - 30 * HOUR && rng() < 0.3 ? +(rng() * 1.5).toFixed(1) : 0,
      })
    }

    const inWindow = passes.filter((ps) => ps.t >= winStart)
    const fire = {
      id: `${regionId}-${i}`,
      region: regionId,
      name,
      place,
      lat,
      lon,
      frp: last.frp,
      frpDelta: prev ? +(last.frp - prev.frp).toFixed(1) : null,
      lastPass: { t: last.t, sat: last.sat },
      prevPass: prev ? { t: prev.t, sat: prev.sat } : null,
      firstDetected: first,
      detections24h: {
        viirs: inWindow.filter((ps) => ps.sensor === 'viirs').reduce((a, ps) => a + ps.det, 0),
        modis: inWindow.filter((ps) => ps.sensor === 'modis').reduce((a, ps) => a + ps.det, 0),
      },
      persistence: passes.length,
      burnedHa: Math.round(((SNAPSHOT - first) / HOUR) * peak * (0.5 + rng() * 0.6)),
      spread: { to: spreadTo, kmh: +(0.1 + (fwi / 60) * 1.4 * (0.6 + rng() * 0.6)).toFixed(1) },
      landCover: LAND_COVER[regionId][Math.floor(rng() * LAND_COVER[regionId].length)],
      confidence: passes.length >= 3 && last.frp > 15 ? 'high' : 'nominal',
      fwi: +fwi.toFixed(0),
      weather,
      passes,
      hourly,
    }
    fires.push(fire)

    // individual hotspot pixels for each pass in the map window
    const spread = 0.012 + Math.sqrt(last.frp) * 0.006
    for (const ps of inWindow) {
      for (let k = 0; k < ps.det; k++) {
        detections.push({
          lat: +(lat + gauss(rng) * spread).toFixed(4),
          lon: +(lon + gauss(rng) * spread * 1.3).toFixed(4),
          t: ps.t,
          sensor: ps.sensor,
          frp: +(ps.frp / ps.det).toFixed(1),
          fireId: fire.id,
        })
      }
    }
  })

  // small isolated detections (agricultural burns, short-lived fires)
  const all = FIRE_SITES[regionId]
  const singles = Math.round(18 * all.length * season)
  for (let k = 0; k < singles; k++) {
    const [, , lat, lon] = all[Math.floor(rng() * all.length)]
    const viirs = rng() < 0.75
    detections.push({
      lat: +(lat + gauss(rng) * 0.35).toFixed(4),
      lon: +(lon + gauss(rng) * 0.45).toFixed(4),
      t: Math.round(winStart + rng() * NRT_WINDOW_H * HOUR),
      sensor: viirs ? 'viirs' : 'modis',
      frp: +(1 + rng() * 6).toFixed(1),
      fireId: null,
    })
  }

  const result = { fires, detections }
  nrtCache.set(regionId, result)
  return result
}

function gauss(rng) {
  let u = 0
  let v = 0
  while (!u) u = rng()
  while (!v) v = rng()
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
}

/** [[south, west], [north, east]] map bounds for a region. */
export function regionBounds(region) {
  if (region === 'global' || !PROFILES[region]) return [[-45, -140], [70, 160]]
  const { lat, lon } = PROFILES[region]
  return [[lat[0] - 1, lon[0] - 1], [lat[1] + 1, lon[1] + 1]]
}

/**
 * Near-real-time active fires for the map (mock FIRMS-like feed).
 * `fires` are clusters with per-pass history; `detections` are individual hotspot pixels
 * from the last 24 h. With a raw sensor selected, only that sensor's detections are kept
 * (and fires it did not see are dropped — MODIS misses many small fires).
 */
export async function getActiveFires({ region = 'global', sensor = 'harmonized' } = {}) {
  await latency(260)
  const ids = region === 'global' ? Object.keys(PROFILES) : [region]
  let fires = []
  let detections = []
  for (const id of ids) {
    const r = regionActiveFires(id)
    fires = fires.concat(r.fires)
    detections = detections.concat(r.detections)
  }
  if (sensor !== 'harmonized') {
    detections = detections.filter((d) => d.sensor === sensor)
    fires = fires.filter((f) => f.detections24h[sensor] > 0)
  }
  fires = [...fires].sort((a, b) => b.frp - a.frp)
  return {
    fires,
    detections,
    snapshot: SNAPSHOT,
    window: { start: SNAPSHOT - NRT_WINDOW_H * HOUR, end: SNAPSHOT },
    chartRange: { start: SNAPSHOT - CHART_BACK_H * HOUR, end: SNAPSHOT + FORECAST_H * HOUR },
    bounds: regionBounds(region),
    meta: describe(region, sensor),
  }
}

export const DATA_RANGE = {
  startYear: DATA_START_YEAR,
  endYear: END_YEAR,
  endDate: `${END_YEAR}-${String(END_MONTH + 1).padStart(2, '0')}-31`,
}
