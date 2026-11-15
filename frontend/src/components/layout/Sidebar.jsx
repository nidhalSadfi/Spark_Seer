import { Link, NavLink } from 'react-router-dom'
import { motion } from 'motion/react'
import { CalendarDays, ChartLine, Database, Flame, LayoutDashboard, Map, Siren, Workflow } from 'lucide-react'
import { useReports } from '../../hooks/useReports'

export const NAV_ITEMS = [
  { to: '/dashboard', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/dashboard/map', label: 'Global Fire Map', icon: Map },
  { to: '/dashboard/reports', label: 'Fire Reports', icon: Siren, badge: 'pending' },
  { to: '/dashboard/calendar', label: 'Activity Calendar', icon: CalendarDays },
  { to: '/dashboard/analytics', label: 'Analytics', icon: ChartLine },
  { to: '/dashboard/explorer', label: 'Data Explorer', icon: Database },
  { to: '/dashboard/processing', label: 'Processing Center', icon: Workflow },
]

// Instruments feeding the harmonized record (names only; no live-status claim).
const INSTRUMENTS = [
  { sensor: 'MODIS', platforms: 'Terra · Aqua' },
  { sensor: 'VIIRS', platforms: 'Suomi NPP · NOAA-20/21' },
]

export function Brand() {
  return (
    <div className="flex items-center gap-3 px-1">
      <span className="relative grid size-10 place-items-center">
        {/* orbiting satellite */}
        <span className="absolute inset-0 rounded-full border border-dashed border-info/30 motion-safe:animate-orbit">
          <span className="absolute -top-[3px] left-1/2 size-1.5 -translate-x-1/2 rounded-full bg-info shadow-[0_0_8px_#38bdf8]" />
        </span>
        <Flame className="size-5 text-fire motion-safe:animate-flicker" aria-hidden />
      </span>
      <div className="leading-none">
        <div className="text-[15px] font-extrabold tracking-[0.14em]">
          SPARK <span className="text-fire">SEER</span>
        </div>
        <div className="mt-1 font-mono text-[9px] uppercase tracking-[0.2em] text-faint">Fire intelligence</div>
      </div>
    </div>
  )
}

export default function Sidebar({ onNavigate }) {
  const { counts } = useReports()
  return (
    <div className="flex h-full flex-col gap-6 bg-sidebar p-4">
      <Link to="/" onClick={onNavigate} aria-label="Spark Seer landing page" className="rounded-lg">
        <Brand />
      </Link>
      <nav aria-label="Main" className="flex flex-col gap-1">
        <p className="mb-1 px-3 font-mono text-[10px] uppercase tracking-[0.2em] text-faint">Navigation</p>
        {NAV_ITEMS.map(({ to, label, icon: Icon, end, badge }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={onNavigate}
            className={({ isActive }) =>
              `group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                isActive ? 'font-medium text-orange-200' : 'text-muted hover:text-ink'
              }`
            }
          >
            {({ isActive }) => (
              <>
                {isActive && (
                  <motion.span
                    layoutId="nav-active"
                    className="absolute inset-0 rounded-lg border border-fire/30 bg-fire/15"
                    transition={{ type: 'spring', stiffness: 420, damping: 34 }}
                  >
                    <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r bg-fire shadow-[0_0_10px_#f97316]" />
                  </motion.span>
                )}
                <Icon className="relative size-[18px] transition-transform duration-200 group-hover:scale-110" aria-hidden />
                <span className="relative">{label}</span>
                {badge && counts[badge] > 0 && (
                  <span
                    className="relative ml-auto grid min-w-5 place-items-center rounded-full border border-dashed border-yellow-300/70 bg-yellow-300/10 px-1.5 text-[11px] font-bold leading-5 tabular-nums text-yellow-200"
                    title={`${counts[badge]} unverified ${counts[badge] === 1 ? 'alert' : 'alerts'} waiting for review`}
                  >
                    {counts[badge]}
                  </span>
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      <div className="mt-auto">
        <p className="mb-2 px-1 font-mono text-[10px] uppercase tracking-[0.2em] text-faint">Instruments</p>
        <ul className="flex flex-col gap-2">
          {INSTRUMENTS.map((i) => (
            <li key={i.sensor} className="rounded-lg border border-line bg-card/50 px-3 py-2">
              <p className="font-mono text-xs font-semibold text-info">{i.sensor}</p>
              <p className="text-[11px] text-muted">{i.platforms}</p>
            </li>
          ))}
        </ul>
        <p className="mt-4 px-1 text-[11px] leading-relaxed text-faint">NASA Space Apps Challenge 2026</p>
      </div>
    </div>
  )
}
