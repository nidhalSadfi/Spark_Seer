import { AlertTriangle, Info } from 'lucide-react'

export function Card({ className = '', children, ...rest }) {
  return (
    <section
      className={`min-w-0 rounded-2xl border border-line bg-gradient-to-br from-card to-[#131f31] p-5 ${className}`}
      {...rest}
    >
      {children}
    </section>
  )
}

export function CardHeader({ title, subtitle, action }) {
  return (
    <div className="mb-4 flex items-start justify-between gap-3">
      <div className="min-w-0">
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

export function PageHeader({ title, description, actions }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-2xl">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted">{description}</p>}
      </div>
      {actions}
    </div>
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
