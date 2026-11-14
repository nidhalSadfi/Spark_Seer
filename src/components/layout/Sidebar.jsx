import { NavLink } from 'react-router-dom'
import { CalendarDays, ChartLine, Database, Flame, LayoutDashboard, Map } from 'lucide-react'

export const NAV_ITEMS = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/map', label: 'Global Fire Map', icon: Map },
  { to: '/calendar', label: 'Activity Calendar', icon: CalendarDays },
  { to: '/analytics', label: 'Analytics', icon: ChartLine },
  { to: '/explorer', label: 'Data Explorer', icon: Database },
]

export function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <span className="grid size-8 place-items-center rounded-lg bg-fire/15 text-fire">
        <Flame className="size-5" aria-hidden />
      </span>
      <span className="text-[15px] font-extrabold tracking-[0.12em]">
        SPARK <span className="text-fire">SEER</span>
      </span>
    </div>
  )
}

export default function Sidebar({ onNavigate }) {
  return (
    <div className="flex h-full flex-col gap-6 bg-sidebar p-4">
      <Brand />
      <nav aria-label="Main" className="flex flex-col gap-1">
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={onNavigate}
            className={({ isActive }) =>
              `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                isActive ? 'bg-fire/15 font-medium text-orange-200' : 'text-muted hover:bg-card hover:text-ink'
              }`
            }
          >
            <Icon className="size-[18px]" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
      <p className="mt-auto px-2 text-[11px] leading-relaxed text-faint">
        NASA Space Apps Challenge 2026
        <br />
        MODIS + VIIRS active-fire harmonization
      </p>
    </div>
  )
}
