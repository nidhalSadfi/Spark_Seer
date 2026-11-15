import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import './fireMap.css'
import { toast } from 'sonner'
import {
  ArrowUpRight,
  ChevronDown,
  ChevronUp,
  Flame,
  LoaderCircle,
  LocateFixed,
  Map as MapIcon,
  MapPinPlus,
  Maximize2,
  Minimize2,
  Minus,
  Pause,
  Play,
  Plus,
  Satellite,
  Scan,
  Sparkles,
  SquareDashed,
  X,
} from 'lucide-react'
import { useFilters } from '../../context/FilterContext'
import { useActiveFires } from '../../hooks/useFireData'
import { useReports } from '../../hooks/useReports'
import { fmtInt } from '../../utils/format'
import { haversineKm } from '../../utils/geo'
import { observedLabel, timeAgo } from '../../utils/reports'
import {
  AOI_STYLE,
  GlowLayer,
  boundsToFeature,
  createBaseMap,
  draftIcon,
  fireIcon,
  fireLabelIcon,
  inAoi,
  reportIcon,
  reportLabelIcon,
  setBasemap,
} from './leafletLayers'
import { fmtUtc } from './mapConfig'

const SENSOR_CHIP = {
  harmonized: 'VIIRS 375 m + MODIS 1 km',
  modis: 'MODIS 1 km only',
  viirs: 'VIIRS 375 m only',
}

const glass = 'rounded-xl border border-line/80 bg-[#0d1524]/85 shadow-[0_10px_26px_rgba(0,0,0,.4)] backdrop-blur-md'

// Wide views can hold thousands of fires; drawing a marker for each makes the map sluggish. The
// strongest ones get a marker, every one still glows.
const MAX_MARKERS = 500

// the base map the user picked, remembered in this browser
const BASEMAP_KEY = 'spark-seer.basemap'
function savedBasemap() {
  try {
    return localStorage.getItem(BASEMAP_KEY) === 'satellite' ? 'satellite' : 'dark'
  } catch {
    return 'dark'
  }
}
function saveBasemap(kind) {
  try {
    localStorage.setItem(BASEMAP_KEY, kind)
  } catch {
    /* storage blocked: the choice lasts until the page is closed */
  }
}

const LOCATE_ERRORS = {
  1: 'Location access was blocked. Allow it for this site in your browser settings, then try again.',
  2: 'Your position is not available right now. Try again in a moment.',
  3: 'Finding your position took too long. Try again.',
}

const fmtKm = (km) => (km < 10 ? `${km.toFixed(1)} km` : `${fmtInt(km)} km`)

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
 * Live fire map (Leaflet; Esri dark map or satellite imagery, switchable and remembered).
 *
 * Shows near-real-time fire clusters (flame markers sized by FRP; from GET /fires/live, or the
 * illustrative mock feed when the API is unreachable) over a glow layer of
 * individual hotspot detections, with a 24 h time slider, layer toggles, legend and
 * rectangle AOI drawing.
 *
 * Integration contract (unchanged):
 *   - Reads region + sensor from `useFilters()`; data comes from `useActiveFires()`.
 *   - Drawing / clearing an area calls `setAoi(feature | null)`.
 *
 * Community fire reports (GET /reports, or browser-local when the API is unreachable; see
 * services/reportsStore.js) are drawn on top:
 * unverified ones as a "?", confirmed ones as a flame with a tick, dismissed ones not at all.
 *
 * Props:
 *   height     – CSS height of the map (default 420)
 *   compact    – preview mode for the Overview card (no slider / legend / drawing)
 *   selectedId – controlled selected fire or report id (optional)
 *   onSelect   – called with a fire or report id when a marker is clicked (optional)
 *   reportMode / onReportModeChange – "pick the spot of a new alert" mode (the button is hidden without the handler)
 *   draft      – {lat, lon} of the spot being reported, drawn as a pulsing "?"
 *   onPick     – called with (lat, lon) when the map is clicked in report mode
 *   focus      – {key, lat, lon}: centre the map there once (e.g. when opened from the reports page)
 */
