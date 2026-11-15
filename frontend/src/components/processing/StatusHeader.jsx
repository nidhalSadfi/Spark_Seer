import { AlertTriangle, CalendarClock, CircleCheck, Database, Layers } from 'lucide-react'
import { Badge, Card, CardHeader, Skeleton } from '../ui/primitives'
import { fmtInt } from '../../utils/format'
import { DECISION_TONE, STATUS_TONE, cadenceLabel, fmtUtc, kindLabel, relTime, statusLabel } from '../../utils/processing'

function Row({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line/50 py-1.5 text-sm last:border-0">
      <span className="shrink-0 text-xs text-faint">{label}</span>
      <span className="min-w-0 text-right text-muted">{children}</span>
    </div>
  )
}

function When({ iso, now }) {
  if (!iso) return <span>—</span>
  return (
    <span title={fmtUtc(iso)}>
      {relTime(iso, now)} <span className="text-faint">· {fmtUtc(iso)}</span>
    </span>
  )
}

/** One scheduler kind: cadence, last run, next run (overdue?), active job, last success and error. */
function KindCard({ k, now, onOpenJob, delay }) {
  const last = k.last_run
  return (
    <Card delay={delay}>
      <CardHeader
        kicker="Scheduler"
        title={kindLabel(k.kind)}
        subtitle={`${k.kind} · ${cadenceLabel(k)}`}
        action={
          <div className="flex flex-wrap justify-end gap-1.5">
            {!k.enabled && <Badge>disabled</Badge>}
            {k.overdue && (
              <Badge tone="fire" title="next_run_at has passed and no worker has claimed the run">
                overdue
              </Badge>
            )}
            {k.active_job && (
              <Badge tone="info">
                <span className="size-1.5 rounded-full bg-info motion-safe:animate-pulse" aria-hidden /> job #{k.active_job.id} {k.active_job.status}
              </Badge>
            )}
          </div>
        }
      />
      <Row label="Last run">
        {last ? (
          <button type="button" onClick={() => onOpenJob(last.id)} className="inline-flex flex-wrap items-center justify-end gap-1.5 hover:text-ink">
            <span className="font-mono text-xs">#{last.id}</span>
            <Badge tone={STATUS_TONE[last.status]}>{statusLabel(last.status)}</Badge>
            {last.decision && <Badge tone={DECISION_TONE[last.decision]}>{last.decision}</Badge>}
            <When iso={last.finished_at ?? last.created_at} now={now} />
          </button>
        ) : (
          'never'
        )}
      </Row>
      <Row label="Next run">{k.enabled ? <When iso={k.next_run_at} now={now} /> : 'not scheduled (disabled)'}</Row>
      <Row label="Last success">
        {k.last_success ? (
          <>
            <span className="font-mono text-xs">#{k.last_success.job_id}</span> <When iso={k.last_success.finished_at} now={now} />
          </>
        ) : (
          'none yet'
        )}
      </Row>
      <Row label="Last error">
        {k.last_error ? (
          <button type="button" onClick={() => onOpenJob(k.last_error.job_id)} className="text-orange-200 hover:text-ink">
            #{k.last_error.job_id} {k.last_error.status}
            {k.last_error.error ? `: ${k.last_error.error}` : ''}
          </button>
        ) : (
          'none'
        )}
      </Row>
    </Card>
  )
}

function Metric({ icon: Icon, label, value, hint }) {
  return (
    <div className="rounded-xl border border-line bg-sidebar/60 p-3">
      <p className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-faint">
        <Icon className="size-3.5" aria-hidden /> {label}
      </p>
      <p className="mt-0.5 text-xl font-semibold tabular-nums text-ink">{value}</p>
      {hint && <p className="mt-0.5 truncate text-[11px] text-faint" title={hint}>{hint}</p>}
    </div>
  )
}

/** Status header of the Processing Center (GET /processing/status). */
export default function StatusHeader({ status, now, onOpenJob }) {
  if (!status) {
    return (
      <div className="grid gap-4 lg:grid-cols-3">
        <Skeleton className="h-48" />
        <Skeleton className="h-48" />
        <Skeleton className="h-48" />
      </div>
    )
  }
  const ds = status.approved?.dataset_version
  const cal = status.approved?.calibration_version
  const months = Array.isArray(ds?.summary?.months) ? ds.summary.months : null
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 lg:grid-cols-3">
        {status.scheduler.kinds.map((k, i) => (
          <KindCard key={k.kind} k={k} now={now} onOpenJob={onOpenJob} delay={i * 0.05} />
        ))}
        <Card delay={0.1}>
          <CardHeader kicker="Published" title="Approved versions" subtitle="What /calendar serves right now" />
          <Row label="Dataset version">
            {ds ? (
              <>
                <span className="font-mono text-xs text-ink">v{ds.id}</span>
                {ds.job_id != null ? (
                  <button type="button" onClick={() => onOpenJob(ds.job_id)} className="ml-1.5 hover:text-ink">
                    from job #{ds.job_id}
                  </button>
                ) : (
                  <span className="ml-1.5">{ds.summary?.source ?? 'bootstrap'}</span>
                )}
              </>
            ) : (
              'none (not bootstrapped)'
            )}
          </Row>
          {ds && <Row label="Approved">{ds.approved_at ? <When iso={ds.approved_at} now={now} /> : '—'}</Row>}
          {months && <Row label="Months in this version">{months.join(', ')}</Row>}
          <Row label="Calibration version">
            {cal ? (
              <>
                <span className="font-mono text-xs text-ink">v{cal.id}</span> {cal.label}
              </>
            ) : (
              'none'
            )}
          </Row>
          {cal && (
            <Row label="Inter-VIIRS ratio (r)">
              {cal.r_global != null ? cal.r_global.toFixed(3) : '—'}
              {cal.snpp_end && <span className="text-faint"> · S-NPP end {cal.snpp_end}</span>}
            </Row>
          )}
        </Card>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric icon={Layers} label="Raw detections" value={fmtInt(status.data?.raw_detections)} hint="Stored NRT FIRMS detections" />
        <Metric icon={CalendarClock} label="Pending" value={fmtInt(status.data?.pending_detections)} hint="Detections waiting for a run" />
        <Metric icon={Database} label="Approved cell-months" value={fmtInt(status.data?.approved_cell_months)} hint="Rows behind the calendar" />
        <Metric
          icon={status.last_error ? AlertTriangle : CircleCheck}
          label="Last error"
          value={status.last_error ? `#${status.last_error.job_id}` : 'none'}
          hint={
            status.last_error
              ? `${status.last_error.status}${status.last_error.error ? `: ${status.last_error.error}` : ''} · ${fmtUtc(status.last_error.finished_at)}`
              : 'No failed, abandoned or rejected job'
          }
        />
      </div>
    </div>
  )
}
