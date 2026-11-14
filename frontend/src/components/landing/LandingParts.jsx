import { useRef } from 'react'
import { Link } from 'react-router-dom'
import NumberFlow from '@number-flow/react'
import { motion, useInView } from 'motion/react'
import { ArrowUpRight, CalendarDays, ChartLine, Database, LayoutDashboard } from 'lucide-react'
import TiltCard from './TiltCard'

/** Scroll-reveal wrapper. */
export function Reveal({ children, delay = 0, className = '' }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 28 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: '0px 0px -60px 0px' }}
      transition={{ duration: 0.55, delay, ease: [0.22, 1, 0.36, 1] }}
      className={className}
    >
      {children}
    </motion.div>
  )
}

export function SectionHeading({ kicker, title, children }) {
  return (
    <Reveal className="mx-auto mb-12 max-w-2xl text-center">
      <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-fire">
        <span className="mr-2 text-faint">//</span>
        {kicker}
      </p>
      <h2 className="mt-2 text-3xl font-bold tracking-tight sm:text-4xl">{title}</h2>
      {children && <p className="mt-3 text-muted">{children}</p>}
    </Reveal>
  )
}

const FACTS = [
  { value: 2000, label: 'MODIS record begins', unit: '', group: false },
  { value: 2012, label: 'VIIRS record begins', unit: '', group: false },
  { value: 1000, label: 'MODIS pixel size', unit: ' m', group: true },
  { value: 375, label: 'VIIRS pixel size', unit: ' m', group: false },
]

function Fact({ fact, index }) {
  const ref = useRef(null)
  const seen = useInView(ref, { once: true, margin: '0px 0px -10% 0px' })
  return (
    <Reveal delay={index * 0.08}>
      <div ref={ref} className="text-center">
        <div className="text-4xl font-bold tabular-nums text-heat sm:text-5xl">
          <NumberFlow
            value={seen ? fact.value : 0}
            suffix={fact.unit}
            format={{ useGrouping: fact.group }}
            transformTiming={{ duration: 1400, easing: 'ease-out' }}
          />
        </div>
        <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.16em] text-muted">{fact.label}</p>
      </div>
    </Reveal>
  )
}

export function FactsStrip() {
  return (
    <div className="grid grid-cols-2 gap-8 rounded-3xl border border-line bg-card/40 px-6 py-8 backdrop-blur-sm md:grid-cols-4">
      {FACTS.map((f, i) => (
        <Fact key={f.label} fact={f} index={i} />
      ))}
    </div>
  )
}

const FEATURES = [
  { to: '/dashboard', icon: LayoutDashboard, title: 'Overview', body: 'KPIs, hotspot trend and anomaly alerts at a glance.' },
  { to: '/dashboard/calendar', icon: CalendarDays, title: 'Fire Activity Calendar', body: 'A year × month heatmap of harmonized hotspots. The centerpiece.', core: true },
  { to: '/dashboard/analytics', icon: ChartLine, title: 'Historical Analytics', body: 'Seasonality, long-term trends, and raw vs. harmonized sensors.' },
  { to: '/dashboard/explorer', icon: Database, title: 'Data Explorer', body: 'Filter hotspot records and export them as CSV.' },
]

export function FeatureGrid() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {FEATURES.map((f, i) => (
        <Reveal key={f.to} delay={i * 0.08}>
          <Link to={f.to} className="block h-full rounded-2xl focus-visible:outline-offset-4">
            <TiltCard className="h-full">
              <div className="flex items-start justify-between">
                <span className="grid size-11 place-items-center rounded-xl bg-fire/15 text-fire transition-transform duration-300 group-hover:rotate-6 group-hover:scale-110">
                  <f.icon className="size-5" aria-hidden />
                </span>
                <ArrowUpRight className="size-5 text-faint transition-all duration-300 group-hover:-translate-y-1 group-hover:translate-x-1 group-hover:text-fire" aria-hidden />
              </div>
              <h3 className="mt-5 text-lg font-semibold">{f.title}</h3>
              {f.core && (
                <span className="mt-1 inline-block rounded-full border border-fire/40 bg-fire/10 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-orange-200">
                  Core feature
                </span>
              )}
              <p className="mt-2 text-sm leading-relaxed text-muted">{f.body}</p>
            </TiltCard>
          </Link>
        </Reveal>
      ))}
    </div>
  )
}
