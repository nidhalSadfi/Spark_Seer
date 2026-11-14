import { motion } from 'motion/react'
import { useFilters } from '../../context/FilterContext'
import { DATA_RANGE } from '../../services/fireApi'
import { REGIONS, SENSORS } from '../../services/constants'
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
  const { region, setRegion } = useFilters()
  return (
    <Select
      label="Region"
      value={region}
      onChange={setRegion}
      options={REGIONS.map((r) => ({ value: r.id, label: r.name }))}
    />
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
