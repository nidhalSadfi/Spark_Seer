import NumberFlow from '@number-flow/react'
import { motion } from 'motion/react'
import { Skeleton } from '../ui/primitives'

export const staggerItem = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.3 } },
}

export default function KPICard({ label, value, icon: Icon, hint, suffix, signed, tone = 'text-ink', loading }) {
  return (
    <motion.div
      variants={staggerItem}
      className="rounded-2xl border border-line bg-gradient-to-br from-card to-[#131f31] p-4"
    >
      <div className="flex items-center justify-between text-xs text-muted">
        <span>{label}</span>
        {Icon && <Icon className="size-4 text-faint" aria-hidden />}
      </div>
      <div className={`mt-2 text-3xl font-semibold tabular-nums tracking-tight ${tone}`}>
        {loading || value == null ? (
          loading ? (
            <Skeleton className="h-9 w-28" />
          ) : (
            <span className="text-muted">n/a</span>
          )
        ) : (
          <NumberFlow
            value={value}
            suffix={suffix}
            format={{ maximumFractionDigits: 0, signDisplay: signed ? 'exceptZero' : 'auto' }}
          />
        )}
      </div>
      {hint && <p className="mt-1 text-xs text-faint">{hint}</p>}
    </motion.div>
  )
}
