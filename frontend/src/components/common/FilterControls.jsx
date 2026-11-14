import { motion } from 'motion/react'
import { X } from 'lucide-react'
import { useFilters } from '../../context/FilterContext'
import { DATA_RANGE } from '../../services/fireApi'
import { SENSORS } from '../../services/constants'
import { Select } from '../ui/primitives'

export function SensorSelector() {
  const { sensor, setSensor } = useFilters()
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] font-medium uppercase tracking-wider text-faint">Sensor</span>
      <div role="radiogroup" aria-label="Sensor" className="flex h-9 rounded-lg border border-line bg-sidebar p-0.5">
        {SENSORS.map((s) => {
          const active = s.id === sensor
          return (
            <button
              key={s.id}
              role="radio"
              aria-checked={active}
              title={s.description}
              onClick={() => setSensor(s.id)}
              className={`relative rounded-md px-3 text-sm transition-colors ${
                active ? 'font-medium text-orange-200' : 'text-muted hover:text-ink'
              }`}
            >
              {active && (
                <motion.span
                  layoutId="sensor-pill"
                  className="absolute inset-0 rounded-md border border-fire/30 bg-fire/20"
                  transition={{ type: 'spring', stiffness: 450, damping: 34 }}
                />
              )}
              <span className="relative">{s.label}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export function RegionSelector() {
  const { areas, region, setRegion, aoi } = useFilters()
  return (
    <Select
      label="Area"
      value={aoi ? '' : region}
      onChange={(v) => v && setRegion(v)}
      options={[...(aoi ? [{ value: '', label: 'Custom area (map)' }] : []), ...areas.map((a) => ({ value: a.id, label: a.name }))]}
    />
  )
}

/** Shown while an AOI drawn on the map is driving the data; click to go back to the named area. */
export function AoiChip() {
  const { aoi, setAoi } = useFilters()
  if (!aoi) return null
  return (
    <button
      type="button"
      onClick={() => setAoi(null)}
      className="inline-flex h-9 items-center gap-1.5 self-end rounded-lg border border-info/50 bg-info/10 px-2.5 text-xs text-sky-200 transition-colors hover:bg-info/20"
      title="Clear the area drawn on the map"
    >
      Drawn area <X className="size-3.5" aria-hidden />
    </button>
  )
}

export function YearRangePicker() {
  const { startYear, endYear, setYearRange } = useFilters()
  const years = []
  for (let y = DATA_RANGE.startYear; y <= DATA_RANGE.endYear; y++) years.push({ value: y, label: String(y) })
  return (
    <div className="flex items-end gap-2">
      <Select label="From" value={startYear} onChange={(v) => setYearRange(+v, endYear)} options={years} />
      <Select label="To" value={endYear} onChange={(v) => setYearRange(startYear, +v)} options={years} />
    </div>
  )
}
