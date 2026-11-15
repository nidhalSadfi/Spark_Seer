import { CloudRain, Droplets, Thermometer, Wind } from 'lucide-react'
import { Badge, Skeleton } from '../ui/primitives'
import { useWeather } from '../../hooks/useFireData'
import { useFilters } from '../../context/FilterContext'
import { MONTHS } from '../../utils/stats'
import { fmtInt } from '../../utils/format'

const dayName = (iso) => new Date(`${iso}T12:00:00Z`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' })

function Metric({ icon: Icon, value, unit, label }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <Icon className="size-4 shrink-0 text-fire/80" aria-hidden />
      <span className="font-medium tabular-nums text-ink">
        {value ?? 'n/a'}
        {value != null && <span className="ml-0.5 text-xs font-normal text-muted">{unit}</span>}
      </span>
      <span className="text-xs text-faint">{label}</span>
    </div>
  )
}

/** 3-day weather context for the selected named area (GET /weather). Context only, not a danger index. */
export default function WeatherCard() {
  const { data, isLoading, error } = useWeather()
  const { aoi, region, areaName } = useFilters()

  if (isLoading) return <Skeleton className="h-36 w-full" />
  if (error) {
    return <p className="text-sm text-muted">Weather is unavailable right now: {error.message}</p>
  }

  const hc = data.historical_context
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span>{data.area.name}</span>
        <span aria-hidden>·</span>
        <span>{data.source}</span>
        <span aria-hidden>·</span>
        <span>fetched {new Date(data.fetched_at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })} UTC</span>
        {aoi && <Badge tone="info">Shown for {areaName(region)}: weather is only available for named areas</Badge>}
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        {data.days.map((d) => (
          <div key={d.date} className="rounded-xl border border-line bg-sidebar/60 p-3">
            <p className="mb-2 font-mono text-[11px] uppercase tracking-wider text-fire">{dayName(d.date)}</p>
            <div className="flex flex-col gap-1.5">
              <Metric icon={Thermometer} value={d.temp_max_c} unit="°C" label="max" />
              <Metric icon={Droplets} value={d.humidity_min_pct} unit="%" label="min humidity" />
              <Metric icon={Wind} value={d.gust_max_kmh} unit="km/h" label="max gust" />
              <Metric icon={CloudRain} value={d.rain_total_mm} unit="mm" label="rain" />
            </div>
          </div>
        ))}
      </div>

      {hc?.available && (
        <p className="flex flex-wrap items-center gap-2 text-xs text-muted">
          Typical {MONTHS[hc.month - 1]} activity here: median {fmtInt(hc.median_harmonized_same_month)} harmonized
          detections ({hc.median_years.join('–')}).
          {hc.is_critical_month && <Badge tone="fire">Critical fire-season month</Badge>}
        </p>
      )}
      <p className="text-[11px] text-faint">{data.label}. Forecast issue time is not published by Open-Meteo.</p>
    </div>
  )
}
