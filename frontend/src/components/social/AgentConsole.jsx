import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { Activity, Radio, WifiOff, X } from 'lucide-react'
import { Badge } from '../ui/primitives'
import AgentControls from './AgentControls'
import AgentGraph from './AgentGraph'
import { useAddSchedule, useAgentsStatus, useIncidentRuns, useIncidentSchedules, useRemoveSchedule, useStartRun } from '../../hooks/useIncidents'
import { useNow } from '../../hooks/useNow'
import { FLOW_NODES, eventsFor, flowFromEvents, friendlyRunError, latestRunId, runIsActive, stepTag } from '../../utils/agentFlow'
import { timeAgo } from '../../utils/reports'

const panel = 'rounded-2xl border border-line bg-gradient-to-br from-card to-[#131f31]'
const SHOWN = 60 // activity rows

const pad = (n) => String(n).padStart(2, '0')
const clock = (ms) => {
  const d = new Date(ms)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
const STATUS_TONE = { running: 'text-info', ok: 'text-ok', done: 'text-ok', created: 'text-ok', updated: 'text-ok', error: 'text-red-300', skipped: 'text-faint', warning: 'text-yellow-200', retry: 'text-yellow-200' }
const RUN_TONE = { running: 'info', done: 'ok', error: 'fire', idle: 'neutral', unknown: 'neutral' }

function Legend() {
  const dot = (c) => <i className="inline-block size-2 rounded-full" style={{ background: c }} aria-hidden />
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-faint" aria-label="Legend">
      <li className="flex items-center gap-1.5">{dot('#fb923c')} working: the tile rises</li>
      <li className="flex items-center gap-1.5">{dot('#22c55e')} done</li>
      <li className="flex items-center gap-1.5">{dot('#ef4444')} failed</li>
      <li className="flex items-center gap-1.5">{dot('#26364d')} waiting or skipped</li>
      <li className="text-faint">Stepped tile: a model agent · ring: a source</li>
    </ul>
  )
}

