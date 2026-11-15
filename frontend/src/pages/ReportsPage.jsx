import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { motion } from 'motion/react'
import { toast } from 'sonner'
import { AlertTriangle, ArrowUpRight, Check, CircleX, Flame, HardDrive, Inbox, MapPinPlus, RotateCcw, SatelliteDish, Trash2, X } from 'lucide-react'
import { Badge, Notice, PageHeader, Skeleton } from '../components/ui/primitives'
import ReportIntel from '../components/reports/ReportIntel'
import ReportMiniMap from '../components/reports/ReportMiniMap'
import { useActiveFires } from '../hooks/useFireData'
import { useReports } from '../hooks/useReports'
import { notifyReportError } from '../components/reports/notifyError'
import { STATUS, fmtLatLon, fmtLocal, observedLabel, satelliteCheck, timeAgo } from '../utils/reports'

const TABS = [
  { id: 'pending', label: 'To review' },
  { id: 'confirmed', label: 'Confirmed' },
  { id: 'false', label: 'False alarms' },
  { id: 'all', label: 'All' },
]

const panel = 'rounded-2xl border border-line bg-gradient-to-br from-card to-[#131f31]'

function StatusIcon({ status, className = '' }) {
  if (status === 'confirmed') {
    return (
      <span className={`grid size-9 shrink-0 place-items-center rounded-lg bg-fire/15 text-fire ${className}`}>
        <Flame className="size-5" aria-hidden />
      </span>
    )
  }
  if (status === 'false') {
    return (
      <span className={`grid size-9 shrink-0 place-items-center rounded-lg bg-line/60 text-faint ${className}`}>
        <CircleX className="size-5" aria-hidden />
      </span>
    )
  }
  return (
    <span
      className={`grid size-9 shrink-0 place-items-center rounded-full border-2 border-dashed border-yellow-300 bg-yellow-300/10 text-lg font-extrabold leading-none text-yellow-200 ${className}`}
    >
      ?
    </span>
  )
}

function ReportRow({ report, active, onSelect }) {
  const st = STATUS[report.status]
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(report.id)}
        aria-current={active ? 'true' : undefined}
        className={`flex w-full items-start gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors ${
          active ? 'border-fire/50 bg-fire/10' : 'border-transparent hover:bg-card-hi/70'
        }`}
      >
        <StatusIcon status={report.status} />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <b className="text-sm font-semibold text-ink">{observedLabel(report.observed)}</b>
            <Badge tone={st.tone}>{st.short}</Badge>
          </span>
          <span className="mt-0.5 block font-mono text-[11px] text-faint">{fmtLatLon(report.lat, report.lon)}</span>
          <span className="mt-0.5 block truncate text-xs text-muted">{report.note || 'No note'}</span>
        </span>
        <span className="shrink-0 text-[11px] text-faint">{timeAgo(report.createdAt)}</span>
      </button>
    </li>
  )
}

/** Hotspots the satellite feed shows around the reported point: the evidence a reviewer weighs. */
function SatelliteCheck({ report, feed, feedError }) {
  const check = satelliteCheck(report, feed)
  if (!check && feedError) {
    return (
      <p className="rounded-xl border border-line bg-sidebar/60 px-3.5 py-3 text-xs text-muted">
        The satellite feed could not be loaded: {feedError.message}
      </p>
    )
  }
  if (!check) return <Skeleton className="h-16" />
  const n = check.detections.length
  return (
    <div className="flex items-start gap-3 rounded-xl border border-line bg-sidebar/60 px-3.5 py-3">
      <SatelliteDish className={`mt-0.5 size-5 shrink-0 ${n ? 'text-fire' : 'text-faint'}`} aria-hidden />
      <div className="min-w-0 text-[13px] leading-relaxed text-muted">
        {n > 0 ? (
          <p>
            <b className="font-semibold text-ink">
              {n} satellite hotspot {n === 1 ? 'detection' : 'detections'}
            </b>{' '}
            within {check.radiusKm} km in the last 24 h. That supports the report.
          </p>
        ) : (
          <p>
            <b className="font-semibold text-ink">No satellite hotspots</b> within {check.radiusKm} km in the last 24 h. Satellites pass only a few
            times a day and can miss small fires, so this does not rule the report out.
          </p>
        )}
        {check.nearest && (
          <p className="text-xs text-faint">
            Nearest mapped fire: {check.nearest.name}, {check.nearest.km < 10 ? check.nearest.km.toFixed(1) : Math.round(check.nearest.km)} km away.
          </p>
        )}
        {feed.meta?.source === 'mock' ? (
          <p className="mt-1 text-[11px] text-yellow-200/90">
            The live fire feed is unreachable, so this compares against illustrative sample fires, not real detections.
          </p>
        ) : (
          <p className="mt-1 text-[11px] text-faint">NASA FIRMS near-real-time detections for the area selected in the filters.</p>
        )}
      </div>
    </div>
  )
}

