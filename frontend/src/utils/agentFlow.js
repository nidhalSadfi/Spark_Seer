/**
 * The incident agents' pipeline as a node graph, and the reducer that turns the agents' step events
 * (GET /api/events/stream of incident_agents) into the state of every node.
 *
 * Every pipeline step reports `{id, run_id, ts, step, source, status, message, data}`. A run goes
 * plan → validate → (Kanari, Bluesky, X, GDELT) → filter → judge → locate → ticket → (satellite,
 * history, lead). Nothing here draws: components/social/AgentGraph.jsx does.
 *
 * No imports on purpose: dev-checks/agent-flow.check.mjs runs this file in plain Node.
 */

/**
 * kind: 'llm' (a model decides), 'code' (plain code) or 'source' (a feed it reads).
 * u, v: position on the graph's floor grid; u runs along the flow, v across it.
 */
export const FLOW_NODES = [
  { id: 'plan', label: 'Plan', kind: 'llm', u: 0, v: 1.5, role: 'A model turns your text into zones and extra keywords.' },
  { id: 'validate', label: 'Validate', kind: 'code', u: 1, v: 1.5, role: 'Resolves each zone to a box; sends errors back to the model (at most 2 retries).' },
  { id: 'kanari', label: 'Kanari', kind: 'source', u: 2, v: 0, role: 'Verified fire signals and satellite clusters (kanari.io).' },
  { id: 'bluesky', label: 'Bluesky', kind: 'source', u: 2, v: 1, role: 'Post search on Bluesky.' },
  { id: 'x', label: 'X', kind: 'source', u: 2, v: 2, role: 'Post search on X (TwitterAPI.io).' },
  { id: 'gdelt', label: 'GDELT', kind: 'source', u: 2, v: 3, role: 'News headlines from GDELT.' },
  { id: 'filter', label: 'Filter', kind: 'code', u: 3, v: 1.5, role: 'Drops items outside the time window, without a search term, or already seen.' },
  { id: 'judge', label: 'Judge', kind: 'llm', u: 4, v: 1.5, role: 'One model agent per source decides the category, the timing and the place named.' },
  { id: 'locate', label: 'Locate', kind: 'code', u: 5, v: 1.5, role: 'Finds each named place inside the zone; coordinates are never invented.' },
  { id: 'ticket', label: 'Ticket', kind: 'code', u: 6, v: 1.5, role: 'Merges mentions of the same incident into one ticket.' },
  { id: 'satellite', label: 'Satellite', kind: 'code', u: 7, v: 0.5, role: 'Checks Kanari clusters and NASA FIRMS detections near the ticket.' },
  { id: 'history', label: 'History', kind: 'code', u: 7, v: 1.5, role: 'The 25-year fire history of the 0.5° cell under the ticket.' },
  { id: 'lead', label: 'Lead', kind: 'code', u: 7, v: 2.5, role: 'Minutes from the first public post to the first satellite detection.' },
]

/** [from, to]: data flows from the first node to the second. */
export const FLOW_LINKS = [
  ['plan', 'validate'],
  ['validate', 'kanari'],
  ['validate', 'bluesky'],
  ['validate', 'x'],
  ['validate', 'gdelt'],
  ['kanari', 'filter'],
  ['bluesky', 'filter'],
  ['x', 'filter'],
  ['gdelt', 'filter'],
  ['filter', 'judge'],
  ['judge', 'locate'],
  ['locate', 'ticket'],
  ['ticket', 'satellite'],
  ['ticket', 'history'],
  ['ticket', 'lead'],
]

export const NODE_IDS = FLOW_NODES.map((n) => n.id)
const SOURCE_IDS = ['kanari', 'bluesky', 'x', 'gdelt']

/** The node an event belongs to, or null (run-level events and unknown steps). */
export function nodeForEvent(e) {
  if (!e) return null
  if (e.step === 'fetch') return SOURCE_IDS.includes(e.source) ? e.source : null
  return NODE_IDS.includes(e.step) ? e.step : null
}

/** Event status word → 'running' | 'ok' | 'error' | 'skipped' | 'warn' (null: not a node state). */
export function nodeStatus(status) {
  switch (status) {
    case 'running':
      return 'running'
    case 'ok':
    case 'done':
    case 'created':
    case 'updated':
      return 'ok'
    case 'error':
      return 'error'
    case 'skipped':
      return 'skipped'
    case 'warning':
    case 'retry':
      return 'warn'
    default:
      return null
  }
}

const ms = (iso) => {
  const t = Date.parse(iso)
  return Number.isFinite(t) ? t : null
}
const int = (s) => Number.parseInt(s, 10)

