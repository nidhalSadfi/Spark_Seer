import L from 'leaflet'

const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas'
const ATTRIBUTION = 'Basemap © <a href="https://www.esri.com">Esri</a>, HERE, Garmin, © OpenStreetMap contributors'

export const AOI_STYLE = { color: '#fb923c', weight: 2, dashArray: '6 5', fillColor: '#f97316', fillOpacity: 0.07 }

/** Dark Esri basemap (no API key needed) with labels in their own pane above the data layers. */
export function createBaseMap(el, { interactive = true } = {}) {
  const map = L.map(el, {
    zoomControl: false,
    zoomSnap: 0.25,
    fadeAnimation: false,
    scrollWheelZoom: interactive,
    minZoom: 2,
    worldCopyJump: true,
  })
  map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>')
  map.createPane('labels')
  const labels = map.getPane('labels')
  labels.style.zIndex = 450
  labels.style.pointerEvents = 'none'
  L.tileLayer(`${ESRI}/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`, {
    maxNativeZoom: 16,
    maxZoom: 18,
    attribution: ATTRIBUTION,
    className: 'sg-basemap',
  }).addTo(map)
  L.tileLayer(`${ESRI}/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, {
    maxNativeZoom: 16,
    maxZoom: 18,
    pane: 'labels',
    opacity: 0.85,
  }).addTo(map)
  return map
}

let sprite
function glowSprite() {
  if (sprite) return sprite
  sprite = document.createElement('canvas')
  sprite.width = sprite.height = 64
  const g = sprite.getContext('2d')
  const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32)
  gr.addColorStop(0, 'rgba(255,246,220,1)')
  gr.addColorStop(0.12, 'rgba(255,205,120,0.95)')
  gr.addColorStop(0.34, 'rgba(255,115,45,0.45)')
  gr.addColorStop(0.68, 'rgba(210,50,20,0.12)')
  gr.addColorStop(1, 'rgba(180,30,10,0)')
  g.fillStyle = gr
  g.fillRect(0, 0, 64, 64)
  return sprite
}

/**
 * Canvas overlay that paints every hotspot detection as an additive glow.
 * Detections newer than the time cursor are hidden; older ones fade over the window.
 */
export const GlowLayer = L.Layer.extend({
  initialize() {
    this._points = []
    this._cursor = Infinity
    this._span = 24 * 3600000
  },
  setData(points, cursor, span) {
    this._points = points
    this._cursor = cursor
    this._span = span
    if (this._map) this._redraw()
    return this
  },
  onAdd(map) {
    this._map = map
    const c = (this._canvas = L.DomUtil.create('canvas', 'sg-glow'))
    c.style.position = 'absolute'
    c.style.pointerEvents = 'none'
    map.getPane('overlayPane').appendChild(c)
    map.on('moveend zoomend resize viewreset', this._reset, this)
    map.on('zoomstart', this._hide, this)
    this._reset()
  },
  onRemove(map) {
    L.DomUtil.remove(this._canvas)
    map.off('moveend zoomend resize viewreset', this._reset, this)
    map.off('zoomstart', this._hide, this)
  },
  _hide() {
    this._canvas.style.opacity = 0
  },
  _reset() {
    const map = this._map
    const size = map.getSize()
    if (!size.x || !size.y) return
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    const c = this._canvas
    L.DomUtil.setPosition(c, map.containerPointToLayerPoint([0, 0]))
    c.width = size.x * dpr
    c.height = size.y * dpr
    c.style.width = `${size.x}px`
    c.style.height = `${size.y}px`
    c.style.opacity = 1
    this._dpr = dpr
    this._redraw()
  },
  _redraw() {
    const map = this._map
    const c = this._canvas
    if (!c.width) return
    const ctx = c.getContext('2d')
    ctx.setTransform(this._dpr, 0, 0, this._dpr, 0, 0)
    ctx.clearRect(0, 0, c.width, c.height)
    ctx.globalCompositeOperation = 'lighter'
    const size = map.getSize()
    const k = Math.min(2.6, Math.max(0.55, 1.6 ** (map.getZoom() - 5)))
    const img = glowSprite()
    for (const d of this._points) {
      const age = this._cursor - d.t
      if (age < 0) continue
      const p = map.latLngToContainerPoint([d.lat, d.lon])
      if (p.x < -30 || p.y < -30 || p.x > size.x + 30 || p.y > size.y + 30) continue
      const s = (2.5 + Math.min(6, Math.sqrt(d.frp) * 1.2)) * k
      ctx.globalAlpha = 0.85 * Math.max(0.18, 1 - age / this._span)
      ctx.drawImage(img, p.x - s, p.y - s, 2 * s, 2 * s)
    }
  },
})

const FLAME =
  '<path d="M12 1.5c.6 3.2 3.9 5.4 5.6 8.6 1.9 3.6.4 9.4-5.6 10.4-6-1-7.5-6.8-5.6-10.4.8-1.5 2-2.6 2.7-4 .5 1.6 1.4 2.6 2.4 3.1C11.2 6.9 11.3 4 12 1.5z" fill="#ff7a36"/>' +
  '<path d="M12 12.2c.9 1.3 2.4 2.2 2.4 4.1 0 1.6-1.1 2.8-2.4 3.1-1.3-.3-2.4-1.5-2.4-3.1 0-1.9 1.5-2.8 2.4-4.1z" fill="#ffe7a3"/>'

const esc = (s) => String(s).replace(/[&<>"']/g, (ch) => `&#${ch.charCodeAt(0)};`)

/** Flame marker sized by Fire Radiative Power, with a pulsing ring when selected. */
export function fireIcon(fire, selected) {
  const s = Math.min(30, Math.max(14, 14 + fire.frp / 6))
  const h = Math.min(120, Math.max(34, 30 + fire.frp * 1.1))
  return L.divIcon({
    className: '',
    iconSize: [26, 26],
    iconAnchor: [13, 13],
    html: `<div class="sg-fire${selected ? ' is-selected' : ''}" style="--s:${s}px;--h:${h}px"><span class="sg-halo"></span>${
      selected ? '<span class="sg-core"></span>' : ''
    }<svg viewBox="0 0 24 24" aria-hidden="true">${FLAME}</svg></div>`,
  })
}

/** Floating label next to the selected fire. */
export function fireLabelIcon(fire) {
  return L.divIcon({
    className: '',
    iconSize: [0, 0],
    iconAnchor: [0, 0],
    html: `<div class="sg-label"><b>${esc(fire.name)}</b>${Math.round(fire.frp)} MW · FWI ${fire.fwi} · ${fire.persistence} passes</div>`,
  })
}

/** GeoJSON Feature for a drawn rectangle, as expected by FilterContext.setAoi. */
export function boundsToFeature(b) {
  const [w, s, e, n] = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => +v.toFixed(4))
  const mid = ((s + n) / 2) * (Math.PI / 180)
  const areaKm2 = Math.round((n - s) * 111.32 * (e - w) * 111.32 * Math.cos(mid))
  return {
    type: 'Feature',
    bbox: [w, s, e, n],
    properties: { source: 'map-draw', areaKm2 },
    geometry: { type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] },
  }
}

/** True when a point falls inside an AOI feature's bounding box. */
export function inAoi(aoi, lat, lon) {
  if (!aoi?.bbox) return true
  const [w, s, e, n] = aoi.bbox
  return lat >= s && lat <= n && lon >= w && lon <= e
}
