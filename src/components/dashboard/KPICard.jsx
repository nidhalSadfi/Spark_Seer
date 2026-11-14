import NumberFlow from '@number-flow/react'
import { motion } from 'motion/react'
import { Skeleton } from '../ui/primitives'

// Parent supplies `initial="hidden" animate="show"` + staggerChildren; this is the child variant.
export const staggerItem = {
  hidden: { opacity: 0, y: 16, scale: 0.97 },
  show: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.4, ease: [0.22, 1, 0.36, 1] } },
}

export default function KPICard({ label, value, icon: Icon, hint, suffix, signed, tone = 'text-ink', loading }) {
  return (
    <motion.div
      variants={staggerItem}
      whileHover={{ y: -3 }}
      transition={{ type: 'spring', stiffness: 400, damping: 28 }}
      className="group/kpi relative overflow-hidden rounded-2xl border border-line bg-gradient-to-br from-card to-[#111b2b] p-4 transition-[border-color,box-shadow] duration-300 hover:border-fire/40 hover:shadow-[0_0_30px_-12px_rgba(249,115,22,0.6)]"
    >
      {/* telemetry accent line */}
      <span aria-hidden className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-fire/60 to-transparent opacity-60 transition-opacity group-hover/kpi:opacity-100" />
      <div className="flex items-center justify-between font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
        <span>{label}</span>
        {Icon && <Icon className="size-4 text-fire/70 transition-transform duration-300 group-hover/kpi:rotate-6 group-hover/kpi:scale-110" aria-hidden />}
      </div>
      <div className={`mt-3 text-3xl font-semibold tabular-nums tracking-tight ${tone}`}>
        {loading ? (
          <Skeleton className="h-9 w-28" />
        ) : value == null ? (
          <span className="text-muted">n/a</span>
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
