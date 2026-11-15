import { Link } from 'react-router-dom'
import { ArrowUpRight, Flame, MapPin } from 'lucide-react'
import { Badge } from '../ui/primitives'
import ReportIntel from './ReportIntel'
import { STATUS, fmtLatLon, fmtLocal, observedLabel, timeAgo } from '../../utils/reports'

function Section({ children, className = '' }) {
  return <div className={`border-t border-line py-3.5 ${className}`}>{children}</div>
}

/** Map-page side panel for a selected community report. Confirming happens in Fire Reports. */
export default function ReportDetails({ report, className = '' }) {
  const confirmed = report.status === 'confirmed'
  const st = STATUS[report.status]

  return (
    <aside
      aria-label="Selected community report"
      className={`flex min-w-0 flex-col rounded-2xl border border-line bg-gradient-to-br from-card to-[#131f31] p-4 ${className}`}
    >
      <div className="flex items-start gap-2.5 rounded-xl border border-line bg-sidebar/70 px-3 py-2.5">
        <MapPin className="mt-0.5 size-4 shrink-0 text-info" aria-hidden />
        <div className="min-w-0">
          <p className="truncate text-[15px] font-semibold text-ink">Community alert</p>
          <p className="font-mono text-[11px] text-faint">{fmtLatLon(report.lat, report.lon)}</p>
        </div>
      </div>
      <p className="mt-2.5 px-0.5 text-[11.5px] text-faint">
        Reported <span className="text-muted">{timeAgo(report.createdAt)}</span> · {fmtLocal(report.createdAt)}
      </p>

      <Section className="mt-3 flex items-center gap-2.5">
        {confirmed ? (
          <span className="grid size-9 place-items-center rounded-lg bg-fire/15 text-fire">
            <Flame className="size-5" aria-hidden />
          </span>
        ) : (
          <span className="grid size-9 place-items-center rounded-full border-2 border-dashed border-yellow-300 bg-yellow-300/10 text-lg font-extrabold leading-none text-yellow-200">
            ?
          </span>
        )}
        <p className="min-w-0 text-sm leading-tight text-ink">
          <b className="font-semibold">{st.label}</b>
          <span className="block text-xs text-muted">
            {confirmed ? `Confirmed ${timeAgo(report.reviewedAt)}` : 'Waiting for someone to confirm it'}
          </span>
        </p>
        <Badge tone={confirmed ? 'fire' : 'neutral'} className="ml-auto">
          Community report
        </Badge>
      </Section>

      <Section>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-[12.5px]">
          <dt className="text-muted">What was seen</dt>
          <dd className="text-right font-medium text-sky-300">{observedLabel(report.observed)}</dd>
          <dt className="text-muted">Source</dt>
          <dd className="text-right font-medium text-sky-300">Human report, not a satellite detection</dd>
        </dl>
        {report.note && <p className="mt-3 rounded-lg bg-sidebar/70 px-3 py-2 text-[12.5px] leading-relaxed text-muted">“{report.note}”</p>}
      </Section>

      {confirmed ? (
        <Section>
          <ReportIntel report={report} />
        </Section>
      ) : (
        <Section>
          <p className="mb-3 text-xs leading-relaxed text-muted">
            Until it is confirmed this stays a <b className="text-yellow-200">?</b> on the map, and no weather lookup is made. Confirming it
            shows it as a fire and fetches the weather for these coordinates.
          </p>
        </Section>
      )}

      <Link
        to={`/dashboard/reports?report=${encodeURIComponent(report.id)}`}
        className="mt-auto inline-flex h-9 items-center justify-center gap-1.5 rounded-lg border border-line text-sm text-ink transition-colors hover:border-fire/40 hover:text-orange-200"
      >
        {confirmed ? 'Open in Fire Reports' : 'Review this alert'} <ArrowUpRight className="size-4" aria-hidden />
      </Link>
    </aside>
  )
}
