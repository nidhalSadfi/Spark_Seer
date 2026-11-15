import { useMemo, useState } from 'react'
import EChart, { axisStyle, tooltipStyle } from '../common/EChart'
import { MONTHS } from '../../utils/stats'
import { FWI_CLASSES, SAT_COLORS, fmtUtc } from './mapConfig'

const HOUR = 3600000

const TABS = [
  { id: 'frp', label: 'Fire Radiative Power', unit: 'MW', fmt: (v) => (v == null ? 'FRP unknown' : `${Math.round(v)} MW`) },
  { id: 'det', label: 'Detections per pass', unit: 'pixels', fmt: (v) => `${v} detections` },
  { id: 'fwi', label: 'Fire Weather Index', unit: 'FWI', fmt: (v) => `FWI ${Math.round(v)}` },
  { id: 'temp', label: 'Temperature', unit: '°C', fmt: (v) => `${v} °C` },
  { id: 'rh', label: 'Humidity', unit: '%', fmt: (v) => `${v} %` },
  { id: 'wind', label: 'Wind speed', unit: 'km/h', fmt: (v) => `${v} km/h` },
  { id: 'rain', label: 'Precipitation', unit: 'mm', fmt: (v) => `${v} mm` },
]

const pad = (n) => String(n).padStart(2, '0')

/** Local night (19:00–06:30 solar time) intervals inside [start, end] for a longitude. */
function nights(lon, start, end) {
  const out = []
  const day0 = Math.floor(start / 86400000) * 86400000 - 86400000
  for (let d = day0; d <= end; d += 86400000) {
    const a = d + (19 - lon / 15) * HOUR
    const b = a + 11.5 * HOUR
    if (b > start && a < end) out.push([{ xAxis: Math.max(a, start) }, { xAxis: Math.min(b, end) }])
  }
  return out
}

function buildOption(tab, fire, range, now) {
  const isPass = tab.id === 'frp' || tab.id === 'det'
  const accent = isPass ? '#f97316' : '#38bdf8'
  const xAxis = {
    type: 'time',
    min: range.start,
    max: range.end,
    minInterval: 3 * HOUR,
    maxInterval: 6 * HOUR,
    ...axisStyle,
    splitLine: { show: false },
    axisLabel: {
      ...axisStyle.axisLabel,
      hideOverlap: true,
      formatter: (v) => {
        const d = new Date(v)
        return d.getUTCHours() === 0 ? `{d|${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}}` : `${pad(d.getUTCHours())}:00`
      },
      rich: { d: { color: '#edf3fb', fontWeight: 600, fontSize: 11 } },
    },
  }
  const yAxis = {
    type: 'value',
    name: tab.unit,
    nameTextStyle: { color: '#7c8ca3', fontSize: 11, align: 'right' },
    min: 0,
    ...(tab.id === 'rh' ? { max: 100 } : {}),
    ...(tab.id === 'fwi' ? { max: (v) => Math.max(60, Math.ceil(v.max / 10) * 10) } : {}),
    ...axisStyle,
  }
  const markLine = {
    silent: true,
    symbol: 'none',
    lineStyle: { color: '#edf3fb', type: 'solid', width: 1 },
    // the live feed has no forecast: its right edge is the newest stored detection, not "now"
    label: { formatter: fire.hourly ? 'Now' : 'Latest', color: '#edf3fb', fontSize: 10.5 },
    data: [{ xAxis: now }],
  }
  const night = {
    silent: true,
    itemStyle: { color: 'rgba(3,7,15,0.45)' },
    label: { show: false },
    data: nights(fire.lon, range.start, range.end),
  }
  const area = (c) => ({
    color: {
      type: 'linear',
      x: 0,
      y: 0,
      x2: 0,
      y2: 1,
      colorStops: [
        { offset: 0, color: `${c}59` },
        { offset: 1, color: `${c}00` },
      ],
    },
  })

  let series
  if (tab.id === 'frp') {
    series = [
      {
        type: 'line',
        name: 'FRP',
        smooth: 0.35,
        symbol: 'circle',
        symbolSize: 9,
        lineStyle: { color: accent, width: 2.2 },
        areaStyle: area(accent),
        data: fire.passes.map((p) => ({
          value: [p.t, p.frp],
          sat: p.sat,
          itemStyle: { color: '#0f1828', borderColor: SAT_COLORS[p.sat], borderWidth: 2.4 },
        })),
        markLine,
        markArea: night,
      },
    ]
  } else if (tab.id === 'det') {
    series = [
      {
        type: 'bar',
        name: 'Detections',
        barWidth: 7,
        data: fire.passes.map((p) => ({
          value: [p.t, p.det],
          sat: p.sat,
          itemStyle: { color: SAT_COLORS[p.sat], borderRadius: [3, 3, 0, 0] },
        })),
        markLine,
        markArea: night,
      },
    ]
  } else {
    const pts = fire.hourly.map((h) => [h.t, h[tab.id]])
    const obs = pts.filter(([t]) => t <= now)
    const fc = pts.filter(([t]) => t >= now)
    const bands =
      tab.id === 'fwi'
        ? FWI_CLASSES.map((c, i) => [
            { yAxis: i ? FWI_CLASSES[i - 1].max : 0, itemStyle: { color: `${c.color}1c` } },
            { yAxis: c.max === Infinity ? 100 : c.max },
          ])
        : []
    series = [
      {
        type: tab.id === 'rain' ? 'bar' : 'line',
        name: 'Observed',
        smooth: true,
        symbol: 'none',
        barWidth: 4,
        lineStyle: { color: accent, width: 2.2 },
        itemStyle: { color: accent },
        areaStyle: tab.id === 'rain' ? undefined : area(accent),
        data: obs,
        markLine,
        markArea: { ...night, data: [...night.data, ...bands] },
      },
      {
        type: tab.id === 'rain' ? 'bar' : 'line',
        name: 'Forecast',
        smooth: true,
        symbol: 'none',
        barWidth: 4,
        lineStyle: { color: accent, width: 1.8, type: 'dashed', opacity: 0.85 },
        itemStyle: { color: `${accent}99` },
        data: fc,
      },
    ]
  }

  return {
    grid: { left: 46, right: 22, top: 30, bottom: 26 },
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle,
      formatter: (items) => {
        const it = items[0]
        if (!it) return ''
        const [t, v] = it.value
        const sat = it.data?.sat ? ` · ${it.data.sat}` : ''
        return `${fmtUtc(t)}${sat}<br/><b>${tab.fmt(v)}</b>${t > now ? ' <span style="color:#7c8ca3">forecast</span>' : ''}`
      },
    },
    xAxis,
    yAxis,
    series,
  }
}

