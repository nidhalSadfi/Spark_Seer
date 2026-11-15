// Dev check (not shipped, not a test runner): the Social Alerts mapping (utils/incidents.js) over sample
// tickets shaped like GET /api/tickets of the incident agents (incident_agents/store.py,
// tickets_with_sources). SYNTHETIC values: places, posts and links are invented.
// Run from frontend/:  node dev-checks/incidents.check.mjs
import assert from 'node:assert/strict'
import {
  AGENT_KEYS,
  diagnoseAgents,
  isApproximate,
  leadWords,
  matchLine,
  observedFor,
  reportNote,
  reportOf,
  reportValues,
  safeUrl,
  statusInfo,
  ticketFromApi,
  ticketsFromApi,
} from '../src/utils/incidents.js'

let n = 0
const check = (name, fn) => {
  fn()
  n++
  console.log(`ok - ${name}`)
}

const source = (over = {}) => ({
  source: 'x',
  kind: 'post',
  url: 'https://x.example/post/1',
  author: 'someone',
  published_at: '2026-08-27T10:00:00Z',
  fetched_at: '2026-08-27T10:30:00Z',
  excerpt: 'Fire near the village',
  place_phrase: 'Calenzana',
  ...over,
})
const raw = (over = {}) => ({
  id: 12,
  created_at: '2026-08-27T10:31:00Z',
  updated_at: '2026-08-27T11:00:00Z',
  title: 'Fire reported: Calenzana',
  place: 'Calenzana',
  country: 'FR',
  lat: 42.5,
  lon: 8.85,
  precision: 'city',
  radius_km: 4.2,
  first_seen: '2026-08-27T10:00:00Z',
  last_seen: '2026-08-27T12:00:00Z',
  status: 'Satellite overlap',
  category: 'active_fire_report',
  mode: 'live',
  satellite: {
    checked: ['kanari', 'firms'],
    radius_km: 14.2,
    matches: [{ source: 'kanari', place: 'Calenzana', km: 3.2, first_seen: '2026-08-27T10:47:00Z' }, { source: 'firms', sensor: 'VIIRS' }],
    lead: { social_first: '2026-08-27T10:00:00Z', minutes_vs_kanari: 47, minutes_vs_polar: -120, posts_truncated: false },
  },
  history: { text: 'MODIS saw fire in this 0.5° cell in 9 of 25 years.', cell_id: '21.0_4.0' },
  sources: [source({ url: 'https://x.example/post/2', published_at: '2026-08-27T11:00:00Z' }), source()],
  ...over,
})

check('ticket JSON → UI shape (times in ms, sources kept, history text)', () => {
  const t = ticketFromApi(raw())
  assert.equal(t.id, 12)
  assert.equal(t.hasLocation, true)
  assert.equal(t.radiusKm, 4.2)
  assert.equal(t.firstSeen, Date.parse('2026-08-27T10:00:00Z'))
  assert.equal(t.replay, false)
  assert.equal(t.sources.length, 2)
  assert.equal(t.sources[0].publishedAt, Date.parse('2026-08-27T11:00:00Z'))
  assert.deepEqual(t.history, { text: 'MODIS saw fire in this 0.5° cell in 9 of 25 years.', cellId: '21.0_4.0' })
  assert.equal(t.satellite.matches.length, 2)
  assert.equal(t.satellite.lead.minutes_vs_kanari, 47)
})

check('tickets without an id are dropped; a non-array payload is an error', () => {
  assert.equal(ticketFromApi({ title: 'no id' }), null)
  assert.equal(ticketFromApi(null), null)
  assert.deepEqual(ticketsFromApi([raw(), { title: 'no id' }, 'junk']).map((t) => t.id), [12])
  assert.throws(() => ticketsFromApi({ detail: 'oops' }), /cannot read/)
})

check('a ticket without coordinates is kept but cannot be reported', () => {
  const t = ticketFromApi(raw({ lat: null, lon: null }))
  assert.equal(t.hasLocation, false)
  assert.throws(() => reportValues(t), /no coordinates/)
  assert.equal(reportOf(t, [{ note: 'Social #12: x', lat: 0, lon: 0 }]), null)
  assert.equal(ticketFromApi(raw({ lat: 95, lon: 8 })).hasLocation, false) // out of range
})

check('only http(s) links are kept (no javascript: or data: URLs from untrusted posts)', () => {
  assert.equal(safeUrl('https://bsky.app/profile/a/post/b'), 'https://bsky.app/profile/a/post/b')
  assert.equal(safeUrl('javascript:alert(1)'), null)
  assert.equal(safeUrl('data:text/html,<script>1</script>'), null)
  assert.equal(safeUrl('not a url'), null)
  assert.equal(safeUrl(undefined), null)
  const t = ticketFromApi(raw({ sources: [source({ url: 'javascript:alert(1)' })] }))
  assert.equal(t.sources[0].url, null)
})

