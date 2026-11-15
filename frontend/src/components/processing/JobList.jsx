import { Badge, Card, CardHeader, Select, Skeleton } from '../ui/primitives'
import { fmtInt } from '../../utils/format'
import {
  AI_REVIEW,
  DECISION_TONE,
  JOB_KINDS,
  JOB_STATUSES,
  STATUS_TONE,
  fmtDuration,
  fmtUtc,
  kindLabel,
  relTime,
  statusLabel,
} from '../../utils/processing'

const KIND_OPTIONS = [{ value: 'all', label: 'All kinds' }, ...JOB_KINDS.map((k) => ({ value: k, label: kindLabel(k) }))]
const STATUS_OPTIONS = [{ value: 'all', label: 'All statuses' }, ...JOB_STATUSES.map((s) => ({ value: s, label: statusLabel(s) }))]

const th = 'px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-muted'
const td = 'px-3 py-2.5 align-top'

function CountsCell({ counts }) {
  if (!counts) return '—'
  const parts = [
    ['fetched', counts.fetched],
    ['new', counts.new],
    ['affected', counts.affected],
    ['staged', counts.staged],
  ].filter(([, v]) => v != null)
  if (!parts.length) return <span className="text-faint">—</span>
  return (
    <span className="whitespace-nowrap text-xs">
      {parts.map(([k, v], i) => (
        <span key={k}>
          {i > 0 && <span className="text-faint"> · </span>}
          <span className="text-ink">{fmtInt(v)}</span> <span className="text-faint">{k}</span>
        </span>
      ))}
    </span>
  )
}

/** Paginated job list with kind and status filters (GET /processing/jobs). Rows open the drawer. */
export default function JobList({ query, filters, setFilters, now, selectedId, onOpen }) {
  const { data, isPending, isFetching, error } = query
  const pages = data ? Math.max(1, Math.ceil(data.total / filters.pageSize)) : 1
  const set = (patch) => setFilters({ ...filters, page: 0, ...patch })

  return (
    <Card className="!p-0">
      <div className="p-5 pb-3">
        <CardHeader
          kicker="History"
          title="Processing jobs"
          subtitle="Newest first. Select a job to see its steps, gates and the AI review."
          action={<Badge>{isFetching && !data ? 'Loading…' : `${fmtInt(data?.total ?? 0)} jobs`}</Badge>}
        />
        <div className="flex flex-wrap items-end gap-3">
          <Select label="Kind" value={filters.kind} onChange={(v) => set({ kind: v })} options={KIND_OPTIONS} />
          <Select label="Status" value={filters.status} onChange={(v) => set({ status: v })} options={STATUS_OPTIONS} />
        </div>
      </div>
      {error && <p className="px-5 pb-3 text-sm text-fire-high">Could not load jobs: {error.message}</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-y border-line">
              <th className={th}>Job</th>
              <th className={th}>Kind</th>
              <th className={th}>Status</th>
              <th className={th}>Decision</th>
              <th className={th}>AI review</th>
              <th className={th}>Counts</th>
              <th className={th}>Duration</th>
              <th className={th}>Time</th>
            </tr>
          </thead>
          <tbody>
            {isPending
              ? Array.from({ length: 4 }).map((_, i) => (
                  <tr key={i}>
                    <td colSpan={8} className="px-3 py-2">
                      <Skeleton className="h-6 w-full" />
                    </td>
                  </tr>
                ))
              : data?.jobs.map((j) => {
                  const active = j.id === selectedId
                  return (
                    <tr
                      key={j.id}
                      onClick={() => onOpen(j.id)}
                      className={`cursor-pointer border-b border-line/60 text-muted transition-colors hover:bg-fire/[0.06] ${active ? 'bg-fire/10' : ''}`}
                    >
                      <td className={td}>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation()
                            onOpen(j.id)
                          }}
                          className="font-mono text-xs font-semibold text-ink hover:text-orange-200"
                          aria-label={`Open job ${j.id}`}
                        >
                          #{j.id}
                        </button>
                        {j.trigger !== 'schedule' && <span className="ml-1.5 text-[11px] text-faint">{j.trigger}</span>}
                      </td>
                      <td className={`${td} whitespace-nowrap`}>{kindLabel(j.kind)}</td>
                      <td className={td}>
                        <Badge tone={STATUS_TONE[j.status]}>{statusLabel(j.status)}</Badge>
                      </td>
                      <td className={td}>{j.decision ? <Badge tone={DECISION_TONE[j.decision]}>{j.decision}</Badge> : <span className="text-faint">—</span>}</td>
                      <td className={td}>
                        {j.ai_review ? (
                          <Badge tone={AI_REVIEW[j.ai_review]?.tone} title={AI_REVIEW[j.ai_review]?.text}>
                            {j.ai_review}
                          </Badge>
                        ) : (
                          <span className="text-faint">—</span>
                        )}
                      </td>
                      <td className={td}>
                        <CountsCell counts={j.counts} />
                      </td>
                      <td className={`${td} whitespace-nowrap tabular-nums`}>{fmtDuration(j.duration_s)}</td>
                      <td className={`${td} whitespace-nowrap text-xs`} title={fmtUtc(j.created_at)}>
                        {relTime(j.created_at, now)}
                      </td>
                    </tr>
                  )
                })}
            {data && !data.jobs.length && (
              <tr>
                <td colSpan={8} className="px-3 py-10 text-center text-muted">
                  No jobs match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between gap-3 border-t border-line px-5 py-3 text-sm text-muted">
        <span>
          Page {filters.page + 1} of {pages}
        </span>
        <div className="flex gap-2">
          <button
            onClick={() => setFilters({ ...filters, page: filters.page - 1 })}
            disabled={filters.page === 0}
            className="h-8 rounded-lg border border-line px-3 hover:text-ink disabled:opacity-40"
          >
            Previous
          </button>
          <button
            onClick={() => setFilters({ ...filters, page: filters.page + 1 })}
            disabled={filters.page + 1 >= pages}
            className="h-8 rounded-lg border border-line px-3 hover:text-ink disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>
    </Card>
  )
}
