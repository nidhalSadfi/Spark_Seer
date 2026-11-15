// Dev check (not shipped, not a test runner): the agent graph's event reducer (utils/agentFlow.js) over a
// sample run shaped like the incident agents' step events (incident_agents/pipeline.py, planner.py:
// `report(step, status, message, source, data)`). SYNTHETIC: places, counts and messages are invented.
// Run from frontend/:  node dev-checks/agent-flow.check.mjs
import assert from 'node:assert/strict'
import {
  FLOW_LINKS,
  FLOW_NODES,
  NODE_IDS,
  eventsFor,
  flowFromEvents,
  friendlyRunError,
  latestRunId,
  linkState,
  nodeForEvent,
  nodeStatus,
  runIsActive,
  stepTag,
} from '../src/utils/agentFlow.js'

let n = 0
const check = (name, fn) => {
  fn()
  n++
  console.log(`ok - ${name}`)
}

let nextId = 1
const T0 = Date.parse('2026-11-15T10:00:00Z')
const ev = (run_id, step, status, message = '', source = null, data = null, sec = nextId) => ({
  id: nextId++,
  run_id,
  ts: new Date(T0 + sec * 1000).toISOString(),
  step,
  source,
  status,
  message,
  data,
})

/** A full live run (#1): Bluesky skipped (no key), X failed, everything else ran. */
const run1 = (end = 'done') => [
  ev(1, 'run', 'started', 'query: France | last 24 h'),
  ev(1, 'plan', 'running', 'asking the model for a search plan (attempt 1)'),
  ev(1, 'plan', 'ok', 'France (FR)'),
  ev(1, 'validate', 'ok', 'zones resolved'),
  ev(1, 'fetch', 'running', 'verified public signals', 'kanari'),
  ev(1, 'fetch', 'ok', '12 posts in the zones (Source: kanari.io)', 'kanari'),
  ev(1, 'fetch', 'skipped', 'no Bluesky handle / app password', 'bluesky'),
  ev(1, 'fetch', 'running', 'search fr', 'x'),
  ev(1, 'fetch', 'error', 'HTTP 429', 'x'),
  ev(1, 'fetch', 'ok', 'FR/fr: 30 headlines', 'gdelt'),
  ev(1, 'filter', 'ok', 'kept 30 of 42 (dropped {})'),
  ev(1, 'judge', 'running', '8 items with openai/gpt-oss-20b', 'gdelt'),
  ev(1, 'judge', 'ok', '5 of 8 look like a current fire report in scope; {}', 'gdelt'),
  ev(1, 'judge', 'running', '8 items with openai/gpt-oss-20b', 'kanari'),
  ev(1, 'judge', 'ok', '3 of 8 look like a current fire report in scope; {}', 'kanari'),
  ev(1, 'locate', 'ok', '6 located, 2 relevant but without a usable place'),
  ev(1, 'ticket', 'created', '#7 Calenzana (FR) from x'),
  ev(1, 'ticket', 'created', '#8 Magescq (FR) from x'),
  ev(1, 'ticket', 'updated', '#7 Calenzana (FR) from gdelt'),
  ev(1, 'satellite', 'ok', '#7: Satellite overlap'),
  ev(1, 'satellite', 'ok', '#8: No satellite support'),
  ev(1, 'history', 'ok', '25-year cell history on 2 of 2 tickets'),
  ev(1, 'lead', 'ok', 'lead times: 1 ticket'),
  ...(end ? [ev(1, 'run', end, end === 'done' ? '2 new and 1 updated tickets' : 'boom')] : []),
]

check('every node has a unique id and a position; every link joins two known nodes', () => {
  assert.equal(new Set(NODE_IDS).size, FLOW_NODES.length)
  for (const nd of FLOW_NODES) assert.ok(Number.isFinite(nd.u) && Number.isFinite(nd.v) && nd.label && nd.role, nd.id)
  for (const [a, b] of FLOW_LINKS) assert.ok(NODE_IDS.includes(a) && NODE_IDS.includes(b), `${a} -> ${b}`)
})

check('events map to nodes: steps, fetch sources, run-level and unknown events', () => {
  assert.equal(nodeForEvent({ step: 'plan' }), 'plan')
  assert.equal(nodeForEvent({ step: 'fetch', source: 'bluesky' }), 'bluesky')
  assert.equal(nodeForEvent({ step: 'judge', source: 'kanari_signal' }), 'judge') // judge is one node for every source
  assert.equal(nodeForEvent({ step: 'fetch', source: 'kanari_satellite' }), null)
  assert.equal(nodeForEvent({ step: 'fetch' }), null)
  assert.equal(nodeForEvent({ step: 'run' }), null)
  assert.equal(nodeForEvent({ step: 'whatever' }), null)
  assert.equal(nodeForEvent(null), null)
})