function DeleteButton({ onDelete }) {
  const [asking, setAsking] = useState(false)
  if (asking) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-muted">
        Delete this report?
        <button type="button" onClick={onDelete} className="rounded-md bg-fire-high/20 px-2 py-1 font-medium text-red-200 hover:bg-fire-high/30">
          Delete
        </button>
        <button type="button" onClick={() => setAsking(false)} className="rounded-md px-2 py-1 hover:text-ink">
          Keep
        </button>
      </span>
    )
  }
  return (
    <button type="button" onClick={() => setAsking(true)} className="inline-flex items-center gap-1.5 text-xs text-faint hover:text-red-300">
      <Trash2 className="size-3.5" aria-hidden /> Delete
    </button>
  )
}

function ReportReview({ report, feed, feedError, busy, onVerdict, onReopen, onDelete }) {
  const st = STATUS[report.status]
  const reviewed = report.status !== 'pending'

  return (
    <div className={`${panel} flex min-w-0 flex-col gap-4 p-4 sm:p-5`}>
      <div className="flex flex-wrap items-start gap-3">
        <StatusIcon status={report.status} />
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] font-semibold text-ink">
            {observedLabel(report.observed)} reported at <span className="font-mono text-[13px]">{fmtLatLon(report.lat, report.lon)}</span>
          </h2>
          <p className="text-xs text-muted">
            Filed {timeAgo(report.createdAt)} · {fmtLocal(report.createdAt)} · human report, not a satellite detection
          </p>
        </div>
        <Badge tone={st.tone}>{st.label}</Badge>
      </div>

      {/* the decision comes first so it is on screen without scrolling */}
      {!reviewed ? (
        <div className="rounded-xl border border-yellow-300/30 bg-yellow-300/[0.04] p-4">
          <p className="text-sm font-semibold text-ink">Is this a real fire?</p>
          <p className="mt-0.5 text-xs text-muted">
            Check the map and the satellite evidence above. <b className="text-ink">Yes</b> turns the <b className="text-yellow-200">?</b> into a fire
            on the map and looks up the weather for these coordinates. <b className="text-ink">No</b> removes it from the map.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => onVerdict(report, true)}
              disabled={busy}
              className="disabled:opacity-60 inline-flex h-10 items-center gap-2 rounded-lg bg-fire px-4 text-sm font-semibold text-[#1a0d02] transition-colors hover:bg-orange-400"
            >
              <Check className="size-4" aria-hidden /> Yes, it is a fire
            </button>
            <button
              type="button"
              onClick={() => onVerdict(report, false)}
              disabled={busy}
              className="disabled:opacity-60 inline-flex h-10 items-center gap-2 rounded-lg border border-line px-4 text-sm font-medium text-ink transition-colors hover:border-faint hover:bg-card-hi"
            >
              <X className="size-4" aria-hidden /> No, false alarm
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl border border-line bg-sidebar/60 px-4 py-3">
          <p className="text-sm text-muted">
            {report.status === 'confirmed' ? (
              <>
                <b className="font-semibold text-ink">Confirmed as a real fire</b> {timeAgo(report.reviewedAt)}. It is shown as a fire on the map.
              </>
            ) : (
              <>
                <b className="font-semibold text-ink">Dismissed as a false alarm</b> {timeAgo(report.reviewedAt)}. It is hidden from the map.
              </>
            )}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            {report.status === 'confirmed' && (
              <Link
                to={`/dashboard/map?report=${encodeURIComponent(report.id)}`}
                className="inline-flex items-center gap-1 text-xs font-medium text-orange-200 hover:underline"
              >
                View on the map <ArrowUpRight className="size-3.5" aria-hidden />
              </Link>
            )}
            <button type="button" onClick={() => onReopen(report)} disabled={busy} className="disabled:opacity-60 inline-flex items-center gap-1.5 text-xs text-muted hover:text-ink">
              <RotateCcw className="size-3.5" aria-hidden /> Reopen as unverified
            </button>
            <DeleteButton onDelete={() => onDelete(report)} />
          </div>
        </div>
      )}

      {/* the evidence: where it is, and what the satellites see around it */}
      <div className="grid gap-4 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        {/* keyed by id: a fresh map for every report */}
        <ReportMiniMap key={report.id} report={report} feed={feed} height={260} />
        <div className="flex min-w-0 flex-col gap-3">
          {report.note && <p className="rounded-lg bg-sidebar/70 px-3 py-2 text-[13px] leading-relaxed text-muted">“{report.note}”</p>}
          <SatelliteCheck report={report} feed={feed} feedError={feedError} />
        </div>
      </div>

      {!reviewed && (
        <div className="-mt-1 flex justify-end">
          <DeleteButton onDelete={() => onDelete(report)} />
        </div>
      )}

      {report.status === 'confirmed' && (
        <section aria-label="Weather and location info">
          <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-ink">
            <Flame className="size-4 text-fire" aria-hidden /> Weather and location info
          </h3>
          <ReportIntel report={report} />
        </section>
      )}
    </div>
  )
}

