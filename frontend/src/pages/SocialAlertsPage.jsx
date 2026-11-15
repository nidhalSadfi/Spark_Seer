import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { motion } from 'motion/react'
import { toast } from 'sonner'
import {
  ArrowUpRight,
  Check,
  Cloud,
  ExternalLink,
  Flame,
  History,
  LoaderCircle,
  MapPinPlus,
  Newspaper,
  Quote,
  Radio,
  RefreshCw,
  Satellite,
} from 'lucide-react'
import { Badge, Notice, PageHeader, Skeleton } from '../components/ui/primitives'
import ReportMiniMap from '../components/reports/ReportMiniMap'
import { notifyReportError } from '../components/reports/notifyError'
import AgentConsole from '../components/social/AgentConsole'
import { useBackendIncidents, useIncidentEvents, useIncidentTickets } from '../hooks/useIncidents'
import { useNow } from '../hooks/useNow'
import { useReports } from '../hooks/useReports'
import { flowFromEvents, latestRunId, runIsActive } from '../utils/agentFlow'
import {
  categoryLabel,
  diagnoseAgents,
  isApproximate,
  leadWords,
  matchLine,
  reportOf,
  reportValues,
  sourceLabel,
  statusInfo,
} from '../utils/incidents'
import { STATUS as REPORT_STATUS, fmtLatLon, fmtLocal, timeAgo } from '../utils/reports'

const panel = 'rounded-2xl border border-line bg-gradient-to-br from-card to-[#131f31]'
const BULK_MAX = 25 // most alerts one click can file, so the review queue is not flooded

// Short tab labels (the list column is narrow); `id` is the status word the agents use.
const FILTERS = [
  { id: 'all', label: 'All', title: 'All tickets' },
  { id: 'Satellite overlap', label: 'Overlap', title: 'Satellite overlap' },
  { id: 'No satellite support', label: 'No support', title: 'No satellite support' },
  { id: 'Mentioned', label: 'Mentioned', title: 'Mentioned (no satellite check)' },
]

function CategoryIcon({ category, className = '' }) {
  const Icon = category === 'active_fire_report' ? Flame : category === 'smoke_or_possible_fire' ? Cloud : category === 'news_about_fire' ? Newspaper : category === 'satellite' ? Satellite : Radio
  const tone = category === 'active_fire_report' ? 'bg-fire/15 text-fire' : category === 'satellite' ? 'bg-info/15 text-info' : 'bg-line/60 text-muted'
  return (
    <span className={`grid size-9 shrink-0 place-items-center rounded-lg ${tone} ${className}`}>
      <Icon className="size-5" aria-hidden />
    </span>
  )
}

function ReportedBadge({ report }) {
  const st = REPORT_STATUS[report.status]
  return <Badge tone={st.tone}>Reported · {st.short.toLowerCase()}</Badge>
}

function TicketRow({ ticket, report, active, onSelect }) {
  const st = statusInfo(ticket.status)
  const n = ticket.sources.length
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(ticket.id)}
        aria-current={active ? 'true' : undefined}
        className={`flex w-full items-start gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors ${
          active ? 'border-fire/50 bg-fire/10' : 'border-transparent hover:bg-card-hi/70'
        }`}
      >
        <CategoryIcon category={ticket.category} />
        <span className="min-w-0 flex-1">
          <b className="block truncate text-sm font-semibold text-ink">{ticket.title}</b>
          <span className="mt-0.5 block truncate text-xs text-muted">
            {[ticket.place, ticket.country].filter(Boolean).join(' · ') || 'Unknown place'}
          </span>
          <span className="mt-1 flex flex-wrap items-center gap-1.5">
            <Badge tone={st.tone}>{ticket.status}</Badge>
            {ticket.replay && <Badge tone="info">Replay</Badge>}
            {report && <ReportedBadge report={report} />}
          </span>
        </span>
        <span className="shrink-0 text-right text-[11px] text-faint">
          {ticket.lastSeen != null && <span className="block">{timeAgo(ticket.lastSeen)}</span>}
          <span className="block">
            {n} {n === 1 ? 'source' : 'sources'}
          </span>
        </span>
      </button>
    </li>
  )
}

