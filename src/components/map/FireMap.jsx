import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import './fireMap.css'
import {
  ArrowUpRight,
  ChevronDown,
  ChevronUp,
  Flame,
  Layers,
  LocateFixed,
  Maximize2,
  Minimize2,
  Minus,
  Pause,
  Play,
  Plus,
  SquareDashed,
  X,
} from 'lucide-react'
import { useFilters } from '../../context/FilterContext'
import { useActiveFires } from '../../hooks/useFireData'
import { fmtInt } from '../../utils/format'
import { AOI_STYLE, GlowLayer, boundsToFeature, createBaseMap, fireIcon, fireLabelIcon, inAoi } from './leafletLayers'
import { fmtUtc } from './mapConfig'

const SENSOR_CHIP = {
  harmonized: 'VIIRS 375 m + MODIS 1 km',
  modis: 'MODIS 1 km only',
  viirs: 'VIIRS 375 m only',
}

const glass = 'rounded-xl border border-line/80 bg-[#0d1524]/85 shadow-[0_10px_26px_rgba(0,0,0,.4)] backdrop-blur-md'

function ToolButton({ label, active = false, onClick, children }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={`grid size-9 place-items-center rounded-xl border backdrop-blur-md transition-colors ${
        active ? 'border-info/60 bg-info text-[#04121c]' : 'border-line/80 bg-[#0d1524]/85 text-muted hover:text-ink'
      }`}
    >
      {children}
    </button>
  )
}

/**
 * Live fire map (Leaflet + Esri dark basemap).
 *
 * Shows near-real-time fire clusters (flame markers sized by FRP) over a glow layer of
 * individual hotspot detections, with a 24 h time slider, layer toggles, legend and
 * rectangle AOI drawing.
 *
 * Integration contract (unchanged):
 *   - Reads region + sensor from `useFilters()`; data comes from `useActiveFires()`.
 *   - Drawing / clearing an area calls `setAoi(feature | null)`.
 *
 * Props:
 *   height     – CSS height of the map (default 420)
 *   compact    – preview mode for the Overview card (no slider / legend / drawing)
 *   selectedId – controlled selected fire id (optional)
 *   onSelect   – called with a fire id when a marker is clicked (optional)
 */