/** Fold one event's message into the node's running tallies (what its badge shows). */
function tally(node, e) {
  const msg = typeof e.message === 'string' ? e.message : ''
  const s = node.stats
  if (e.status === 'created') s.created = (s.created ?? 0) + 1
  if (e.status === 'updated') s.updated = (s.updated ?? 0) + 1
  if (e.status !== 'ok') return
  let m
  switch (node.id) {
    case 'kanari':
    case 'bluesky':
    case 'x':
    case 'gdelt':
      if ((m = /(\d+) (?:posts|headlines|archived fires)/.exec(msg))) s.items = (s.items ?? 0) + int(m[1])
      break
    case 'filter':
      // "kept 30 of 42 posts", "kept the 20 largest of 35 satellite clusters", "replaying 6 of 12 archived fires"
      if ((m = /kept the (\d+) largest of (\d+)|replaying (\d+) of (\d+)|kept (\d+) of (\d+)/.exec(msg))) {
        s.kept = int(m[1] ?? m[3] ?? m[5])
        s.of = int(m[2] ?? m[4] ?? m[6])
      }
      break
    case 'judge':
      if ((m = /(\d+) of (\d+) look like/.exec(msg))) {
        s.fire = (s.fire ?? 0) + int(m[1])
        s.total = (s.total ?? 0) + int(m[2])
      } else if ((m = /reused (\d+) verdicts/.exec(msg))) s.reused = int(m[1])
      break
    case 'locate':
      if ((m = /(\d+) located/.exec(msg))) s.located = int(m[1])
      break
    case 'satellite':
      s.checked = (s.checked ?? 0) + 1
      if (/Satellite overlap/.test(msg)) s.overlap = (s.overlap ?? 0) + 1
      break
    case 'history':
      if ((m = /on (\d+) of (\d+) tickets/.exec(msg))) {
        s.withHistory = int(m[1])
        s.of = int(m[2])
      }
      break
    case 'plan':
      s.zones = msg
      break
    default:
  }
}

/** A few words for under a node's label, or null when there is nothing to say yet. */
export function badgeFor(node) {
  const s = node.stats
  switch (node.id) {
    case 'kanari':
    case 'bluesky':
    case 'x':
    case 'gdelt':
      return s.items != null ? `${s.items} ${node.id === 'gdelt' ? 'headlines' : node.id === 'kanari' ? 'signals' : 'posts'}` : null
    case 'filter':
      return s.kept != null ? (s.of != null ? `kept ${s.kept}/${s.of}` : `kept ${s.kept}`) : null
    case 'judge':
      if (s.total != null) return `${s.fire}/${s.total} fires`
      return s.reused != null ? `reused ${s.reused}` : null
    case 'locate':
      return s.located != null ? `${s.located} located` : null
    case 'ticket':
      return s.created != null || s.updated != null ? `${s.created ?? 0} new · ${s.updated ?? 0} upd.` : null
    case 'satellite':
      return s.checked != null ? `${s.overlap ?? 0}/${s.checked} overlap` : null
    case 'history':
      return s.withHistory != null ? `${s.withHistory}/${s.of}` : null
    case 'plan': {
      if (!s.zones) return null
      // "Tunisia (TN), Algeria (DZ)" → "TN · DZ" (short enough to sit under the tile)
      const codes = [...s.zones.matchAll(/\(([A-Za-z]{2})\)/g)].map((m) => m[1].toUpperCase())
      if (codes.length) return codes.slice(0, 3).join(' · ') + (codes.length > 3 ? ` +${codes.length - 3}` : '')
      return s.zones.length > 12 ? `${s.zones.slice(0, 11)}…` : s.zones
    }
    default:
      return null
  }
}

/** The newest run id among the events, or null. */
export function latestRunId(events) {
  let id = null
  for (const e of events) if (Number.isInteger(e.run_id) && (id == null || e.run_id > id)) id = e.run_id
  return id
}

/**
 * The state of the graph for one run (default: the newest run in `events`).
 *   run    { id, status: 'idle'|'running'|'done'|'error', query, replay, startedAt, finishedAt, message }
 *   nodes  { [id]: { id, status: 'idle'|'running'|'ok'|'error'|'skipped'|'warn', events, errors, last, lastAt, stats, badge } }
 *   lastAt time of the newest event of the run (ms), or null
 * When the run ended, nodes still "running" are settled: ok after "done", error after "error".
 */
