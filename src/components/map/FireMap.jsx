import { MapPinned } from 'lucide-react'

/**
 * MAP SLOT — intentionally blank.
 *
 * The map (MapLibre GL, hotspot layers, AOI drawing, popups, time animation)
 * is owned by another team member. Replace the body of this component with the
 * real map; the rest of the app only depends on this component's props.
 *
 * Integration contract:
 *   - Read filters from `useFilters()` (region, sensor, startYear, endYear).
 *   - When the user draws/clears an AOI, call `setAoi(feature | null)` from
 *     `useFilters()` so the calendar, analytics and explorer pages react.
 *
 * Props:
 *   height   – CSS height of the slot (default 420)
 *   compact  – true when embedded as a preview card on the Overview page
 */
export default function FireMap({ height = 420, compact = false }) {
  return (
    <div
      id="fire-map-root"
      style={{ height }}
      className="grid w-full place-items-center rounded-xl border border-dashed border-line bg-sidebar/60"
    >
      <div className="flex flex-col items-center gap-2 text-center text-faint">
        <MapPinned className="size-7" aria-hidden />
        <p className="text-sm font-medium text-muted">Map module</p>
        {!compact && <p className="max-w-xs text-xs">Reserved for the interactive hotspot map.</p>}
      </div>
    </div>
  )
}
