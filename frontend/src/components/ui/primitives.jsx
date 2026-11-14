import { motion } from 'motion/react'
import { AlertTriangle, Info } from 'lucide-react'

/**
 * Panel with HUD-style corner ticks, an ember glow on hover, and a scroll-reveal entrance.
 * Pass `reveal={false}` for cards that already animate as part of a parent stagger.
 */
export function Card({ className = '', children, reveal = true, delay = 0, ...rest }) {
  const entrance = reveal
    ? {
        initial: { opacity: 0, y: 18 },
        whileInView: { opacity: 1, y: 0 },
        viewport: { once: true, margin: '0px 0px -40px 0px' },
        transition: { duration: 0.45, delay, ease: [0.22, 1, 0.36, 1] },
      }
    : {}
  return (
    <motion.section
      {...entrance}
      className={`group/card relative min-w-0 rounded-2xl border border-line bg-gradient-to-br from-card to-[#111b2b] p-5 transition-[border-color,box-shadow] duration-300 hover:border-fire/35 hover:shadow-[0_0_36px_-14px_rgba(249,115,22,0.55)] ${className}`}
      {...rest}
    >
      <span aria-hidden className="pointer-events-none absolute left-2 top-2 size-2.5 border-l border-t border-fire/50 transition-all duration-300 group-hover/card:size-3.5" />
      <span aria-hidden className="pointer-events-none absolute bottom-2 right-2 size-2.5 border-b border-r border-fire/50 transition-all duration-300 group-hover/card:size-3.5" />
      {children}
    </motion.section>
  )
}

export function CardHeader({ title, subtitle, kicker, action }) {
  return (
    <div className="mb-4 flex items-start justify-between gap-3">
      <div className="min-w-0">
        {kicker && <p className="mb-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.18em] text-fire/80">{kicker}</p>}
        <h3 className="text-[15px] font-semibold tracking-tight text-ink">{title}</h3>
        {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
      </div>
      {action}
    </div>
  )
}

const TONES = {
  fire: 'border-fire/40 bg-fire/10 text-orange-200',
  ok: 'border-ok/40 bg-ok/10 text-green-200',
  info: 'border-info/40 bg-info/10 text-sky-200',
  neutral: 'border-line bg-card-hi text-muted',
}

export function Badge({ tone = 'neutral', className = '', children }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${TONES[tone]} ${className}`}
    >
      {children}
    </span>
  )
}

export function Notice({ icon: Icon = Info, title, tone = 'fire', children }) {
  const border = tone === 'fire' ? 'border-fire' : 'border-info'
  return (
    <div className={`flex gap-3 rounded-r-xl border-l-[3px] ${border} bg-fire/5 px-4 py-3 text-sm text-muted`}>
      <Icon className="mt-0.5 size-4 shrink-0 text-fire" aria-hidden />
      <div>
        {title && <strong className="mr-1 font-semibold text-ink">{title}</strong>}
        {children}
      </div>
    </div>
  )
}

export function IllustrativeNotice() {
  return (
    <Notice icon={AlertTriangle} title="Illustrative data.">
      Figures shown are generated sample values for UI development, not live MODIS or VIIRS observations.
    </Notice>
  )
}

export function Skeleton({ className = '' }) {
  return <div className={`animate-pulse rounded-lg bg-line/60 ${className}`} aria-hidden />
}

export function PageHeader({ title, description, actions, kicker }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-2xl">
        {kicker && (
          <p className="mb-1 font-mono text-[11px] font-medium uppercase tracking-[0.2em] text-fire">
            <span className="mr-2 text-faint">//</span>
            {kicker}
          </p>
        )}
        <h1 className="text-2xl font-semibold tracking-tight text-ink sm:text-3xl">{title}</h1>
        <motion.div
          aria-hidden
          className="mt-2 h-0.5 rounded-full bg-gradient-to-r from-fire-mid via-fire to-transparent"
          initial={{ width: 0 }}
          animate={{ width: 120 }}
          transition={{ duration: 0.7, delay: 0.15, ease: 'easeOut' }}
        />
        {description && <p className="mt-2 text-sm text-muted">{description}</p>}
      </div>
      {actions}
    </div>
  )
}

/** Tap/hover-responsive button used for primary and secondary actions. */
export function ActionButton({ variant = 'secondary', icon: Icon, children, className = '', ...rest }) {
  const styles =
    variant === 'primary'
      ? 'bg-fire text-[#1a0d02] font-medium hover:bg-orange-400'
      : 'border border-line text-muted hover:border-fire/40 hover:text-ink'
  return (
    <motion.button
      whileHover={{ y: -1 }}
      whileTap={{ scale: 0.96 }}
      transition={{ type: 'spring', stiffness: 500, damping: 30 }}
      className={`inline-flex h-9 items-center gap-2 rounded-lg px-3 text-sm ${styles} ${className}`}
      {...rest}
    >
      {Icon && <Icon className="size-4" aria-hidden />}
      {children}
    </motion.button>
  )
}

export function Select({ label, value, onChange, options, className = '' }) {
  return (
    <label className={`flex flex-col gap-1 text-[11px] font-medium uppercase tracking-wider text-faint ${className}`}>
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 rounded-lg border border-line bg-sidebar px-2.5 text-sm font-normal normal-case tracking-normal text-ink hover:border-faint"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}