check('category → what the community report says was seen', () => {
  assert.equal(observedFor('active_fire_report'), 'flames')
  assert.equal(observedFor('smoke_or_possible_fire'), 'smoke')
  for (const c of ['news_about_fire', 'satellite', '', undefined]) assert.equal(observedFor(c), 'unsure')
})

check('report note: prefix first, counts, status, earliest link; never longer than the limit', () => {
  const t = ticketFromApi(raw())
  assert.equal(
    reportNote(t),
    'Social #12: Fire reported: Calenzana · ±4 km · 2 public mentions · satellite overlap · https://x.example/post/1',
  )
  const long = ticketFromApi(raw({ title: 'T'.repeat(400) }))
  const note = reportNote(long)
  assert.ok(note.length <= 280)
  assert.ok(note.startsWith('Social #12: '))
  assert.equal(reportNote(ticketFromApi(raw({ status: 'Mentioned', radius_km: null, sources: [source()] }))), 'Social #12: Fire reported: Calenzana · 1 public mention · https://x.example/post/1')
})

check('report values carry the ticket position and the observed kind', () => {
  assert.deepEqual(Object.keys(reportValues(ticketFromApi(raw()))).sort(), ['lat', 'lon', 'note', 'observed'])
  const v = reportValues(ticketFromApi(raw({ category: 'smoke_or_possible_fire' })))
  assert.equal(v.observed, 'smoke')
  assert.equal(v.lat, 42.5)
})

check('"already reported" matches on prefix and place, not on the ticket number alone', () => {
  const t = ticketFromApi(raw())
  const mine = { id: 'a', lat: 42.5, lon: 8.85, note: reportNote(t), status: 'pending' }
  assert.equal(reportOf(t, [mine]), mine)
  assert.equal(reportOf(t, [{ ...mine, lat: 36.9 }]), null) // another ticket 12 from another database, elsewhere
  assert.equal(reportOf(t, [{ ...mine, note: 'Social #13: other' }]), null)
  assert.equal(reportOf(t, [{ ...mine, note: 'Social #120: other' }]), null) // #12 is not a prefix of #120:
  assert.equal(reportOf(t, [{ ...mine, note: null }]), null)
  assert.equal(reportOf(t, []), null)
})

check('lead wording: positive = people posted first', () => {
  assert.equal(leadWords(47), 'posts 47 min before')
  assert.equal(leadWords(-120), 'satellite 2.0 h before')
  assert.equal(leadWords(-26), 'satellite 26 min before')
  assert.equal(leadWords(0), 'same time')
  assert.equal(leadWords(null), null)
})

check('satellite match line and approximate-location flag', () => {
  assert.equal(matchLine({ source: 'kanari', place: 'Calenzana', km: 3.2 }), 'kanari Calenzana · 3.2 km')
  assert.equal(matchLine({ source: 'firms', sensor: 'VIIRS' }), 'firms VIIRS')
  assert.equal(isApproximate(ticketFromApi(raw())), false)
  assert.equal(isApproximate(ticketFromApi(raw({ precision: 'region', radius_km: 60 }))), true)
  assert.equal(isApproximate(ticketFromApi(raw({ precision: 'city', radius_km: 40 }))), true)
  assert.equal(statusInfo('Satellite overlap').tone, 'ok')
  assert.equal(statusInfo('something new').tone, 'neutral')
})

check('why the agents do not answer, in plain words: no address, port, command or setting on the page', () => {
  const ok = { autostart: true, running: false, gave_up: false, last_error: 'the agents exited with code 3', url: 'http://127.0.0.1:8765' }
  const cases = {
    'backend-down': { error: Object.assign(new Error('no response'), { status: 0 }) },
    'not-started': { error: Object.assign(new Error('Not Found'), { status: 404 }) }, // a backend that does not start them
    'turned-off': { status: { ...ok, autostart: false } },
    'gave-up': { status: { ...ok, gave_up: true } },
    starting: { status: ok },
    unreachable: { status: { ...ok, running: true } },
  }
  for (const [kind, input] of Object.entries(cases)) {
    const d = diagnoseAgents(input)
    assert.equal(d.kind, kind)
    assert.ok(d.title && d.body, kind)
    const text = `${d.title} ${d.body}`
    assert.doesNotMatch(text, /localhost|127\.0\.0\.1|:\d{2,5}\b|\bport\b|VITE_|INCIDENT_|\.env|uvicorn|python|cd |CORS|exited with code/i, kind)
    assert.deepEqual(Object.keys(d).sort(), ['body', 'kind', 'title'], kind)
  }
  assert.equal(diagnoseAgents({}).kind, 'not-started')
  assert.equal(diagnoseAgents({ error: Object.assign(new Error('x'), { status: 500 }) }).kind, 'backend-down')
  assert.deepEqual(AGENT_KEYS.filter((k) => k.required).map((k) => k.id), ['groq'])
})

console.log(`\n${n} checks passed`)