export function flowFromEvents(events, runId = latestRunId(events)) {
  const nodes = Object.fromEntries(
    FLOW_NODES.map((n) => [n.id, { id: n.id, status: 'idle', events: 0, errors: 0, last: null, lastAt: null, stats: {}, badge: null }]),
  )
  const run = { id: runId, status: 'idle', query: null, replay: false, startedAt: null, finishedAt: null, message: null }
  let lastAt = null
  const mine = events.filter((e) => runId != null && e.run_id === runId).sort((a, b) => a.id - b.id)

  for (const e of mine) {
    const t = ms(e.ts)
    if (t != null && (lastAt == null || t > lastAt)) lastAt = t
    if (e.step === 'run') {
      if (e.status === 'started') {
        run.status = 'running'
        run.startedAt = t
        run.query = e.message ?? null
        run.replay = /^REPLAY/.test(e.message ?? '')
      } else if (e.status === 'done' || e.status === 'error') {
        run.status = e.status
        run.finishedAt = t
        run.message = e.message ?? null
      }
      continue
    }
    const id = nodeForEvent(e)
    if (!id) continue
    const node = nodes[id]
    node.events++
    node.last = e.message ?? null
    node.lastAt = t
    const st = nodeStatus(e.status)
    if (st) node.status = st
    if (st === 'error') node.errors++
    tally(node, e)
  }

  for (const node of Object.values(nodes)) {
    if (node.status === 'running' && run.status === 'done') node.status = 'ok'
    else if (node.status === 'running' && run.status === 'error') node.status = 'error'
    node.badge = badgeFor(node)
  }
  return { run, nodes, lastAt }
}

/** True while a run is going on: it started, has not ended, and was heard from recently. */
export function runIsActive(flow, now, staleMs = 5 * 60 * 1000) {
  return flow.run.status === 'running' && flow.lastAt != null && now - flow.lastAt < staleMs
}

/** 'live' (the target is working: data is flowing), 'done' (both ends finished) or 'idle'. */
export function linkState(nodes, from, to) {
  const a = nodes[from]?.status
  const b = nodes[to]?.status
  if (b === 'running') return 'live'
  if (a === 'ok' && (b === 'ok' || b === 'error' || b === 'warn')) return 'done'
  return 'idle'
}

/** The events of one run, optionally only those of one node, oldest first. */
export function eventsFor(events, runId, nodeId = null) {
  return events
    .filter((e) => e.run_id === runId && (nodeId == null || nodeForEvent(e) === nodeId))
    .sort((a, b) => a.id - b.id)
}

/** "fetch/bluesky" or "plan": the step label of an event. */
export const stepTag = (e) => (e.source ? `${e.step}/${e.source}` : e.step)

// ───────────────────────── the API's JSON → UI shapes ─────────────────────────

const str = (v) => (typeof v === 'string' ? v : '')
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/** One step event from the stream, or null when it has no usable id and step. */
export function eventFromApi(raw) {
  if (!raw || typeof raw !== 'object' || !Number.isInteger(raw.id) || typeof raw.step !== 'string') return null
  return {
    id: raw.id,
    run_id: Number.isInteger(raw.run_id) ? raw.run_id : null,
    ts: str(raw.ts),
    step: raw.step,
    source: str(raw.source) || null,
    status: str(raw.status),
    message: str(raw.message),
    data: raw.data && typeof raw.data === 'object' ? raw.data : null,
  }
}

/** One row of GET /api/runs (times in ms; `summary` is the run's own JSON summary or null). */
export function runFromApi(raw) {
  if (!raw || typeof raw !== 'object' || !Number.isInteger(raw.id)) return null
  return {
    id: raw.id,
    query: str(raw.query),
    hours: num(raw.hours),
    scheduleId: Number.isInteger(raw.schedule_id) ? raw.schedule_id : null,
    startedAt: ms(raw.started_at),
    finishedAt: ms(raw.finished_at),
    status: str(raw.status) || 'unknown',
    replay: raw.mode === 'replay',
    summary: raw.summary && typeof raw.summary === 'object' ? raw.summary : null,
  }
}

/** One row of GET /api/schedules. */
export function scheduleFromApi(raw) {
  if (!raw || typeof raw !== 'object' || !Number.isInteger(raw.id)) return null
  return {
    id: raw.id,
    query: str(raw.query),
    hours: num(raw.hours),
    intervalMin: num(raw.interval_min),
    enabled: raw.enabled !== 0 && raw.enabled !== false,
    lastRunAt: ms(raw.last_run_at),
    nextRunAt: ms(raw.next_run_at),
  }
}

/**
 * A run's error as people read it. The agents' own message (kept in the activity log) names
 * exceptions, settings and files; the banner and the runs list show this instead.
 */
export function friendlyRunError(message) {
  const m = String(message ?? '')
  if (/\bkey\b/i.test(m) && /\b(no|missing|not set|invalid|unauthori[sz]ed|401|403)\b/i.test(m)) {
    return 'A required key is missing or was not accepted, so the search could not run.'
  }
  if (/\b429\b|rate.?limit/i.test(m)) return 'A source asked to slow down. Try again in a few minutes.'
  if (/time.?out|timed out/i.test(m)) return 'A source took too long to answer. Try again later.'
  if (/quota|calls? a day|per day/i.test(m)) return 'Today’s free search quota is used up. Try again tomorrow.'
  return 'The search stopped because of an unexpected problem. Try again later.'
}