check('status words map to node states', () => {
  for (const [w, s] of [['running', 'running'], ['ok', 'ok'], ['done', 'ok'], ['created', 'ok'], ['updated', 'ok'], ['error', 'error'], ['skipped', 'skipped'], ['warning', 'warn'], ['retry', 'warn'], ['started', null], ['???', null]]) {
    assert.equal(nodeStatus(w), s, w)
  }
})

check('a finished run: node states, tallies and badges', () => {
  const f = flowFromEvents(run1())
  assert.equal(f.run.status, 'done')
  assert.equal(f.run.id, 1)
  assert.equal(f.run.query, 'query: France | last 24 h')
  assert.equal(f.run.replay, false)
  const s = (id) => f.nodes[id].status
  assert.deepEqual(['plan', 'validate', 'kanari', 'bluesky', 'x', 'gdelt', 'filter', 'judge', 'locate', 'ticket', 'satellite', 'history', 'lead'].map(s),
    ['ok', 'ok', 'ok', 'skipped', 'error', 'ok', 'ok', 'ok', 'ok', 'ok', 'ok', 'ok', 'ok'])
  assert.equal(f.nodes.x.errors, 1)
  assert.equal(f.nodes.kanari.badge, '12 signals')
  assert.equal(f.nodes.gdelt.badge, '30 headlines')
  assert.equal(f.nodes.bluesky.badge, null)
  assert.equal(f.nodes.filter.badge, 'kept 30/42')
  assert.equal(f.nodes.judge.badge, '8/16 fires') // batches add up
  assert.equal(f.nodes.locate.badge, '6 located')
  assert.equal(f.nodes.ticket.badge, '2 new · 1 upd.')
  assert.equal(f.nodes.satellite.badge, '1/2 overlap')
  assert.equal(f.nodes.history.badge, '2/2')
  assert.equal(f.nodes.plan.badge, 'FR')
  assert.equal(f.nodes.judge.events, 4)
  assert.equal(f.nodes.judge.last, '3 of 8 look like a current fire report in scope; {}')
})

check('a run in progress: the working node is running, later ones idle', () => {
  const evs = run1(null).slice(0, 12) // ... up to "judge running"
  const f = flowFromEvents(evs)
  assert.equal(f.run.status, 'running')
  assert.equal(f.nodes.judge.status, 'running')
  assert.equal(f.nodes.locate.status, 'idle')
  assert.equal(f.nodes.ticket.status, 'idle')
  assert.equal(runIsActive(f, f.lastAt + 1000), true)
  assert.equal(runIsActive(f, f.lastAt + 6 * 60 * 1000), false) // silent for 6 min: a crashed run, not a live one
  assert.equal(runIsActive(flowFromEvents(run1()), Date.now()), false)
})

check('links: live while the target works, done when both ends finished, otherwise idle', () => {
  const f = flowFromEvents(run1(null).slice(0, 12))
  assert.equal(linkState(f.nodes, 'filter', 'judge'), 'live')
  assert.equal(linkState(f.nodes, 'plan', 'validate'), 'done')
  assert.equal(linkState(f.nodes, 'judge', 'locate'), 'idle')
  assert.equal(linkState(f.nodes, 'validate', 'bluesky'), 'idle') // skipped: nothing flowed
  assert.equal(linkState(f.nodes, 'validate', 'x'), 'done') // failed after being asked: the attempt happened
})

check('a run that ended in error settles the nodes still running into errors', () => {
  const evs = [...run1(null).slice(0, 12), ev(1, 'run', 'error', 'boom')]
  const f = flowFromEvents(evs)
  assert.equal(f.run.status, 'error')
  assert.equal(f.nodes.judge.status, 'error')
  assert.equal(f.nodes.locate.status, 'idle')
  // and one that ended "done" settles a node left running into ok
  const done = flowFromEvents([...run1(null).slice(0, 12), ev(1, 'run', 'done', 'finished')])
  assert.equal(done.nodes.judge.status, 'ok')
})