function SourceCard({ s }) {
  const when = s.publishedAt ?? s.fetchedAt
  return (
    <li className="rounded-xl border border-line bg-sidebar/60 p-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Badge tone="info">{sourceLabel(s.source)}</Badge>
        {s.author && <span className="text-xs text-muted">{s.author}</span>}
        {when != null && (
          <span className="ml-auto text-[11px] text-faint" title={s.fetchedAt != null ? `Fetched ${fmtLocal(s.fetchedAt)}` : undefined}>
            {s.publishedAt != null ? 'posted' : 'fetched'} {fmtLocal(when)}
          </span>
        )}
      </div>
      {s.excerpt && (
        <p className="mt-2 flex gap-2 text-[13px] leading-relaxed text-muted">
          <Quote className="mt-1 size-3.5 shrink-0 text-faint" aria-hidden />
          <span className="min-w-0 break-words">{s.excerpt}</span>
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11.5px] text-faint">
        <span>{s.placePhrase ? `Place named: “${s.placePhrase}”` : 'No place named in the text'}</span>
        {s.url ? (
          <a
            href={s.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 font-medium text-orange-200 hover:underline"
          >
            Open original <ExternalLink className="size-3.5" aria-hidden />
          </a>
        ) : (
          <span>No link</span>
        )}
      </div>
    </li>
  )
}

function Satellites({ ticket }) {
  const sat = ticket.satellite
  const st = statusInfo(ticket.status)
  const lead = sat?.lead
  const leads = [
    { label: 'Kanari first detection', words: leadWords(lead?.minutes_vs_kanari) },
    { label: 'First FIRMS pass', words: leadWords(lead?.minutes_vs_polar) },
  ].filter((l) => l.words)

  return (
    <div className="flex flex-col gap-3 text-[13px] leading-relaxed text-muted">
      <div className="flex items-start gap-3 rounded-xl border border-line bg-sidebar/60 px-3.5 py-3">
        <Satellite className={`mt-0.5 size-5 shrink-0 ${ticket.status === 'Satellite overlap' ? 'text-ok' : 'text-faint'}`} aria-hidden />
        <div className="min-w-0">
          <p>
            <b className="font-semibold text-ink">{ticket.status}.</b> {st.hint}
          </p>
          {sat && sat.matches.length > 0 && (
            <ul className="mt-1.5 list-inside list-disc text-xs text-faint">
              {sat.matches.slice(0, 4).map((m, i) => (
                <li key={i}>{matchLine(m)}</li>
              ))}
            </ul>
          )}
          {sat && sat.matches.length === 0 && sat.checked.length > 0 && (
            <p className="mt-1 text-xs text-faint">
              Checked {sat.checked.join(' and ')} within {sat.radiusKm} km.
            </p>
          )}
        </div>
      </div>

      {leads.length > 0 && (
        <div className="rounded-xl border border-line bg-sidebar/60 px-3.5 py-3">
          <p className="font-semibold text-ink">Lead time</p>
          <ul className="mt-1 text-xs">
            {leads.map((l) => (
              <li key={l.label}>
                vs {l.label}: <b className="text-ink">{l.words}</b>
              </li>
            ))}
          </ul>
          {lead.posts_truncated && <p className="mt-1 text-[11px] text-faint">The post search hit its page limit, so people may have posted even earlier.</p>}
        </div>
      )}

      {ticket.history && (
        <div className="flex items-start gap-3 rounded-xl border border-line bg-sidebar/60 px-3.5 py-3">
          <History className="mt-0.5 size-5 shrink-0 text-faint" aria-hidden />
          <p>
            {ticket.history.text} {ticket.history.cellId && <span className="text-[11px] text-faint">(cell {ticket.history.cellId})</span>}
          </p>
        </div>
      )}
    </div>
  )
}

/** The call to action: file this mention as an unverified community alert, or show where it went. */
function ReportAction({ ticket, report, saving, blocked, onReport }) {
  if (report) {
    const st = REPORT_STATUS[report.status]
    return (
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl border border-ok/30 bg-ok/[0.05] px-4 py-3">
        <p className="flex items-center gap-2 text-sm text-muted">
          <Check className="size-4 text-ok" aria-hidden />
          <span>
            <b className="font-semibold text-ink">Reported</b> {timeAgo(report.createdAt)}. Review status: {st.label.toLowerCase()}.
          </span>
        </p>
        <div className="flex flex-wrap items-center gap-3 text-xs font-medium">
          <Link to={`/dashboard/reports?report=${encodeURIComponent(report.id)}`} className="inline-flex items-center gap-1 text-orange-200 hover:underline">
            Review in Fire Reports <ArrowUpRight className="size-3.5" aria-hidden />
          </Link>
          {report.status !== 'false' && (
            <Link to={`/dashboard/map?report=${encodeURIComponent(report.id)}`} className="inline-flex items-center gap-1 text-orange-200 hover:underline">
              View on the map <ArrowUpRight className="size-3.5" aria-hidden />
            </Link>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-yellow-300/30 bg-yellow-300/[0.04] p-4">
      <p className="text-sm font-semibold text-ink">Report this alert</p>
      <p className="mt-0.5 text-xs text-muted">
        Files an unverified fire alert at these coordinates. It shows as a <b className="text-yellow-200">?</b> on the Global Fire Map and in
        Fire Reports, where someone confirms it or rejects it. The public posts are listed in the note.
      </p>
      {isApproximate(ticket) && ticket.hasLocation && (
        <p className="mt-2 text-xs text-yellow-200">
          The location is only known to about ±{Math.round(ticket.radiusKm ?? 0)} km, so the <b>?</b> marks the centre of the named area, not the fire.
        </p>
      )}
      {!ticket.hasLocation && <p className="mt-2 text-xs text-yellow-200">This ticket has no coordinates, so it cannot be placed on the map.</p>}
      <button
        type="button"
        disabled={!ticket.hasLocation || saving || Boolean(blocked)}
        onClick={() => onReport(ticket)}
        className="mt-3 inline-flex h-10 items-center gap-2 rounded-lg bg-fire px-4 text-sm font-semibold text-[#1a0d02] transition-colors hover:bg-orange-400 disabled:cursor-not-allowed disabled:bg-line disabled:text-faint"
      >
        <MapPinPlus className="size-4" aria-hidden /> {saving ? 'Reporting…' : blocked ?? 'Report as fire alert'}
      </button>
    </div>
  )
}

function TicketDetail({ ticket, report, saving, blocked, onReport }) {
  const st = statusInfo(ticket.status)
  return (
    <div className={`${panel} flex min-w-0 flex-col gap-4 p-4 sm:p-5`}>
      <div className="flex flex-wrap items-start gap-3">
        <CategoryIcon category={ticket.category} />
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] font-semibold text-ink">{ticket.title}</h2>
          <p className="text-xs text-muted">
            {[ticket.place, ticket.country].filter(Boolean).join(' · ')}
            {ticket.hasLocation && <span className="font-mono"> · {fmtLatLon(ticket.lat, ticket.lon)}</span>}
            {ticket.precision && ` · ${ticket.precision}${ticket.radiusKm != null ? ` ±${Math.round(ticket.radiusKm)} km` : ''}`}
          </p>
          <p className="text-xs text-faint">
            {categoryLabel(ticket.category)}
            {ticket.firstSeen != null && ` · first seen ${fmtLocal(ticket.firstSeen)}`}
            {ticket.lastSeen != null && ticket.lastSeen !== ticket.firstSeen && `, last ${fmtLocal(ticket.lastSeen)}`}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge tone={st.tone}>{ticket.status}</Badge>
          {ticket.replay && <Badge tone="info">Replay</Badge>}
        </div>
      </div>

      <ReportAction ticket={ticket} report={report} saving={saving} blocked={blocked} onReport={onReport} />

      <div className="grid gap-4 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        {ticket.hasLocation ? (
          // keyed by ticket and place: a fresh map for every ticket
          <ReportMiniMap
            key={`${ticket.id}:${ticket.lat}:${ticket.lon}`}
            report={{ lat: ticket.lat, lon: ticket.lon, status: 'pending' }}
            radiusKm={Math.max(ticket.radiusKm ?? 5, 1)}
            label={`Dashed circle: location precision ±${Math.round(Math.max(ticket.radiusKm ?? 5, 1))} km`}
            height={280}
          />
        ) : (
          <div className="grid place-items-center rounded-xl border border-line bg-sidebar/60 p-6 text-center text-sm text-muted">No coordinates for this ticket.</div>
        )}
        <Satellites ticket={ticket} />
      </div>

      <section aria-label="Public mentions">
        <h3 className="mb-2 text-sm font-semibold text-ink">
          Public mentions <span className="font-normal text-faint">({ticket.sources.length})</span>
        </h3>
        {ticket.sources.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {ticket.sources.map((s, i) => (
              <SourceCard key={`${s.url ?? s.source}:${i}`} s={s} />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">This ticket has no stored sources.</p>
        )}
      </section>

      <p className="text-[11px] leading-relaxed text-faint">
        Unverified public mentions found by the incident agents, not official alerts. Follow your civil-protection authority.
      </p>
    </div>
  )
}

/**
 * Shown when the incident agents do not answer. The backend starts them, so it is asked what it knows
 * (GET /incidents/status) and the panel says, in plain words, what is going on. The page keeps looking by itself.
 */
function Offline({ onRetry, retrying }) {
  const backend = useBackendIncidents()
  const d = backend.isPending
    ? { kind: 'checking', title: 'Looking for the social media agents…', body: null }
    : diagnoseAgents({ status: backend.data, error: backend.error })
  const busy = d.kind === 'checking' || d.kind === 'starting'

  return (
    <div className={`${panel} flex flex-col items-center gap-3 px-6 py-12 text-center`} role="status">
      <span className="grid size-12 place-items-center rounded-xl bg-line/60 text-faint">
        {busy ? <LoaderCircle className="size-6 animate-spin" aria-hidden /> : <Radio className="size-6" aria-hidden />}
      </span>
      <h2 className="text-base font-semibold text-ink">{d.title}</h2>
      {d.body && <p className="max-w-xl text-sm text-muted">{d.body}</p>}
      <button
        type="button"
        onClick={() => {
          backend.refetch()
          onRetry()
        }}
        disabled={retrying}
        className="inline-flex h-9 items-center gap-2 rounded-lg border border-line px-3 text-sm text-ink hover:border-fire/40 disabled:opacity-60"
      >
        <RefreshCw className={`size-4 ${retrying ? 'animate-spin' : ''}`} aria-hidden /> Try again
      </button>
    </div>
  )
}

/**
 * Social Alerts: fires the incident agents found in public posts and news, each with its sources and
 * satellite check. A ticket can be reported as a community alert (unverified "?"), which then goes
 * through the usual review in Fire Reports.
 */
export default function SocialAlertsPage() {
  const { data: tickets, error, isPending, isFetching, refetch } = useIncidentTickets()
  const { reports, addReport, source: reportsSource, error: reportsError } = useReports()
  const navigate = useNavigate()
  const qc = useQueryClient()

  // Alerts (the tickets) or Agents (the console); the tab is in the URL so it can be linked
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'agents' ? 'agents' : 'alerts'
  const setTab = (t) => setParams(t === 'agents' ? { tab: 'agents' } : {}, { replace: true })

  // The agents' live step events. They keep the tickets and runs fresh while the agents work, and
  // say when a run finishes. The server replays its latest events on connect, so only events from
  // the last half minute count as news.
  const refresh = useRef({ timer: null, told: new Set() })
  const onEvent = (e) => {
    const r = refresh.current
    const ticketStep = ['ticket', 'satellite', 'history', 'lead'].includes(e.step)
    const runEnd = e.step === 'run' && (e.status === 'done' || e.status === 'error')
    if (ticketStep || runEnd || (e.step === 'run' && e.status === 'started')) {
      clearTimeout(r.timer) // a burst of events becomes one refresh
      r.timer = setTimeout(() => {
        qc.invalidateQueries({ queryKey: ['incident-tickets'] })
        qc.invalidateQueries({ queryKey: ['incident-runs'] })
      }, 600)
    }
    const news = Date.now() - Date.parse(e.ts) < 30_000
    if (runEnd && news && !r.told.has(e.id)) {
      r.told.add(e.id)
      if (e.status === 'done') {
        toast.success(`Run #${e.run_id} finished: ${e.message}`, { action: { label: 'See the alerts', onClick: () => setTab('alerts') } })
      } else {
        toast.error(`Run #${e.run_id} failed: ${e.message}`)
      }
    }
  }
  const { events, state: streamState } = useIncidentEvents({ enabled: Boolean(tickets) && !error, onEvent })
  // the live stream dropping usually means the agents (or the backend) just went away: check at once
  // instead of waiting for the next refresh, so the page says what is wrong within seconds
  useEffect(() => {
    if (streamState === 'error') refetch()
  }, [streamState, refetch])
  const now = useNow()
  const agentsBusy = runIsActive(flowFromEvents(events, latestRunId(events)), now)

  // Reporting waits until the existing reports are known: that is what tells "already reported" apart
  // (so nothing is filed twice) and where a new report is saved (the server or this browser).
  const reportsReady = reportsSource !== undefined
  const blocked = reportsReady ? null : reportsError ? 'Reports unavailable' : 'Loading reports…'

  const [filter, setFilter] = useState('all')
  const [selectedId, setSelectedId] = useState(null)
  const [saving, setSaving] = useState(null) // ticket id being reported, or 'bulk'
  const [confirmBulk, setConfirmBulk] = useState(false)

  const all = tickets ?? []
  const shown = filter === 'all' ? all : all.filter((t) => t.status === filter)
  const selected = shown.find((t) => t.id === selectedId) ?? shown[0] ?? null
  const countOf = (id) => (id === 'all' ? all.length : all.filter((t) => t.status === id).length)
  const todo = reportsReady ? shown.filter((t) => t.hasLocation && !reportOf(t, reports)) : []
  const batch = todo.slice(0, BULK_MAX)

  const where = reportsSource === 'local' ? ' It is saved in this browser only for now, because the shared report list is not available.' : ''

  const report = async (ticket) => {
    if (saving || !reportsReady || reportOf(ticket, reports)) return
    setSaving(ticket.id)
    try {
      const created = await addReport(reportValues(ticket))
      toast.success(`Reported as an unverified alert (?). Someone has to confirm it before it counts as a fire.${where}`, {
        action: { label: 'Review', onClick: () => navigate(`/dashboard/reports?report=${encodeURIComponent(created.id)}`) },
      })
    } catch (e) {
      notifyReportError(e)
    } finally {
      setSaving(null)
    }
  }

  const reportBatch = async () => {
    if (saving || !reportsReady) return
    setConfirmBulk(false)
    setSaving('bulk')
    let done = 0
    try {
      for (const t of batch) {
        await addReport(reportValues(t)) // one at a time: stops at the first failure
        done++
      }
      toast.success(`Reported ${done} ${done === 1 ? 'alert' : 'alerts'} as unverified (?).${where}`, {
        action: { label: 'Review', onClick: () => navigate('/dashboard/reports') },
      })
    } catch (e) {
      if (done) toast(`Reported ${done} of ${batch.length} before an error.`)
      notifyReportError(e)
    } finally {
      setSaving(null)
    }
  }

  return (
    <>
      <PageHeader
        kicker="Social detection"
        title="Social Alerts"
        description="Fires mentioned in public posts and news (Bluesky, X, GDELT, Kanari signals), checked against satellite detections. Report one to send it to the review queue, where it shows as a ? on the map until someone confirms it. Run the agents yourself from the Agents tab."
        actions={
          <button
            type="button"
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex h-9 items-center gap-2 rounded-lg border border-line px-3 text-sm text-muted transition-colors hover:border-fire/40 hover:text-ink disabled:opacity-60"
          >
            <RefreshCw className={`size-4 ${isFetching ? 'animate-spin' : ''}`} aria-hidden /> Refresh
          </button>
        }
      />

      <div className="mb-4">
        <Notice icon={Radio} tone="info" title="Unverified public mentions.">
          These are not official alerts and nobody is dispatched. Public posts can be wrong, old or about another place: the satellite check and a
          reviewer decide what counts as a fire.
        </Notice>
      </div>

      <div role="tablist" aria-label="Social Alerts sections" className="mb-4 inline-flex gap-1 rounded-xl border border-line bg-sidebar/70 p-1">
        {[
          ['alerts', 'Alerts', tickets ? tickets.length : null],
          ['agents', 'Agents', null],
        ].map(([id, label, count]) => (
          <button
            key={id}
            role="tab"
            type="button"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={`relative inline-flex items-center gap-2 rounded-lg px-4 py-1.5 text-sm font-medium transition-colors ${tab === id ? 'text-orange-200' : 'text-muted hover:text-ink'}`}
          >
            {tab === id && (
              <motion.span
                layoutId="social-section"
                className="absolute inset-0 rounded-lg border border-fire/30 bg-fire/15"
                transition={{ type: 'spring', stiffness: 420, damping: 34 }}
              />
            )}
            <span className="relative">{label}</span>
            {count != null && <span className="relative tabular-nums text-xs opacity-70">{count}</span>}
            {id === 'agents' && agentsBusy && (
              <span className="relative size-2 rounded-full bg-fire motion-safe:animate-pulse" role="img" aria-label="A run is in progress" />
            )}
          </button>
        ))}
      </div>

      {isPending ? (
        <div className="grid gap-4 xl:grid-cols-[380px_minmax(0,1fr)]" aria-busy>
          <Skeleton className="h-96" />
          <Skeleton className="h-96" />
        </div>
      ) : error ? (
        <Offline onRetry={() => refetch()} retrying={isFetching} />
      ) : tab === 'agents' ? (
        <AgentConsole events={events} streamState={streamState} />
      ) : all.length === 0 ? (
        <div className={`${panel} flex flex-col items-center gap-3 px-6 py-16 text-center`}>
          <span className="grid size-12 place-items-center rounded-xl bg-line/60 text-faint">
            <Radio className="size-6" aria-hidden />
          </span>
          <h2 className="text-base font-semibold text-ink">No incidents yet</h2>
          <p className="max-w-md text-sm text-muted">
            The agents are running but have not found anything yet. Run a search for a country or zone on the Agents tab and the tickets will
            appear here.
          </p>
          <button type="button" onClick={() => setTab('agents')} className="inline-flex items-center gap-1 text-sm font-medium text-orange-200 hover:underline">
            Open the Agents tab <ArrowUpRight className="size-4" aria-hidden />
          </button>
        </div>
      ) : (
        <div className="grid items-start gap-4 xl:grid-cols-[380px_minmax(0,1fr)]">
          <div className={`${panel} p-2 xl:sticky xl:top-24`}>
            <div role="tablist" aria-label="Satellite status" className="mb-2 flex gap-1 overflow-x-auto p-1">
              {FILTERS.map((f) => (
                <button
                  key={f.id}
                  role="tab"
                  type="button"
                  aria-selected={filter === f.id}
                  title={f.title}
                  onClick={() => setFilter(f.id)}
                  className={`relative whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                    filter === f.id ? 'text-orange-200' : 'text-muted hover:text-ink'
                  }`}
                >
                  {filter === f.id && (
                    <motion.span
                      layoutId="social-tab"
                      className="absolute inset-0 rounded-lg border border-fire/30 bg-fire/15"
                      transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                    />
                  )}
                  <span className="relative">
                    {f.label} <span className="tabular-nums opacity-70">{countOf(f.id)}</span>
                  </span>
                </button>
              ))}
            </div>

            {todo.length > 0 && (
              <div className="mx-1 mb-2 rounded-lg border border-line bg-sidebar/60 px-3 py-2 text-xs text-muted">
                {confirmBulk ? (
                  <span className="flex flex-wrap items-center gap-2">
                    File {batch.length} {batch.length === 1 ? 'alert' : 'alerts'} for review?
                    <button type="button" onClick={reportBatch} className="rounded-md bg-fire px-2.5 py-1 font-semibold text-[#1a0d02] hover:bg-orange-400">
                      Yes, report {batch.length}
                    </button>
                    <button type="button" onClick={() => setConfirmBulk(false)} className="rounded-md px-2 py-1 hover:text-ink">
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => setConfirmBulk(true)}
                    disabled={Boolean(saving)}
                    className="inline-flex items-center gap-1.5 font-medium text-orange-200 hover:underline disabled:opacity-60"
                  >
                    <MapPinPlus className="size-3.5" aria-hidden />
                    {saving === 'bulk' ? 'Reporting…' : `Report ${todo.length > BULK_MAX ? `the first ${BULK_MAX} of ${todo.length}` : `all ${todo.length}`} not yet reported`}
                  </button>
                )}
              </div>
            )}

            {shown.length === 0 ? (
              <p className="px-3 py-8 text-center text-sm text-muted">No tickets with this status.</p>
            ) : (
              <ul className="flex max-h-[calc(100vh-18rem)] flex-col gap-1 overflow-y-auto p-1">
                {shown.map((t) => (
                  <TicketRow key={t.id} ticket={t} report={reportOf(t, reports)} active={t.id === selected?.id} onSelect={setSelectedId} />
                ))}
              </ul>
            )}
          </div>

          {selected ? (
            <TicketDetail
              ticket={selected}
              report={reportOf(selected, reports)}
              saving={saving === selected.id || saving === 'bulk'}
              blocked={blocked}
              onReport={report}
            />
          ) : (
            <div className={`${panel} grid place-items-center px-6 py-20 text-center text-sm text-muted`}>Select a ticket to see its sources.</div>
          )}
        </div>
      )}
    </>
  )
}