/**
 * Review queue for community fire alerts. Alerts are filed from the map and start unverified ("?").
 * Here a reviewer confirms each one as a real fire (it then shows as a fire on the map and its
 * coordinates get a weather lookup) or dismisses it as a false alarm.
 */
export default function ReportsPage() {
  const { reports, counts, source, isLoading, error, reviewReport, reopenReport, deleteReport } = useReports()
  const { data: feed, error: feedError } = useActiveFires()
  const [params] = useSearchParams()
  const [busy, setBusy] = useState(false)

  // opened with ?report=<id> (from the map): start on that report, in the tab it belongs to.
  // The list loads asynchronously, so the tab follows the linked report until one is picked.
  const linkedId = params.get('report')
  const linked = reports.find((r) => r.id === linkedId)
  const [pickedTab, setTab] = useState(null)
  const tab = pickedTab ?? linked?.status ?? 'pending'
  const [selectedId, setSelectedId] = useState(linkedId)

  const list = tab === 'all' ? reports : reports.filter((r) => r.status === tab)
  const selected = list.find((r) => r.id === selectedId) ?? list[0] ?? null
  const countOf = (id) => (id === 'all' ? counts.total : counts[id])

  const run = async (fn) => {
    setBusy(true)
    try {
      return await fn()
    } catch (e) {
      notifyReportError(e)
      return undefined
    } finally {
      setBusy(false)
    }
  }

  const decide = async (report, isFire) => {
    const done = await run(() => reviewReport(report.id, isFire))
    if (!done) return
    setSelectedId(report.id)
    if (tab !== 'all') setTab(isFire ? 'confirmed' : 'false')
    toast(isFire ? 'Confirmed: it now shows as a fire on the map. Fetching its weather…' : 'Dismissed as a false alarm.', {
      action: { label: 'Undo', onClick: () => reopenReport(report.id).catch(notifyReportError) },
    })
  }
  const reopen = async (report) => {
    const done = await run(() => reopenReport(report.id))
    if (!done) return
    setSelectedId(report.id)
    if (tab !== 'all') setTab('pending')
  }
  const remove = async (report) => {
    const done = await run(async () => {
      await deleteReport(report.id)
      return true
    })
    if (done) toast('Report deleted.')
  }

  return (
    <>
      <PageHeader
        kicker="Community"
        title="Fire Reports"
        description="Alerts filed from the map start as unverified (?). Review each one: confirm it as a fire to show it on the map and look up the weather for its coordinates, or dismiss it as a false alarm."
        actions={
          <Link
            to="/dashboard/map"
            className="inline-flex h-9 items-center gap-2 rounded-lg bg-fire px-3 text-sm font-medium text-[#1a0d02] transition-colors hover:bg-orange-400"
          >
            <MapPinPlus className="size-4" aria-hidden /> Report a fire on the map
          </Link>
        }
      />

      <div className="mb-4">
        {source === 'local' ? (
          <Notice icon={HardDrive} title="Saved in this browser only.">
            The shared report list is not available right now, so reports and reviews are kept on this device only: other people and other
            devices do not see them, and they are not shared later. This page switches back to the shared list as soon as it is available.
          </Notice>
        ) : source === 'api' ? (
          <Notice icon={Inbox} tone="info" title="Shared reports.">
            Everyone using Spark Seer sees these reports and their reviews. The list refreshes every 30 seconds.
          </Notice>
        ) : null}
      </div>

      {error && (
        <div className="mb-4">
          <Notice icon={AlertTriangle} title="Could not load the reports.">
            {error.message}
          </Notice>
        </div>
      )}

      {isLoading ? (
        <div className="grid items-start gap-4 xl:grid-cols-[380px_minmax(0,1fr)]" role="status" aria-label="Loading reports">
          <Skeleton className="h-72" />
          <Skeleton className="h-96" />
        </div>
      ) : counts.total === 0 ? (
        <div className={`${panel} flex flex-col items-center gap-3 px-6 py-16 text-center`}>
          <StatusIcon status="pending" className="size-12! text-2xl!" />
          <h2 className="text-base font-semibold text-ink">No reports yet</h2>
          <p className="max-w-md text-sm text-muted">
            Open the map, press the <MapPinPlus className="inline size-4 align-text-bottom text-fire" aria-label="report" /> button, and click where
            you see fire or smoke. The alert will show up here, waiting for review.
          </p>
          <Link to="/dashboard/map" className="mt-1 inline-flex items-center gap-1 text-sm font-medium text-orange-200 hover:underline">
            Go to the map <ArrowUpRight className="size-4" aria-hidden />
          </Link>
        </div>
      ) : (
        <div className="grid items-start gap-4 xl:grid-cols-[380px_minmax(0,1fr)]">
          <div className={`${panel} p-2 xl:sticky xl:top-24`}>
            <div role="tablist" aria-label="Report status" className="mb-2 flex gap-1 overflow-x-auto p-1">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  role="tab"
                  type="button"
                  aria-selected={tab === t.id}
                  onClick={() => setTab(t.id)}
                  className={`relative whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                    tab === t.id ? 'text-orange-200' : 'text-muted hover:text-ink'
                  }`}
                >
                  {tab === t.id && (
                    <motion.span
                      layoutId="reports-tab"
                      className="absolute inset-0 rounded-lg border border-fire/30 bg-fire/15"
                      transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                    />
                  )}
                  <span className="relative">
                    {t.label} <span className="tabular-nums opacity-70">{countOf(t.id)}</span>
                  </span>
                </button>
              ))}
            </div>

            {list.length === 0 ? (
              <p className="px-3 py-8 text-center text-sm text-muted">
                {tab === 'pending' ? 'Nothing is waiting for review.' : tab === 'confirmed' ? 'No confirmed fires yet.' : 'No false alarms.'}
              </p>
            ) : (
              <ul className="flex max-h-[calc(100vh-18rem)] flex-col gap-1 overflow-y-auto p-1">
                {list.map((r) => (
                  <ReportRow key={r.id} report={r} active={r.id === selected?.id} onSelect={setSelectedId} />
                ))}
              </ul>
            )}
          </div>

          {selected ? (
            <ReportReview
              report={selected}
              feed={feed}
              feedError={feedError}
              busy={busy}
              onVerdict={decide}
              onReopen={reopen}
              onDelete={remove}
            />
          ) : (
            <div className={`${panel} grid place-items-center px-6 py-20 text-center text-sm text-muted`}>Select a report to review it.</div>
          )}
        </div>
      )}
    </>
  )
}