/**
 * Bottom chart of the map page: per-pass FRP / detections and hourly fire weather for one fire.
 * Live fires have no hourly weather, so only the two pass tabs are offered for them.
 */
export default function FirePassChart({ fire, range, now }) {
  const [tabId, setTabId] = useState('frp')
  const passOnly = Boolean(fire && !fire.hourly)
  const tabs = passOnly ? TABS.filter((t) => t.id === 'frp' || t.id === 'det') : TABS
  const activeId = passOnly && tabId !== 'det' ? 'frp' : tabId
  const tab = TABS.find((t) => t.id === activeId)
  const option = useMemo(() => (fire && range ? buildOption(tab, fire, range, now) : null), [tab, fire, range, now])

  let current = null
  if (fire) {
    if (tab.id === 'frp') current = fire.frp
    else if (tab.id === 'det') current = fire.passes.at(-1)?.det
    else current = fire.hourly.reduce((best, h) => (Math.abs(h.t - now) < Math.abs(best.t - now) ? h : best))[tab.id]
  }
  const isPass = tab.id === 'frp' || tab.id === 'det'

  return (
    <div className="flex min-w-0 flex-col">
      <div role="tablist" aria-label="Chart metric" className="flex gap-0.5 overflow-x-auto border-b border-line px-3">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            type="button"
            aria-selected={t.id === tab.id}
            onClick={() => setTabId(t.id)}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 pb-2.5 pt-3 text-[13px] transition-colors ${
              t.id === tab.id ? 'border-fire font-semibold text-ink' : 'border-transparent text-muted hover:text-ink'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 pt-3 text-[11.5px] text-muted">
        {isPass ? (
          Object.entries(SAT_COLORS).map(([s, c]) => (
            <span key={s} className="flex items-center gap-1.5">
              <i className="size-2.5 rounded-full border-2" style={{ borderColor: c }} />
              {s}
            </span>
          ))
        ) : (
          <span>
            Observed <span className="text-faint">—</span> forecast <span className="text-faint">- - -</span> · shaded = local night
          </span>
        )}
        {fire && current != null && (
          <span className="ml-auto flex items-center gap-2">
            <b className="rounded-md bg-info px-2 py-0.5 font-semibold text-[#04121c]">{tab.fmt(current)}</b>
            <span className="rounded-md border border-line bg-sidebar px-2 py-0.5 text-muted">{fmtUtc(isPass ? fire.lastPass.t : now)}</span>
          </span>
        )}
      </div>
      {option ? (
        <EChart option={option} height={230} ariaLabel={`${tab.label} for ${fire.name}`} />
      ) : (
        <div className="grid h-[230px] place-items-center text-sm text-faint">Select a fire to see its history.</div>
      )}
    </div>
  )
}
