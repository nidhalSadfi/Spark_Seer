// Dev check (not shipped, not a test runner): the clustering of GET /fires/live and the
// API → UI mapping of /reports, over sample JSON written from docs/processing/ARCHITECTURE.md §10.
// Run from frontend/:  node dev-checks/live-data.check.mjs
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { clusterDetections, confidenceLevel, groupPasses, liveFeed, satelliteName, toFeedDetections } from '../src/utils/fireClusters.js'
import { createBody, reportFromApi, reportsFromApi } from '../src/utils/reportMapping.js'

const sample = JSON.parse(readFileSync(new URL('./fires-live.sample.json', import.meta.url)))
const areas = [{ id: 'bizerte', name: 'Bizerte / NW Tunisia', bbox: [8.3, 36.0, 9.8, 37.2] }]
const ms = (iso) => Date.parse(iso)
let n = 0
const check = (name, fn) => {
  fn()
  n++
  console.log(`ok - ${name}`)
}

check('satellite names and confidence levels', () => {
  assert.equal(satelliteName('VIIRS_SNPP_NRT', 'N'), 'S-NPP')
  assert.equal(satelliteName('VIIRS_NOAA20_NRT', 'N20'), 'NOAA-20')
  assert.equal(satelliteName('VIIRS_NOAA21_NRT', 'N21'), 'NOAA-21')
  assert.equal(satelliteName('MODIS_NRT', 'T'), 'Terra')
  assert.equal(satelliteName('MODIS_NRT', 'Aqua'), 'Aqua')
  assert.equal(confidenceLevel('h'), 'high')
  assert.equal(confidenceLevel('l'), 'low')
  assert.equal(confidenceLevel(85), 'high')
  assert.equal(confidenceLevel('40'), 'nominal')
  assert.equal(confidenceLevel(20), 'low')
  assert.equal(confidenceLevel(null), null)
})

check('invalid detections are skipped, null FRP kept as null', () => {
  const dets = toFeedDetections(sample.detections)
  assert.equal(dets.length, 9)
  assert.equal(dets.find((d) => d.id === '4').frp, null)
  assert.equal(dets[0].t, ms('2026-11-15T01:30:00Z'))
})

check('clustering joins neighbouring cells, splits distant points', () => {
  const clusters = clusterDetections(toFeedDetections(sample.detections))
  const sizes = clusters.map((c) => c.detections.map((d) => d.id).sort((a, b) => a - b).join(',')).sort()
  // 1-5 one fire; 6+7 straddle the 37.00° cell edge and join; 8 and 9 are 0.2° apart and stay apart
  assert.deepEqual(sizes, ['1,2,3,4,5', '6,7', '8', '9'])
})

check('passes group one satellite overpass and sum its FRP', () => {
  const dets = toFeedDetections(sample.detections).filter((d) => +d.id <= 5)
  const passes = groupPasses(dets)
  assert.deepEqual(
    passes.map((p) => [p.sat, p.det, p.frp]),
    [
      ['S-NPP', 1, 4],
      ['Aqua', 1, 22],
      ['NOAA-20', 3, 18], // 10.5 + 7.5, the null-FRP pixel is counted but not summed
    ],
  )
})

