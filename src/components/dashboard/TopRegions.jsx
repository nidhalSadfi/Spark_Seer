import { Skeleton } from '../ui/primitives'
import { fmtInt, fmtPct, anomalyTone } from '../../utils/format'

export default function TopRegions({ summaries, limit = 5 }) {
  if (!summaries) return <Skeleton className="h-48 w-full" />
  const top = [...summaries].sort((a, b) => b.current - a.current).slice(0, limit)
  const max = top[0]?.current || 1
  return (
    <ol className="flex flex-col gap-3">
      {top.map((r, i) => (
        <li key={r.id}>
          <div className="flex items-baseline justify-between gap-2 text-sm">
            <span className="text-ink">
              <span className="mr-2 text-faint tabular-nums">{i + 1}</span>
              {r.name}
            </span>
            <span className="tabular-nums text-muted">
              {fmtInt(r.current)} <span className={`ml-1 text-xs ${anomalyTone(r.anomaly)}`}>{fmtPct(r.anomaly)}</span>
            </span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-line">
            <div className="h-full rounded-full bg-gradient-to-r from-fire-mid to-fire" style={{ width: `${(r.current / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ol>
  )
}