check('the newest run wins by default; an older one can be asked for; other runs never leak in', () => {
  const second = [ev(2, 'run', 'started', 'REPLAY query: Algeria | 2026-08-24 to 2026-08-30'), ev(2, 'plan', 'ok', 'Algeria (DZ)')]
  const all = [...run1(), ...second]
  assert.equal(latestRunId(all), 2)
  const f2 = flowFromEvents(all)
  assert.equal(f2.run.id, 2)
  assert.equal(f2.run.replay, true)
  assert.equal(f2.nodes.plan.badge, 'DZ')
  assert.equal(f2.nodes.judge.status, 'idle')
  const f1 = flowFromEvents(all, 1)
  assert.equal(f1.run.status, 'done')
  assert.equal(f1.nodes.judge.badge, '8/16 fires')
  assert.equal(flowFromEvents([]).run.status, 'idle')
  assert.equal(flowFromEvents([ev(null, 'plan', 'ok', 'x')]).nodes.plan.status, 'idle') // events without a run are ignored
})

check('a warning then a success leaves the node ok; an error then a retry that works too', () => {
  const evs = [ev(3, 'run', 'started', 'q'), ev(3, 'plan', 'running', 'attempt 1'), ev(3, 'validate', 'retry', 'bad zone'), ev(3, 'validate', 'warning', 'kept the valid zones'), ev(3, 'validate', 'ok', 'resolved')]
  assert.equal(flowFromEvents(evs).nodes.validate.status, 'ok')
  assert.equal(flowFromEvents(evs.slice(0, 4)).nodes.validate.status, 'warn')
})

check('plan badge: country codes, short; a plan without codes is cut short', () => {
  const plan = (msg) => flowFromEvents([ev(8, 'run', 'started', 'q'), ev(8, 'plan', 'ok', msg)]).nodes.plan.badge
  assert.equal(plan('Tunisia (TN), Algeria (DZ)'), 'TN · DZ')
  assert.equal(plan('A (fr), B (de), C (es), D (it), E (pt)'), 'FR · DE · ES +2')
  assert.equal(plan('the whole Mediterranean basin'), 'the whole M…')
  assert.equal(plan('Chad'), 'Chad')
})

check('events of one node, in order; step tags', () => {
  const all = run1()
  assert.deepEqual(eventsFor(all, 1, 'judge').map((e) => e.status), ['running', 'ok', 'running', 'ok'])
  assert.equal(eventsFor(all, 1).length, all.length)
  assert.equal(eventsFor(all, 2).length, 0)
  assert.equal(stepTag({ step: 'fetch', source: 'x' }), 'fetch/x')
  assert.equal(stepTag({ step: 'plan', source: null }), 'plan')
})

check('replay runs: filter keeps seeds ("replaying N of M"), fetch counts archived fires', () => {
  const evs = [
    ev(4, 'run', 'started', 'REPLAY query: Tunisie | 2026-08-24 to 2026-08-30'),
    ev(4, 'fetch', 'ok', '12 archived fires in TN', 'kanari'),
    ev(4, 'filter', 'ok', 'kept the 6 largest of 9 satellite clusters'),
  ]
  const f = flowFromEvents(evs)
  assert.equal(f.nodes.kanari.badge, '12 signals')
  assert.equal(f.nodes.filter.badge, 'kept 6/9') // "the 6 largest of 9"
  const seeds = flowFromEvents([ev(5, 'run', 'started', 'REPLAY q'), ev(5, 'filter', 'ok', 'replaying 6 of 12 archived fires in the zones (witness posts or press first, then size)', 'kanari')])
  assert.equal(seeds.nodes.filter.badge, 'kept 6/12')
  const skipped = flowFromEvents([ev(6, 'run', 'started', 'q'), ev(6, 'filter', 'ok', 'skipped 4 satellite clusters south of 34°N in DZ (oil and gas fields)', 'kanari')])
  assert.equal(skipped.nodes.filter.badge, null) // a skip notice is not a count
})

check('run errors read as plain sentences, never settings or file names', () => {
  const key = friendlyRunError('LLMError: no Groq key: set test_fire_groq_key (or GROQ_API_KEY) in .env')
  assert.match(key, /key is missing/)
  assert.doesNotMatch(key, /\.env|GROQ_API_KEY|LLMError/)
  assert.match(friendlyRunError('HTTPError 401 Unauthorized: invalid api key'), /not accepted/)
  assert.match(friendlyRunError('gdelt: rate limited (HTTP 429)'), /slow down/)
  assert.match(friendlyRunError('ReadTimeout: timed out'), /too long/)
  assert.match(friendlyRunError('Groq daily cap reached: 900 calls a day'), /quota/)
  assert.match(friendlyRunError('KeyError: zones'), /unexpected problem/) // a Python KeyError is not a missing key
  assert.match(friendlyRunError(null), /unexpected problem/)
})

console.log(`\n${n} checks passed`)