check('feed shape and fire fields derived from detections', () => {
  const feed = liveFeed(sample, { bbox: [8.3, 36.0, 9.8, 37.2], region: 'bizerte', areas })
  assert.deepEqual(Object.keys(feed).sort(), ['bounds', 'chartRange', 'detections', 'fires', 'meta', 'snapshot', 'window'])
  assert.equal(feed.meta.source, 'api')
  assert.deepEqual(feed.window, { start: ms('2026-11-14T12:01:00Z'), end: ms('2026-11-15T12:01:00Z') })
  assert.deepEqual(feed.bounds, [[36.0, 8.3], [37.2, 9.8]])
  assert.equal(feed.fires.length, 4)

  const f = feed.fires[0] // highest last-pass FRP first
  assert.equal(f.frp, 18) // NOAA-20 pass at 12:00, the latest
  assert.equal(f.frpDelta, 14) // vs the previous VIIRS pass (S-NPP, 4 MW), not the MODIS one
  assert.deepEqual(f.prevPass, { t: ms('2026-11-15T01:30:00Z'), sat: 'S-NPP' })
  assert.deepEqual(f.lastPass, { t: ms('2026-11-15T12:00:00Z'), sat: 'NOAA-20', daynight: 'D' })
  assert.deepEqual(f.detections24h, { viirs: 4, modis: 1 })
  assert.equal(f.persistence, 3)
  assert.equal(f.firstDetected, ms('2026-11-15T01:30:00Z'))
  assert.equal(f.frpMax, 22)
  assert.equal(f.frpTotal, 44)
  assert.equal(f.confidence, 'high')
  assert.equal(f.place, 'Bizerte / NW Tunisia')
  assert.ok(Math.abs(f.lat - 36.952) < 1e-3 && Math.abs(f.lon - 9.1028) < 1e-3)
  for (const k of ['fwi', 'weather', 'hourly', 'burnedHa', 'spread', 'landCover']) assert.equal(f[k], null, k)
  assert.ok(feed.detections.filter((d) => d.fireId === f.id).length === 5)

  const outside = feed.fires.find((x) => x.lon < 5)
  assert.equal(outside.place, null) // Kabylie is not in the areas passed in
  assert.equal(outside.confidence === 'low' || outside.confidence === 'nominal', true)
})

check('fire ids are stable across refetches and input order', () => {
  const a = liveFeed(sample, {}).fires.map((f) => f.id).sort()
  const b = liveFeed({ ...sample, detections: [...sample.detections].reverse() }, {}).fires.map((f) => f.id).sort()
  assert.deepEqual(a, b)
})

check('raw sensor filter keeps only that sensor', () => {
  const modis = liveFeed(sample, { sensor: 'modis' })
  assert.ok(modis.detections.every((d) => d.sensor === 'modis'))
  assert.deepEqual(modis.fires.map((f) => f.detections24h.viirs), [0, 0, 0])
})

check('empty response gives an empty feed', () => {
  const feed = liveFeed({ ...sample, detections: [] }, { bbox: [-2.5, 34, 11.5, 38] })
  assert.equal(feed.fires.length, 0)
  assert.equal(feed.window.end, ms('2026-11-15T12:01:00Z'))
})

check('report API → UI mapping', () => {
  const api = {
    id: 'a1b2', lat: 36.9, lon: 9.1, observed: 'smoke', note: 'smoke over the ridge', status: 'confirmed',
    createdAt: '2026-11-15T08:00:00Z', reviewedAt: '2026-11-15T08:30:00+00:00',
  }
  assert.deepEqual(reportFromApi(api), { ...api, createdAt: ms(api.createdAt), reviewedAt: ms('2026-11-15T08:30:00Z') })
  assert.equal(reportFromApi({ ...api, status: 'pending' }).reviewedAt, null)
  assert.equal(reportFromApi({ ...api, id: 42 }).id, '42')
  assert.equal(reportFromApi({ ...api, observed: 'aliens' }).observed, 'unsure')
  assert.equal(reportFromApi({ ...api, note: null }).note, '')
  assert.equal(reportFromApi({ ...api, createdAt: 'yesterday' }), null)
  assert.equal(reportFromApi({ ...api, status: 'maybe' }), null)
  assert.equal(reportFromApi({ ...api, lat: 95 }), null)
  const list = reportsFromApi({ reports: [api, { ...api, id: 'new', createdAt: '2026-11-15T09:00:00Z' }, { bad: true }] })
  assert.deepEqual(list.map((r) => r.id), ['new', 'a1b2'])
  assert.deepEqual(createBody({ lat: 1, lon: 2, observed: 'both', note: `  ${'x'.repeat(300)}  ` }).note.length, 280)
})

console.log(`\n${n} checks passed`)
