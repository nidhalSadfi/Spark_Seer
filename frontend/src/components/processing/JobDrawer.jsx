import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'motion/react'
import { ChevronRight, CircleCheck, CircleX, X } from 'lucide-react'
import { Badge, Skeleton } from '../ui/primitives'
import ReviewPanel from './ReviewPanel'
import { useProcessingJob } from '../../hooks/useProcessing'
import { fmtInt } from '../../utils/format'
import { fmtKey, fmtUtc, jobDetailView, processingUnavailable, statusLabel } from '../../utils/processing'

function Section({ title, children, aside }) {
  return (
    <section className="mt-5">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h3 className="font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-fire/80">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  )
}

function Json({ value }) {
  return (
    <pre className="mt-1.5 max-h-72 overflow-auto rounded-lg border border-line bg-bg/80 p-2.5 font-mono text-[11px] leading-relaxed text-muted">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

function Expandable({ label, children, className = '' }) {
  const [open, setOpen] = useState(false)
  return (
    <div className={className}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="inline-flex items-center gap-1 text-xs text-info hover:text-sky-200">
        <ChevronRight className={`size-3.5 transition-transform ${open ? 'rotate-90' : ''}`} aria-hidden /> {label}
      </button>
      {open && children}
    </div>
  )
}

function Gates({ gates }) {
  if (!gates.results.length) return <p className="text-sm text-muted">No gate results (the job made no decision).</p>
  return (
    <ul className="flex flex-col gap-2">
      {gates.results.map((g) => (
        <li key={g.name} className="rounded-xl border border-line bg-sidebar/60 p-3">
          <div className="flex items-center gap-2 text-sm">
            {g.passed ? <CircleCheck className="size-4 text-ok" aria-hidden /> : <CircleX className="size-4 text-fire-high" aria-hidden />}
            <span className="font-mono text-xs text-faint">{g.short}</span>
            <span className="text-ink">{g.label}</span>
            <Badge tone={g.passed ? 'ok' : 'fire'} className="ml-auto">
              {g.passed ? 'pass' : 'fail'}
            </Badge>
          </div>
          {g.failures.length > 0 && <p className="mt-1 text-xs text-orange-200">Failures: {g.failures.join(', ')}</p>}
          {g.detail && Object.keys(g.detail).length > 0 && (
            <Expandable label="Details" className="mt-1">
              <Json value={g.detail} />
            </Expandable>
          )}
        </li>
      ))}
    </ul>
  )
}

function SoftFlags({ flags }) {
  if (!flags.length) return <p className="text-sm text-muted">No soft flags.</p>
  return (
    <ul className="flex flex-col gap-2">
      {flags.map((f) => (
        <li key={f.flag} className="rounded-xl border border-line bg-sidebar/60 p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-yellow-100">{f.flag}</span>
            {f.rows != null && <Badge tone="warn">{fmtInt(f.rows)} rows</Badge>}
          </div>
          {f.text && <p className="mt-1 text-xs text-muted">{f.text}</p>}
          {f.keys.length > 0 && (
            <p className="mt-1 text-[11px] text-faint">
              e.g. {f.keys.slice(0, 4).map((k) => `${fmtKey(k).period} ${fmtKey(k).place}`).join('; ')}
              {f.rows != null && f.rows > f.keys.length && ` (${f.keys.length} of ${fmtInt(f.rows)} keys shown by the worker)`}
            </p>
          )}
          {f.extra && (
            <Expandable label="Details" className="mt-1">
              <Json value={f.extra} />
            </Expandable>
          )}
        </li>
      ))}
    </ul>
  )
}

function Steps({ steps }) {
  if (!steps.length) return <p className="text-sm text-muted">No steps recorded.</p>
  const max = Math.max(...steps.map((s) => s.durationS ?? 0), 0.001)
  return (
    <ol className="relative flex flex-col gap-1.5 border-l border-line pl-4">
      {steps.map((s) => (
        <li key={s.id} className="relative">
          <span
            aria-hidden
            className={`absolute -left-[21px] top-2 size-2.5 rounded-full border-2 border-bg ${
              s.tone === 'ok' ? 'bg-ok' : s.tone === 'fire' ? 'bg-fire-high' : s.tone === 'info' ? 'bg-info' : 'bg-faint'
            }`}
          />
          <div className="flex items-center gap-2 text-sm">
            <span className="font-mono text-xs text-ink">{s.name}</span>
            {s.status !== 'succeeded' && <Badge tone={s.tone}>{s.status}</Badge>}
            <span className="ml-auto whitespace-nowrap text-xs tabular-nums text-muted">{s.duration}</span>
          </div>
          <div className="mt-1 h-1 overflow-hidden rounded-full bg-line/60" aria-hidden>
            <div className="h-full rounded-full bg-gradient-to-r from-fire-mid to-fire" style={{ width: `${Math.max(1, ((s.durationS ?? 0) / max) * 100)}%` }} />
          </div>
          {s.summary && (
            <Expandable label="Summary" className="mt-0.5">
              <Json value={s.summary} />
            </Expandable>
          )}
        </li>
      ))}
    </ol>
  )
}

function Detail({ job }) {
  const v = jobDetailView(job)
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={v.statusTone}>{statusLabel(v.status)}</Badge>
        {v.decision.value && <Badge tone={v.decision.tone}>decision: {v.decision.value}</Badge>}
        <span className="text-xs text-faint">
          {v.trigger} · started {fmtUtc(v.startedAt ?? v.createdAt)} · {v.duration}
        </span>
      </div>
      {v.error && <p className="mt-3 rounded-lg border border-fire/40 bg-fire/10 px-3 py-2 text-sm text-orange-200">Error: {v.error}</p>}

      <Section title="Decision">
        <div className="rounded-xl border border-line bg-sidebar/60 p-3 text-sm">
          <p className="text-ink">
            {v.decision.value ? (
              <>
                <Badge tone={v.decision.tone}>{v.decision.value}</Badge>
                <span className="ml-2 font-mono text-xs text-faint">basis: {v.decision.basis ?? '—'}</span>
              </>
            ) : (
              'No decision'
            )}
          </p>
          <p className="mt-1.5 text-muted">{v.decision.basisText}</p>
          <p className="mt-1 text-xs text-faint">
            Hard gates: {v.decision.gatesPassed == null ? 'not run' : v.decision.gatesPassed ? 'all passed' : 'at least one failed'}
            {v.datasetVersion ? ` · published as dataset version ${v.datasetVersion.id} (${v.datasetVersion.status})` : ' · nothing published'}
          </p>
        </div>
      </Section>

      <div className="mt-5">
        <ReviewPanel review={v.review} />
      </div>

      <Section title="Hard gates G1-G4" aside={v.gates.passed != null && <Badge tone={v.gates.passed ? 'ok' : 'fire'}>{v.gates.passed ? 'all passed' : 'failed'}</Badge>}>
        <Gates gates={v.gates} />
      </Section>

      <Section title="Soft flags">
        <SoftFlags flags={v.softFlags} />
      </Section>

      <Section title="Counts">
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {v.counts.map((c) => (
            <div key={c.key} className="rounded-xl border border-line bg-sidebar/60 px-3 py-2">
              <dt className="text-[10px] uppercase tracking-wider text-faint">{c.label}</dt>
              <dd className="text-lg font-semibold tabular-nums text-ink">{fmtInt(c.value)}</dd>
            </div>
          ))}
        </dl>
      </Section>

      <Section title="Steps" aside={<span className="text-[11px] text-faint">{v.steps.length} steps · bars scaled to the slowest</span>}>
        <Steps steps={v.steps} />
      </Section>
    </>
  )
}

/** Right-hand drawer with one job's detail (GET /processing/jobs/{id}); portal so no page transform clips it. */
export default function JobDrawer({ jobId, onClose }) {
  const { data, error, isPending } = useProcessingJob(jobId)
  const panel = useRef(null)

  useEffect(() => {
    if (jobId == null) return undefined
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    panel.current?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [jobId, onClose])

  return createPortal(
    <AnimatePresence>
      {jobId != null && (
        <>
          <motion.div
            className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.aside
            ref={panel}
            tabIndex={-1}
            role="dialog"
            aria-modal="true"
            aria-label={`Processing job ${jobId}`}
            className="fixed inset-y-0 right-0 z-50 w-full max-w-3xl overflow-y-auto border-l border-line bg-gradient-to-b from-card to-bg p-5 shadow-2xl outline-none sm:p-6"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 380, damping: 38 }}
          >
            <div className="mb-3 flex items-start justify-between gap-3">
              <div>
                <p className="font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-fire/80">Job detail</p>
                <h2 className="text-xl font-semibold text-ink">
                  Job #{jobId}
                  {data && <span className="ml-2 text-sm font-normal text-muted">{data.kind}</span>}
                </h2>
              </div>
              <button type="button" onClick={onClose} aria-label="Close job detail" className="rounded-lg p-1 text-muted hover:text-ink">
                <X className="size-5" />
              </button>
            </div>
            {isPending && (
              <div className="flex flex-col gap-3">
                <Skeleton className="h-16" />
                <Skeleton className="h-64" />
                <Skeleton className="h-40" />
              </div>
            )}
            {error && (
              <p className="rounded-lg border border-fire/40 bg-fire/10 px-3 py-2 text-sm text-orange-200">
                {processingUnavailable(error)
                  ? 'Processing history is not available right now.'
                  : 'Could not load this update. Try again in a moment.'}
              </p>
            )}
            {data && <Detail job={data} />}
          </motion.aside>
        </>
      )}
    </AnimatePresence>,
    document.body,
  )
}
