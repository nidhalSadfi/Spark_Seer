import { AnimatePresence, motion } from 'motion/react'
import { TrendingDown, TrendingUp } from 'lucide-react'
import { Skeleton } from '../ui/primitives'
import { MONTHS } from '../../utils/stats'
import { fmtPct, anomalyTone } from '../../utils/format'

/** Anomaly alerts: regions whose latest month deviates notably from the historical median. */
export default function RecentActivity({ summaries, threshold = 20 }) {
  if (!summaries) return <Skeleton className="h-48 w-full" />
  const alerts = summaries
    .filter((r) => r.anomaly != null && Math.abs(r.anomaly) >= threshold)
    .sort((a, b) => Math.abs(b.anomaly) - Math.abs(a.anomaly))
    .slice(0, 5)

  if (!alerts.length) {
    return <p className="text-sm text-muted">No region deviates more than ±{threshold}% from its historical median.</p>
  }
  return (
    <ul className="flex flex-col gap-2.5">
      <AnimatePresence initial={false}>
        {alerts.map((r) => {
          const Icon = r.anomaly > 0 ? TrendingUp : TrendingDown
          return (
            <motion.li
              key={r.id}
              layout
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              className="flex items-start gap-3 rounded-xl border border-line bg-sidebar/60 p-3 text-sm"
            >
              <Icon className={`mt-0.5 size-4 shrink-0 ${anomalyTone(r.anomaly)}`} aria-hidden />
              <div>
                <p className="text-ink">
                  {r.name} <span className={`font-semibold tabular-nums ${anomalyTone(r.anomaly)}`}>{fmtPct(r.anomaly)}</span>
                </p>
                <p className="text-xs text-muted">
                  vs. historical median for {MONTHS[r.month]} {r.year}
                </p>
              </div>
            </motion.li>
          )
        })}
      </AnimatePresence>
    </ul>
  )
}
