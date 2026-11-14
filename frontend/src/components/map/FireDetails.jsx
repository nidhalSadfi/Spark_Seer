import { CloudRain, Droplets, Flame, MapPin, MousePointerClick, Thermometer, TrendingDown, TrendingUp, Wind } from 'lucide-react'
import { Badge } from '../ui/primitives'
import { fmtInt } from '../../utils/format'
import { FWI_CLASSES, fmtCoord, fmtUtc, fwiClass } from './mapConfig'

const panel = 'flex min-w-0 flex-col rounded-2xl border border-line bg-gradient-to-br from-card to-[#131f31] p-4'

function Section({ children, className = '' }) {
  return <div className={`border-t border-line py-3.5 ${className}`}>{children}</div>
}

function Tile({ icon: Icon, value, label }) {
  return (
    <div className="grid grid-cols-[auto_1fr] items-center gap-x-2.5 rounded-lg border-b-2 border-info/50 bg-sidebar/70 px-3 py-2.5">
      <Icon className="row-span-2 size-4 text-info" aria-hidden />
      <b className="text-[14px] font-semibold leading-tight text-ink">{value}</b>
      <span className="text-[11px] text-faint">{label}</span>
    </div>
  )
}

/** Left-hand panel of the map page: everything known about the selected fire. */
export default function FireDetails({ fire, fires = [], onSelect, className = '' }) {
  if (!fire) {
    return (
      <aside className={`${panel} items-center justify-center gap-2 py-16 text-center text-sm text-muted ${className}`}>
        <MousePointerClick className="size-6 text-faint" aria-hidden />
        Select a fire on the map to see its details.
      </aside>
    )
  }

  const cls = fwiClass(fire.fwi)
  const localHour = (((fire.lastPass.t / 3600000 + fire.lon / 15) % 24) + 24) % 24
  const dayPass = localHour >= 6 && localHour < 18
  const others = fires.filter((f) => f.id !== fire.id).slice(0, 4)
  const maxFrp = Math.max(1, ...fires.map((f) => f.frp))
  const up = (fire.frpDelta ?? 0) >= 0

  return (
    <aside className={`${panel} ${className}`} aria-label="Selected fire details">
      <div className="flex items-start gap-2.5 rounded-xl border border-line bg-sidebar/70 px-3 py-2.5">
        <MapPin className="mt-0.5 size-4 shrink-0 text-info" aria-hidden />
        <div className="min-w-0">
          <p className="truncate text-[15px] font-semibold text-ink">{fire.name}</p>
          <p className="text-xs text-muted">{fire.place}</p>
          <p className="font-mono text-[11px] text-faint">{fmtCoord(fire.lat, fire.lon)}</p>
        </div>
      </div>
      <p className="mt-2.5 px-0.5 text-[11.5px] text-faint">
        Last pass <span className="text-muted">{fmtUtc(fire.lastPass.t)}</span> · {fire.lastPass.sat}
      </p>

      <Section className="mt-3">
        <div className="flex items-baseline gap-2">
          <span className="text-[32px] font-bold leading-none tracking-tight text-ink">{fire.fwi}</span>
          <span className="text-sm font-semibold text-muted">FWI</span>
        </div>
        <p className="mt-1 text-[17px] font-semibold" style={{ color: cls.color === '#9b1b4f' ? '#e2457f' : cls.color }}>
          {cls.label} danger
        </p>
        <div className="relative mt-3 flex h-1.5 gap-0.5 overflow-visible" aria-hidden>
          {FWI_CLASSES.map((c) => (
            <span key={c.label} className="flex-1 rounded-full" style={{ background: c.color }} />
          ))}
          <i
            className="absolute -top-[5px] h-4 w-1 -translate-x-1/2 rounded bg-white shadow-[0_0_0_3px_rgba(0,0,0,.45)]"
            style={{ left: `${cls.pos * 100}%` }}
          />
        </div>
        <div className="mt-1.5 flex justify-between text-[10.5px] text-faint" aria-hidden>
          {FWI_CLASSES.map((c) => (
            <span key={c.label}>{c.label}</span>
          ))}
        </div>
      </Section>

      <Section className="flex items-center gap-2.5">
        <span className="grid size-8 place-items-center rounded-lg bg-fire/15 text-fire">
          <Flame className="size-4" aria-hidden />
        </span>
        <p className="min-w-0 text-sm leading-tight text-ink">
          <b className="font-semibold">Active fire</b>
          <span className="block text-xs text-muted">
            {dayPass ? 'Day' : 'Night'} pass · {fire.lastPass.sat}
          </span>
        </p>
        <Badge tone={fire.confidence === 'high' ? 'ok' : 'neutral'} className="ml-auto">
          {fire.confidence === 'high' ? 'High' : 'Nominal'} confidence
        </Badge>
      </Section>

      <Section>
        <div className="flex items-baseline gap-2">
          <span className="text-[32px] font-bold leading-none tracking-tight text-ink">{Math.round(fire.frp)}</span>
          <span className="text-sm font-semibold text-muted">MW</span>
          <span className="text-xs text-faint">Fire Radiative Power</span>
        </div>
        {fire.frpDelta != null && (
          <p className="mt-1.5 flex items-center gap-1 text-xs text-muted">
            {up ? <TrendingUp className="size-3.5 text-fire-mid" /> : <TrendingDown className="size-3.5 text-info" />}
            <b className={`font-semibold ${up ? 'text-fire-mid' : 'text-info'}`}>
              {up ? '+' : ''}
              {fire.frpDelta} MW
            </b>
            since previous pass ({fmtUtc(fire.prevPass.t, { date: false })})
          </p>
        )}
      </Section>

      <Section>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-[12.5px]">
          <dt className="text-muted">Burned area (est.)</dt>
          <dd className="text-right font-medium text-sky-300">{fmtInt(fire.burnedHa)} ha</dd>
          <dt className="text-muted">Detections · 24 h</dt>
          <dd className="text-right font-medium text-sky-300">
            {fire.detections24h.viirs} VIIRS · {fire.detections24h.modis} MODIS
          </dd>
          <dt className="text-muted">Persistence</dt>
          <dd className="text-right font-medium text-sky-300">{fire.persistence} satellite passes</dd>
          <dt className="text-muted">Spread (inferred)</dt>
          <dd className="text-right font-medium text-sky-300">
            towards {fire.spread.to} · ~{fire.spread.kmh} km/h
          </dd>
          <dt className="text-muted">First detected</dt>
          <dd className="text-right font-medium text-sky-300">{fmtUtc(fire.firstDetected)}</dd>
          <dt className="text-muted">Land cover</dt>
          <dd className="text-right font-medium text-sky-300">{fire.landCover}</dd>
        </dl>
      </Section>

      <div className="grid grid-cols-2 gap-2 pb-1">
        <Tile icon={Thermometer} value={`${fire.weather.temp} °C`} label="Temperature" />
        <Tile icon={Droplets} value={`${fire.weather.rh} %`} label="Humidity" />
        <Tile icon={CloudRain} value={`${fire.weather.rain72} mm`} label="Rain · last 72 h" />
        <Tile icon={Wind} value={`${fire.weather.wind} km/h`} label={`Wind · from ${fire.weather.windFrom}`} />
      </div>

      {others.length > 0 && (
        <Section className="mt-2.5 pb-0">
          <p className="mb-2 font-mono text-[10.5px] uppercase tracking-[0.14em] text-faint">Other fires · by FRP</p>
          <ul className="flex flex-col gap-1.5">
            {others.map((f) => (
              <li key={f.id}>
                <button
                  type="button"
                  onClick={() => onSelect?.(f.id)}
                  className="relative flex w-full items-center justify-between overflow-hidden rounded-lg bg-sidebar/70 px-3 py-2 text-left text-[12.5px] hover:bg-card-hi"
                >
                  <span
                    className="absolute inset-y-0 left-0 bg-gradient-to-r from-fire/25 to-fire/5"
                    style={{ width: `${(f.frp / maxFrp) * 100}%` }}
                    aria-hidden
                  />
                  <span className="relative truncate font-medium text-ink">{f.name}</span>
                  <span className="relative ml-2 shrink-0 font-mono text-[11.5px] text-fire-mid">{Math.round(f.frp)} MW</span>
                </button>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </aside>
  )
}