export default function FireMap({ height = 420, compact = false, selectedId, onSelect }) {
  const { sensor, aoi, setAoi } = useFilters()
  const { data, isLoading } = useActiveFires()

  const wrapRef = useRef(null)
  const elRef = useRef(null)
  const mapRef = useRef(null)
  const layersRef = useRef(null)
  const fittedRef = useRef(null)
  const prevSelRef = useRef(null)
  const cursorRef = useRef(null)
  const selectRef = useRef(null)

  const [ownSel, setOwnSel] = useState(null)
  const controlled = selectedId !== undefined
  const sel = controlled ? selectedId : ownSel

  const [cursor, setCursor] = useState(null) // null = live (end of window)
  const [playing, setPlaying] = useState(false)
  const [show, setShow] = useState({ fires: true, detections: true })
  const [legendOpen, setLegendOpen] = useState(false)
  const [drawing, setDrawing] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)

  const win = data?.window
  const t = cursor ?? win?.end ?? Infinity

  useEffect(() => {
    selectRef.current = controlled ? onSelect : setOwnSel
    cursorRef.current = cursor
  })

  // ── map lifecycle ──
  useEffect(() => {
    const map = createBaseMap(elRef.current, { interactive: !compact })
    map.setView([15, 10], 2)
    const glow = new GlowLayer().addTo(map)
    const markers = L.layerGroup().addTo(map)
    const aoiLayer = L.layerGroup().addTo(map)
    mapRef.current = map
    layersRef.current = { glow, markers, aoiLayer }
    fittedRef.current = null
    const ro = new ResizeObserver(() => map.invalidateSize())
    ro.observe(elRef.current)
    return () => {
      ro.disconnect()
      map.remove()
      mapRef.current = null
      layersRef.current = null
    }
  }, [compact])

  // fit to the region whenever it changes
  const regionKey = data ? `${data.meta.region}:${data.bounds.flat().join(',')}` : null
  useEffect(() => {
    const map = mapRef.current
    if (!map || !data || fittedRef.current === regionKey) return
    map.fitBounds(data.bounds, { padding: [24, 24], animate: fittedRef.current !== null })
    fittedRef.current = regionKey
  }, [regionKey, data, compact])

  // ── hotspot glow ──
  useEffect(() => {
    const ls = layersRef.current
    if (!ls || !data) return
    ls.glow.setData(show.detections ? data.detections : [], t, data.window.end - data.window.start)
  }, [data, t, show.detections, compact])

  // ── fire markers ──
  useEffect(() => {
    const ls = layersRef.current
    if (!ls || !data) return
    ls.markers.clearLayers()
    if (!show.fires) return
    for (const f of data.fires) {
      if (f.firstDetected > t) continue
      const isSel = f.id === sel
      const m = L.marker([f.lat, f.lon], {
        icon: fireIcon(f, isSel),
        keyboard: false,
        title: `${f.name} · ${Math.round(f.frp)} MW`,
        zIndexOffset: isSel ? 1000 : Math.round(f.frp),
      })
      m.on('click', () => selectRef.current?.(f.id))
      ls.markers.addLayer(m)
      if (isSel && !compact) {
        ls.markers.addLayer(
          L.marker([f.lat, f.lon], { icon: fireLabelIcon(f), interactive: false, keyboard: false, zIndexOffset: 2000 }),
        )
      }
    }
  }, [data, sel, t, show.fires, compact])

  // fly to a fire picked outside the map (e.g. from the details list) when it is off-screen
  useEffect(() => {
    const map = mapRef.current
    const prev = prevSelRef.current
    prevSelRef.current = sel
    const f = data?.fires.find((x) => x.id === sel)
    if (!map || !f || prev == null || prev === sel) return
    if (!map.getBounds().pad(-0.1).contains([f.lat, f.lon])) {
      map.flyTo([f.lat, f.lon], Math.max(map.getZoom(), 5), { duration: 0.8 })
    }
  }, [sel, data])

  // ── area of interest ──
  useEffect(() => {
    const ls = layersRef.current
    if (!ls) return
    ls.aoiLayer.clearLayers()
    if (aoi) L.geoJSON(aoi, { style: AOI_STYLE, interactive: false }).addTo(ls.aoiLayer)
  }, [aoi, compact])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !drawing) return
    map.dragging.disable()
    map.getContainer().style.cursor = 'crosshair'
    let start = null
    let rect = null
    const down = (e) => {
      start = e.latlng
      rect = L.rectangle([start, start], { ...AOI_STYLE, interactive: false }).addTo(map)
    }
    const move = (e) => start && rect.setBounds([start, e.latlng])
    const up = (e) => {
      if (!start) return
      const b = L.latLngBounds(start, e.latlng)
      rect.remove()
      rect = null
      start = null
      if (b.getNorth() - b.getSouth() > 0.05 && b.getEast() - b.getWest() > 0.05) setAoi(boundsToFeature(b))
      setDrawing(false)
    }
    const key = (e) => e.key === 'Escape' && setDrawing(false)
    map.on('mousedown', down)
    map.on('mousemove', move)
    map.on('mouseup', up)
    window.addEventListener('keydown', key)
    return () => {
      map.off('mousedown', down)
      map.off('mousemove', move)
      map.off('mouseup', up)
      window.removeEventListener('keydown', key)
      rect?.remove()
      map.dragging.enable()
      map.getContainer().style.cursor = ''
    }
  }, [drawing, setAoi])

  // ── time playback ──
  useEffect(() => {
    if (!playing || !win) return
    let raf
    let last = performance.now()
    let cur = cursorRef.current ?? win.start
    const span = win.end - win.start
    const step = (now) => {
      cur += (span * (now - last)) / 10000 // full window in ~10 s
      last = now
      if (cur >= win.end) {
        setCursor(null)
        setPlaying(false)
        return
      }
      setCursor(cur)
      raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [playing, win])

  // ── fullscreen ──
  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === wrapRef.current)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])
  const toggleFullscreen = () =>
    document.fullscreenElement ? document.exitFullscreen() : wrapRef.current?.requestFullscreen?.()

  const togglePlay = () => {
    if (!win) return
    if (!playing && cursor == null) setCursor(win.start)
    setPlaying((p) => !p)
  }

  const visibleFires = data ? data.fires.filter((f) => f.firstDetected <= t) : []
  const aoiFires = aoi ? visibleFires.filter((f) => inAoi(aoi, f.lat, f.lon)).length : 0
  const pct = win ? ((t - win.start) / (win.end - win.start)) * 100 : 100

  return (
    <div
      ref={wrapRef}
      style={{ height: fullscreen ? '100%' : height }}
      className={`sg-map relative isolate w-full overflow-hidden bg-bg ${compact ? 'rounded-xl border border-line' : ''}`}
    >
      <div ref={elRef} className="absolute inset-0 z-0" />

      {isLoading && !data && (
        <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center text-sm text-muted">
          Loading active fires…
        </div>
      )}

      {/* top-left: layer chip + AOI chip */}
      <div className="pointer-events-none absolute left-3 top-3 z-10 flex flex-col items-start gap-2">
        <div className={`${glass} flex items-center gap-2 px-3 py-2 text-xs text-muted`}>
          <Layers className="size-4 text-faint" aria-hidden />
          <b className="font-semibold text-ink">Active fires</b>
          <span className={compact ? 'hidden' : ''}>· {SENSOR_CHIP[sensor]} · last 24 h</span>
          {data && (
            <span className="text-faint">
              · {visibleFires.length} fires · {fmtInt(data.detections.filter((d) => d.t <= t).length)} detections
            </span>
          )}
        </div>
        {aoi && !compact && (
          <div className={`${glass} pointer-events-auto flex items-center gap-2 px-3 py-1.5 text-xs text-muted`}>
            <SquareDashed className="size-3.5 text-fire" aria-hidden />
            Area: {fmtInt(aoi.properties?.areaKm2)} km² · {aoiFires} fires inside
            <button type="button" onClick={() => setAoi(null)} aria-label="Clear area" className="text-faint hover:text-ink">
              <X className="size-3.5" />
            </button>
          </div>
        )}
      </div>

      {drawing && (
        <div className={`${glass} pointer-events-none absolute left-1/2 top-3 z-10 -translate-x-1/2 px-3 py-2 text-xs text-ink`}>
          Drag on the map to draw an area · <span className="text-muted">Esc to cancel</span>
        </div>
      )}

      {/* top-right tools */}
      {compact ? (
        <Link
          to="/dashboard/map"
          className={`${glass} absolute right-3 top-3 z-10 flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-ink hover:text-orange-200`}
        >
          Open full map <ArrowUpRight className="size-3.5" aria-hidden />
        </Link>
      ) : (
        <div className="absolute right-3 top-3 z-10 flex gap-1.5">
          <ToolButton label="Draw an area of interest" active={drawing} onClick={() => setDrawing((d) => !d)}>
            <SquareDashed className="size-4" />
          </ToolButton>
          <ToolButton label="Fit to region" onClick={() => data && mapRef.current?.fitBounds(data.bounds, { padding: [24, 24] })}>
            <LocateFixed className="size-4" />
          </ToolButton>
          <ToolButton label={fullscreen ? 'Exit full screen' : 'Full screen'} onClick={toggleFullscreen}>
            {fullscreen ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
          </ToolButton>
        </div>
      )}

      {/* bottom-left: time slider */}
      {!compact && win && (
        <div className={`${glass} absolute bottom-4 left-3 z-10 flex w-[min(560px,calc(100%-13rem))] items-center gap-3 py-2 pl-2 pr-4`}>
          <button
            type="button"
            onClick={togglePlay}
            aria-label={playing ? 'Pause' : 'Play the last 24 hours'}
            className="grid size-8 shrink-0 place-items-center rounded-full bg-info text-[#04121c]"
          >
            {playing ? <Pause className="size-3.5 fill-current" /> : <Play className="ml-0.5 size-3.5 fill-current" />}
          </button>
          <div className="relative h-10 flex-1">
            <div className="absolute inset-x-0 bottom-4 flex h-2.5 items-end justify-between" aria-hidden>
              {Array.from({ length: 25 }, (_, i) => (
                <i key={i} className={`w-px ${i % 6 === 0 ? 'h-2.5 bg-faint' : 'h-1.5 bg-line'}`} />
              ))}
            </div>
            <div className="absolute bottom-4 left-0 h-0.5 bg-info" style={{ width: `${pct}%` }} aria-hidden />
            <div className="absolute bottom-3 h-5 w-[3px] -translate-x-1/2 rounded bg-white" style={{ left: `${pct}%` }} aria-hidden>
              <span className="absolute bottom-6 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md bg-ink px-1.5 py-0.5 font-mono text-[10.5px] font-semibold text-bg">
                {cursor == null ? 'Live' : fmtUtc(t, { date: false, utc: false })}
              </span>
            </div>
            <div className="absolute inset-x-0 -bottom-0.5 flex justify-between font-mono text-[10px] text-faint" aria-hidden>
              <span>{fmtUtc(win.start)}</span>
              <span>{fmtUtc(win.end)}</span>
            </div>
            <input
              type="range"
              min={win.start}
              max={win.end}
              step={60000}
              value={Math.min(t, win.end)}
              onChange={(e) => {
                setPlaying(false)
                const v = +e.target.value
                setCursor(v >= win.end - 60000 ? null : v)
              }}
              aria-label="Time within the last 24 hours"
              aria-valuetext={cursor == null ? 'Live' : fmtUtc(t)}
              className="absolute inset-0 w-full cursor-pointer opacity-0"
            />
          </div>
        </div>
      )}

      {/* bottom-right: legend, layer toggles, zoom */}
      <div className="absolute bottom-6 right-3 z-10 flex items-end gap-1.5">
        {!compact && (
          <div className="relative">
            {legendOpen && (
              <div className={`${glass} absolute bottom-11 right-0 w-60 p-3 text-xs text-muted`}>
                <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-faint">Legend</p>
                <p className="flex items-center gap-2 py-1">
                  <Flame className="size-4 text-fire" aria-hidden /> Active fire cluster · size = FRP
                </p>
                <p className="flex items-center gap-2 py-1">
                  <span className="size-3.5 rounded-full bg-[radial-gradient(circle,#ffd27a,#f97316_45%,transparent_72%)]" />
                  Hotspot detection · fades with age
                </p>
                <p className="flex items-center gap-2 py-1">
                  <span className="size-3.5 rounded-full border-2 border-info" /> Selected fire
                </p>
                <p className="flex items-center gap-2 py-1">
                  <span className="h-3 w-3.5 rounded-sm border-[1.5px] border-dashed border-orange-400 bg-fire/10" /> Area of interest
                </p>
              </div>
            )}
            <button
              type="button"
              onClick={() => setLegendOpen((o) => !o)}
              aria-expanded={legendOpen}
              className={`${glass} flex h-9 items-center gap-1.5 px-3 text-xs text-muted hover:text-ink`}
            >
              Legend {legendOpen ? <ChevronDown className="size-3.5" /> : <ChevronUp className="size-3.5" />}
            </button>
          </div>
        )}
        {!compact && (
          <>
            <ToolButton label="Fire clusters" active={show.fires} onClick={() => setShow((s) => ({ ...s, fires: !s.fires }))}>
              <Flame className="size-4" />
            </ToolButton>
            <ToolButton
              label="Hotspot detections"
              active={show.detections}
              onClick={() => setShow((s) => ({ ...s, detections: !s.detections }))}
            >
              <Layers className="size-4" />
            </ToolButton>
          </>
        )}
        <div className={`${glass} flex flex-col overflow-hidden`}>
          <button type="button" aria-label="Zoom in" onClick={() => mapRef.current?.zoomIn()} className="grid size-9 place-items-center text-muted hover:text-ink">
            <Plus className="size-4" />
          </button>
          <button
            type="button"
            aria-label="Zoom out"
            onClick={() => mapRef.current?.zoomOut()}
            className="grid size-9 place-items-center border-t border-line/80 text-muted hover:text-ink"
          >
            <Minus className="size-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