export default function FireMap({
  height = 420,
  compact = false,
  selectedId,
  onSelect,
  reportMode = false,
  onReportModeChange,
  draft = null,
  onPick,
  focus = null,
}) {
  const { sensor, aoi, setAoi } = useFilters()
  const { data, isLoading, error } = useActiveFires()
  const { reports } = useReports()

  const wrapRef = useRef(null)
  const elRef = useRef(null)
  const mapRef = useRef(null)
  const layersRef = useRef(null)
  const fittedRef = useRef(null)
  const focusedRef = useRef(null)
  const prevSelRef = useRef(null)
  const cursorRef = useRef(null)
  const selectRef = useRef(null)
  const pickRef = useRef(null)
  const modeRef = useRef(null)

  const [ownSel, setOwnSel] = useState(null)
  const controlled = selectedId !== undefined
  const sel = controlled ? selectedId : ownSel

  const [cursor, setCursor] = useState(null) // null = live (end of window)
  const [playing, setPlaying] = useState(false)
  const [show, setShow] = useState({ fires: true, detections: true })
  const [legendOpen, setLegendOpen] = useState(false)
  const [drawing, setDrawing] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)
  const [basemap, setBasemapKind] = useState(savedBasemap)
  const basemapRef = useRef(basemap)
  const [me, setMe] = useState(null) // { lat, lon, accuracy } once located
  const [locating, setLocating] = useState(false)

  const win = data?.window
  const t = cursor ?? win?.end ?? Infinity

  useEffect(() => {
    selectRef.current = controlled ? onSelect : setOwnSel
    cursorRef.current = cursor
    pickRef.current = onPick
    modeRef.current = onReportModeChange
  })

  // ── map lifecycle ──
  useEffect(() => {
    const map = createBaseMap(elRef.current, { interactive: !compact, basemap: basemapRef.current })
    map.setView([15, 10], 2)
    const glow = new GlowLayer().addTo(map)
    const markers = L.layerGroup().addTo(map)
    const reportMarkers = L.layerGroup().addTo(map)
    const aoiLayer = L.layerGroup().addTo(map)
    const meLayer = L.layerGroup().addTo(map)
    mapRef.current = map
    layersRef.current = { glow, markers, reportMarkers, aoiLayer, meLayer }
    fittedRef.current = null
    focusedRef.current = null
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

  // centre on a place asked for from outside (runs after the fit above, so it wins on first load)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !focus || !data || focusedRef.current === focus.key) return
    focusedRef.current = focus.key
    map.setView([focus.lat, focus.lon], Math.max(map.getZoom(), 9), { animate: false })
  }, [focus, data, compact])

  // ── base map: dark map or satellite imagery ──
  useEffect(() => {
    basemapRef.current = basemap
    if (mapRef.current) setBasemap(mapRef.current, basemap)
  }, [basemap, compact])
  const toggleBasemap = () => {
    const next = basemap === 'satellite' ? 'dark' : 'satellite'
    setBasemapKind(next)
    saveBasemap(next)
  }

  // ── my location: a blue dot with its accuracy circle ──
  useEffect(() => {
    const ls = layersRef.current
    if (!ls) return
    ls.meLayer.clearLayers()
    if (!me) return
    L.circle([me.lat, me.lon], {
      radius: me.accuracy,
      color: '#38bdf8',
      weight: 1,
      fillColor: '#38bdf8',
      fillOpacity: 0.1,
      interactive: false,
    }).addTo(ls.meLayer)
    L.circleMarker([me.lat, me.lon], { radius: 7, color: '#ffffff', weight: 2.5, fillColor: '#0ea5e9', fillOpacity: 1 })
      .bindTooltip(`You are here (within ${fmtInt(me.accuracy)} m)`, { direction: 'top', offset: [0, -6] })
      .addTo(ls.meLayer)
  }, [me, compact])

  const locate = () => {
    if (!navigator.geolocation) {
      toast.error('This browser cannot share your location.')
      return
    }
    if (!window.isSecureContext) {
      toast.error('Your browser only shares your location with secure (https) pages.')
      return
    }
    setLocating(true)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false)
        const here = { lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy: Math.max(5, Math.round(pos.coords.accuracy || 0)) }
        setMe(here)
        const map = mapRef.current
        if (!map) return
        // frame the accuracy circle when it is wide, else zoom in on the dot
        const view = L.latLng(here.lat, here.lon).toBounds(Math.max(here.accuracy * 2.5, 3000))
        map.flyToBounds(view, { maxZoom: 12, duration: 0.9 })
      },
      (err) => {
        setLocating(false)
        toast.error(LOCATE_ERRORS[err.code] ?? 'Could not find your position.')
      },
      { enableHighAccuracy: true, timeout: 12_000, maximumAge: 60_000 },
    )
  }

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
    // the fires come strongest first: the first MAX_MARKERS visible ones get a marker (and the selected one)
    let drawn = 0
    for (const f of data.fires) {
      if (f.firstDetected > t) continue
      if (drawn >= MAX_MARKERS && f.id !== sel) continue
      drawn++
      const isSel = f.id === sel
      const m = L.marker([f.lat, f.lon], {
        icon: fireIcon(f, isSel),
        keyboard: false,
        title: f.frp == null ? f.name : `${f.name} · ${Math.round(f.frp)} MW`,
        zIndexOffset: isSel ? 1000 : Math.round(f.frp ?? 0),
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

  // ── community reports: "?" while unverified, a flame once confirmed, nothing once dismissed ──
  useEffect(() => {
    const ls = layersRef.current
    if (!ls) return
    ls.reportMarkers.clearLayers()
    for (const r of reports) {
      if (r.status === 'false') continue
      const isSel = r.id === sel
      const m = L.marker([r.lat, r.lon], {
        icon: reportIcon(r, isSel),
        keyboard: false,
        title: r.status === 'confirmed' ? 'Confirmed fire report' : 'Unverified fire report (not confirmed yet)',
        zIndexOffset: isSel ? 1500 : 500,
      })
      m.on('click', () => selectRef.current?.(r.id))
      ls.reportMarkers.addLayer(m)
      if (isSel && !compact) {
        const text = `${observedLabel(r.observed)} · reported ${timeAgo(r.createdAt)}`
        ls.reportMarkers.addLayer(
          L.marker([r.lat, r.lon], { icon: reportLabelIcon(r, text), interactive: false, keyboard: false, zIndexOffset: 2500 }),
        )
      }
    }
    if (draft) {
      ls.reportMarkers.addLayer(
        L.marker([draft.lat, draft.lon], { icon: draftIcon(), interactive: false, keyboard: false, zIndexOffset: 3000 }),
      )
    }
  }, [reports, sel, draft, compact])

  // bring a spot typed in (or located) outside the map into view
  useEffect(() => {
    const map = mapRef.current
    if (!map || !draft) return
    if (!map.getBounds().pad(-0.1).contains([draft.lat, draft.lon])) {
      map.flyTo([draft.lat, draft.lon], Math.max(map.getZoom(), 7), { duration: 0.8 })
    }
  }, [draft])

  // fly to a fire or report picked outside the map (e.g. from a details list) when it is off-screen
  useEffect(() => {
    const map = mapRef.current
    const prev = prevSelRef.current
    prevSelRef.current = sel
    const f = data?.fires.find((x) => x.id === sel) ?? reports.find((x) => x.id === sel)
    if (!map || !f || prev == null || prev === sel) return
    if (!map.getBounds().pad(-0.1).contains([f.lat, f.lon])) {
      map.flyTo([f.lat, f.lon], Math.max(map.getZoom(), 5), { duration: 0.8 })
    }
  }, [sel, data, reports])

  // ── report mode: click the map to say where the fire is ──
  useEffect(() => {
    const map = mapRef.current
    if (!map || !reportMode || compact) return
    map.getContainer().style.cursor = 'crosshair'
    const click = (e) => {
      const p = e.latlng.wrap()
      pickRef.current?.(+p.lat.toFixed(4), +p.lng.toFixed(4))
    }
    const key = (e) => e.key === 'Escape' && modeRef.current?.(false)
    map.on('click', click)
    window.addEventListener('keydown', key)
    return () => {
      map.off('click', click)
      window.removeEventListener('keydown', key)
      map.getContainer().style.cursor = ''
    }
  }, [reportMode, compact])

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
  const openReports = reports.filter((r) => r.status !== 'false')
  const unverified = openReports.filter((r) => r.status === 'pending').length
  const pct = win ? ((t - win.start) / (win.end - win.start)) * 100 : 100
  const grid = data?.mode === 'grid'
  const detectionCount = data ? (grid ? data.meta.count : data.detections.filter((d) => d.t <= t).length) : 0
  let nearest = null
  if (me) {
    for (const f of visibleFires) {
      const km = haversineKm(me.lat, me.lon, f.lat, f.lon)
      if (!nearest || km < nearest.km) nearest = { fire: f, km }
    }
  }

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
      {error && !data && (
        <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center px-6 text-center text-sm text-muted">
          Could not load active fires: {error.message}
        </div>
      )}

      {/* top-left: layer chip + AOI chip */}
      <div className="pointer-events-none absolute left-3 top-3 z-10 flex flex-col items-start gap-2">
        <div className={`${glass} flex max-w-[calc(100%-12.5rem)] flex-wrap items-center gap-x-2 gap-y-0.5 px-3 py-2 text-xs text-muted`}>
          <Flame className="size-4 text-faint" aria-hidden />
          <b className="font-semibold text-ink">Active fires</b>
          <span className={compact ? 'hidden' : ''}>· {SENSOR_CHIP[sensor]} · last 24 h</span>
          {data && (
            <span className="text-faint">
              · {fmtInt(visibleFires.length)} {grid ? `squares of ${data.meta.resolution}°` : 'fires'} · {fmtInt(detectionCount)} detections
              {visibleFires.length > MAX_MARKERS && ` · strongest ${MAX_MARKERS} marked`}
            </span>
          )}
          {!compact && openReports.length > 0 && (
            <span className="text-faint">
              · {openReports.length} {openReports.length === 1 ? 'report' : 'reports'}
              {unverified > 0 && <span className="text-yellow-300"> ({unverified} unverified)</span>}
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
        {me && !compact && (
          <div className={`${glass} pointer-events-auto flex flex-wrap items-center gap-x-2 gap-y-0.5 px-3 py-1.5 text-xs text-muted`}>
            <span className="size-2.5 rounded-full border-2 border-white bg-sky-500" aria-hidden />
            Your location
            {nearest ? (
              <button
                type="button"
                onClick={() => selectRef.current?.(nearest.fire.id)}
                className="text-faint underline-offset-2 hover:text-ink hover:underline"
                title="Select the nearest fire"
              >
                · nearest fire {fmtKm(nearest.km)} away
              </button>
            ) : (
              data && <span className="text-faint">· no fire in view</span>
            )}
            <button type="button" onClick={() => setMe(null)} aria-label="Hide your location" className="text-faint hover:text-ink">
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
      {reportMode && !compact && (
        <div className={`${glass} pointer-events-none absolute left-1/2 top-3 z-10 -translate-x-1/2 px-3 py-2 text-xs text-ink`}>
          Click where you see fire or smoke · <span className="text-muted">Esc to cancel</span>
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
          {onReportModeChange && (
            <ToolButton
              label="Report a fire or smoke"
              active={reportMode}
              onClick={() => {
                setDrawing(false)
                onReportModeChange(!reportMode)
              }}
            >
              <MapPinPlus className="size-4" />
            </ToolButton>
          )}
          <ToolButton
            label="Draw an area of interest"
            active={drawing}
            onClick={() => {
              onReportModeChange?.(false)
              setDrawing((d) => !d)
            }}
          >
            <SquareDashed className="size-4" />
          </ToolButton>
          <ToolButton label="Zoom to the selected area" onClick={() => data && mapRef.current?.fitBounds(data.bounds, { padding: [24, 24] })}>
            <Scan className="size-4" />
          </ToolButton>
          <ToolButton label={locating ? 'Finding your location…' : 'Show my location'} active={Boolean(me)} onClick={locate}>
            {locating ? <LoaderCircle className="size-4 animate-spin" /> : <LocateFixed className="size-4" />}
          </ToolButton>
          <ToolButton label={fullscreen ? 'Exit full screen' : 'Full screen'} onClick={toggleFullscreen}>
            {fullscreen ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
          </ToolButton>
        </div>
      )}

      {/* bottom-left: time slider */}
      {/* (a grid of squares has no per-detection times to play back) */}
      {!compact && win && !grid && (
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
                  <span className="grid size-4 shrink-0 place-items-center rounded-full border border-dashed border-yellow-300 bg-yellow-300/15 text-[10px] font-extrabold leading-none text-yellow-200">
                    ?
                  </span>
                  Community alert · not confirmed yet
                </p>
                <p className="flex items-center gap-2 py-1">
                  <span className="relative grid size-4 shrink-0 place-items-center">
                    <Flame className="size-4 text-fire" aria-hidden />
                    <i className="absolute -bottom-0.5 -right-0.5 size-2 rounded-full border border-[#0d1524] bg-ok" />
                  </span>
                  Community fire · confirmed by a reviewer
                </p>
                <p className="flex items-center gap-2 py-1">
                  <span className="size-3.5 rounded-full border-2 border-info" /> Selected fire
                </p>
                <p className="flex items-center gap-2 py-1">
                  <span className="h-3 w-3.5 rounded-sm border-[1.5px] border-dashed border-orange-400 bg-fire/10" /> Area of interest
                </p>
                <p className="flex items-center gap-2 py-1">
                  <span className="size-3 rounded-full border-2 border-white bg-sky-500" /> Your location
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
            <button
              type="button"
              onClick={toggleBasemap}
              aria-pressed={basemap === 'satellite'}
              title={basemap === 'satellite' ? 'Switch to the map' : 'Switch to satellite view'}
              className={`${glass} flex h-9 items-center gap-1.5 px-3 text-xs font-medium text-ink hover:text-orange-200`}
            >
              {basemap === 'satellite' ? <MapIcon className="size-4" aria-hidden /> : <Satellite className="size-4" aria-hidden />}
              {basemap === 'satellite' ? 'Map' : 'Satellite'}
            </button>
            <ToolButton label="Show fire markers" active={show.fires} onClick={() => setShow((s) => ({ ...s, fires: !s.fires }))}>
              <Flame className="size-4" />
            </ToolButton>
            <ToolButton
              label="Show hotspot glow"
              active={show.detections}
              onClick={() => setShow((s) => ({ ...s, detections: !s.detections }))}
            >
              <Sparkles className="size-4" />
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
