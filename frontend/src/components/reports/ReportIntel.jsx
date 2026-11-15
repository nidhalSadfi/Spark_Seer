import { CloudRain, Droplets, MapPin, RefreshCw, Thermometer, Wind } from 'lucide-react'
import { Badge, Skeleton } from '../ui/primitives'
import { Tile } from '../map/FireDetails'
import { usePointWeather } from '../../hooks/useFireData'
import { MONTHS } from '../../utils/stats'
import { fmtInt } from '../../utils/format'
import { fmtLatLon } from '../../utils/reports'

const dayName = (iso) =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' })

const num = (v, unit = '') => (v == null ? 'n/a' : `${v}${unit}`)

/**
 * Weather and location info for a confirmed community fire. Confirming a report is what starts
 * the lookup (GET /weather?lat=&lon=), so nothing is requested for unverified or dismissed ones.
 */
export default function ReportIntel({ report }) {
  const { data, isLoading, isError, error, refetch, isFetching } = usePointWeather(report)

  if (isLoading) {
    return (
      <div className="flex flex-col gap-2" role="status" aria-label="Fetching weather for the reported location">
        <p className="text-xs text-muted">Fetching weather for {fmtLatLon(report.lat, report.lon)}…</p>
        <div className="grid grid-cols-2 gap-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-14" />
          ))}
        </div>
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex flex-col items-start gap-2 rounded-xl border border-line bg-sidebar/60 p-3 text-xs text-muted">
        <p>
          Weather is unavailable for this location right now. <span className="text-faint">{error.message}</span>
        </p>
        <button
          type="button"
          onClick={() => refetch()}
          disabled={isFetching}
          className="inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5 font-medium text-ink hover:border-fire/40 disabled:opacity-60"
        >
          <RefreshCw className={`size-3.5 ${isFetching ? 'animate-spin' : ''}`} aria-hidden /> Try again
        </button>
      </div>
    )
  }

  if (!data) return null
  const today = data.days[0]
  const hc = data.historical_context
  const median = hc?.available ? hc.median_harmonized_same_month : null

  return (
    <div className="flex flex-col gap-3">
      <p className="flex items-start gap-2 text-xs text-muted">
        <MapPin className="mt-0.5 size-3.5 shrink-0 text-info" aria-hidden />
        {data.area ? (
          <span>
            Inside the <b className="font-semibold text-ink">{data.area.name}</b> study area.
          </span>
        ) : (
          <span>Outside the named study areas, so there is no area-level history for this spot.</span>
        )}
      </p>

      {today && (
        <div>
          <p className="mb-1.5 font-mono text-[10.5px] uppercase tracking-[0.14em] text-faint">Today at this spot</p>
          <div className="grid grid-cols-2 gap-2">
            <Tile icon={Thermometer} value={num(today.temp_max_c, ' °C')} label="Max temperature" />
            <Tile icon={Droplets} value={num(today.humidity_min_pct, ' %')} label="Min humidity" />
            <Tile icon={Wind} value={num(today.gust_max_kmh, ' km/h')} label="Max wind gust" />
            <Tile icon={CloudRain} value={num(today.rain_total_mm, ' mm')} label="Rain" />
          </div>
        </div>
      )}

      <div>
        <p className="mb-1.5 font-mono text-[10.5px] uppercase tracking-[0.14em] text-faint">3-day outlook</p>
        <div className="overflow-hidden rounded-lg border border-line text-[12px]">
          <div className="grid grid-cols-[1.4fr_repeat(4,1fr)] gap-x-2 bg-sidebar px-2.5 py-1.5 text-[10.5px] text-faint">
            <span>Day</span>
            <span className="text-right">°C max</span>
            <span className="text-right">% RH min</span>
            <span className="text-right">km/h gust</span>
            <span className="text-right">mm rain</span>
          </div>
          {data.days.map((d) => (
            <div key={d.date} className="grid grid-cols-[1.4fr_repeat(4,1fr)] gap-x-2 border-t border-line px-2.5 py-1.5 tabular-nums text-ink">
              <span className="text-muted">{dayName(d.date)}</span>
              <span className="text-right">{num(d.temp_max_c)}</span>
              <span className="text-right">{num(d.humidity_min_pct)}</span>
              <span className="text-right">{num(d.gust_max_kmh)}</span>
              <span className="text-right">{num(d.rain_total_mm)}</span>
            </div>
          ))}
        </div>
      </div>

      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
        {median != null ? (
          <>
            Typical {MONTHS[hc.month - 1]} satellite activity around here: median {fmtInt(median)} harmonized detections (
            {hc.median_years.join('–')}).
            {hc.is_critical_month && <Badge tone="fire">Critical fire-season month</Badge>}
          </>
        ) : (
          'No satellite record around this point: the EmberLedger dataset covers Tunisia and northern Algeria.'
        )}
      </p>

      <p className="text-[11px] leading-relaxed text-faint">
        {data.source} · fetched{' '}
        {new Date(data.fetched_at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })} UTC. {data.label}.
      </p>
    </div>
  )
}