function Activity_({ events, runId, node, onClear }) {
  const rows = useMemo(() => eventsFor(events, runId, node).slice(-SHOWN).reverse(), [events, runId, node])
  const label = node ? FLOW_NODES.find((n) => n.id === node)?.label : null
  return (
    <section className={`${panel} p-4`} aria-label="Activity">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-2 text-[15px] font-semibold text-ink">
          <Activity className="size-4 text-fire" aria-hidden /> Activity
        </h2>
        {label && (
          <button type="button" onClick={onClear} className="inline-flex items-center gap-1 rounded-full border border-fire/40 bg-fire/10 px-2.5 py-0.5 text-[11px] font-medium text-orange-200 hover:bg-fire/20">
            {label} <X className="size-3" aria-hidden />
          </button>
        )}
        <span className="ml-auto text-[11px] text-faint">newest first</span>
      </div>
      {rows.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">{node ? `${label} has not reported in this run.` : 'No steps yet. Start a search and its steps appear here live.'}</p>
      ) : (
        <ol className="max-h-72 overflow-y-auto font-mono text-[11.5px] leading-relaxed">
          {rows.map((e) => (
            <li key={e.id} className="grid grid-cols-[4.2rem_8.5rem_minmax(0,1fr)] gap-x-2 border-t border-line/60 py-1 first:border-t-0">
              <span className="text-faint">{e.ts ? clock(Date.parse(e.ts)) : ''}</span>
              <span className={STATUS_TONE[e.status] ?? 'text-muted'}>
                {stepTag(e)} · {e.status}
              </span>
              <span className="min-w-0 break-words font-sans text-[12px] text-muted">{e.message}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

function Runs({ runs, current, onPick }) {
  if (!runs?.length) return null
  return (
    <section className={`${panel} p-4`} aria-label="Recent runs">
      <h2 className="mb-2 text-[15px] font-semibold text-ink">Recent runs</h2>
      <ul className="flex flex-col gap-1">
        {runs.map((r) => {
          const s = r.summary
          return (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => onPick(r.id)}
                aria-current={current === r.id ? 'true' : undefined}
                className={`flex w-full flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border px-3 py-2 text-left text-xs transition-colors ${
                  current === r.id ? 'border-fire/50 bg-fire/10' : 'border-transparent hover:bg-card-hi/70'
                }`}
              >
                <b className="font-mono text-ink">#{r.id}</b>
                <span className="min-w-0 flex-1 truncate text-muted">{r.query}</span>
                {r.replay && <Badge tone="info">Replay</Badge>}
                {r.scheduleId != null && <Badge tone="neutral">Scheduled</Badge>}
                <Badge tone={RUN_TONE[r.status] ?? 'neutral'}>{r.status}</Badge>
                <span className="w-full text-[11px] text-faint">
                  {r.startedAt != null && timeAgo(r.startedAt)}
                  {s && !s.error && s.tickets_touched != null && ` · ${s.tickets_created ?? 0} new, ${s.tickets_touched - (s.tickets_created ?? 0)} updated tickets`}
                  {s?.error && ` · ${friendlyRunError(s.error)}`}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

/**
 * The agent console, inside Social Alerts: start a search (live or a replay of past dates), schedule
 * it, and watch the agents work as a node graph fed by the server's live step events.
 */
export default function AgentConsole({ events, streamState }) {
  const runs = useIncidentRuns()
  const agentsStatus = useAgentsStatus()
  const schedules = useIncidentSchedules()
  const start = useStartRun()
  const addSchedule = useAddSchedule()
  const removeSchedule = useRemoveSchedule()

  const [picked, setPicked] = useState(null) // a run chosen from the list; null follows the newest
  const [node, setNode] = useState(null) // a tile chosen in the graph: filters the activity
  const now = useNow()

  const newest = latestRunId(events)
  const runId = picked ?? newest
  const flow = useMemo(() => flowFromEvents(events, runId), [events, runId])
  const newestFlow = useMemo(() => (runId === newest ? flow : flowFromEvents(events, newest)), [events, runId, newest, flow])
  const running = runIsActive(newestFlow, now)

  const onRun = (body) =>
    start.mutate(body, {
      onSuccess: () => {
        setPicked(null)
        setNode(null)
        toast.success(body.replay_from ? 'Replay started. Watch the agents below.' : 'Run started. Watch the agents below.')
      },
      onError: (e) => toast.error(e.status === 409 ? 'A run is already in progress. Wait for it to finish.' : e.message),
    })

  const status = flow.run.status
  return (
    <div className="grid items-start gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
      <AgentControls
        running={running}
        starting={start.isPending}
        schedules={schedules.data ?? []}
        scheduling={addSchedule.isPending}
        now={now}
        agents={agentsStatus.data}
        onRun={onRun}
        onSchedule={(body) => addSchedule.mutate(body, { onSuccess: () => toast.success('Scheduled.'), onError: (e) => toast.error(e.message) })}
        onRemoveSchedule={(id) => removeSchedule.mutate(id, { onError: (e) => toast.error(e.message) })}
      />

      <div className="flex min-w-0 flex-col gap-4">
        <section className={`${panel} p-4 sm:p-5`} aria-label="The agents">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <h2 className="flex items-center gap-2 text-[15px] font-semibold text-ink">
              <Radio className="size-4 text-fire" aria-hidden /> The agents
            </h2>
            {flow.run.id != null && (
              <>
                <Badge tone={RUN_TONE[status] ?? 'neutral'}>
                  Run #{flow.run.id} · {status}
                </Badge>
                {flow.run.replay && <Badge tone="info">Replay</Badge>}
              </>
            )}
            <span className="ml-auto flex items-center gap-1.5 text-[11px] text-faint" title="Live updates from the agents">
              {streamState === 'open' ? (
                <>
                  <i className="size-1.5 rounded-full bg-ok" aria-hidden /> Live
                </>
              ) : (
                <>
                  <WifiOff className="size-3.5" aria-hidden /> {streamState === 'error' ? 'Reconnecting…' : 'Connecting…'}
                </>
              )}
            </span>
          </div>
          {flow.run.query && <p className="mb-2 truncate text-xs text-muted">{flow.run.query}</p>}
          {status === 'error' && (
            <div role="alert" className="mb-3 rounded-lg border border-fire-high/40 bg-fire-high/10 px-3 py-2 text-xs leading-relaxed text-red-200">
              <b className="font-semibold">Run #{flow.run.id} failed.</b> {friendlyRunError(flow.run.message)}
              <span className="mt-1 block text-red-200/80">The step-by-step log below shows where it stopped.</span>
            </div>
          )}
          {flow.run.id == null && <p className="mb-2 text-xs text-muted">No run yet. Start a search on the left and the agents light up here as they work.</p>}
          {picked != null && picked !== newest && (
            <p className="mb-2 text-xs text-faint">
              Showing an earlier run.{' '}
              <button type="button" onClick={() => setPicked(null)} className="font-medium text-orange-200 hover:underline">
                Follow the newest
              </button>
            </p>
          )}

          <AgentGraph flow={flow} selected={node} onSelect={setNode} />
          <div className="mt-2 border-t border-line pt-2">
            <Legend />
          </div>
        </section>

        <Activity_ events={events} runId={runId} node={node} onClear={() => setNode(null)} />
        <Runs
          runs={runs.data}
          current={runId}
          onPick={(id) => {
            setPicked(id === newest ? null : id)
            setNode(null)
          }}
        />
      </div>
    </div>
  )
}
