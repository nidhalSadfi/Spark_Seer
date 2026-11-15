import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import '../map/fireMap.css'
import { GlowLayer, createBaseMap, reportIcon } from '../map/leafletLayers'
import { CORROBORATION_KM } from '../../utils/reports'

/**
 * Small map centred on one report, so a reviewer can judge the place: the report's marker, the
 * radius searched for satellite hotspots, and those hotspots as glow (when a feed is passed in).
 * Mount it with `key={report.id}` so each report gets a fresh map.
 */
export default function ReportMiniMap({ report, feed, height = 240 }) {
  const { lat, lon, status } = report
  const elRef = useRef(null)
  const glowRef = useRef(null)

  useEffect(() => {
    const map = createBaseMap(elRef.current, { interactive: false })
    // frame the search radius (a square of side 2 x radius around the point)
    map.fitBounds(L.latLng(lat, lon).toBounds(CORROBORATION_KM * 2000), { padding: [6, 6], animate: false })
    L.circle([lat, lon], {
      radius: CORROBORATION_KM * 1000,
      color: '#38bdf8',
      weight: 1.5,
      dashArray: '5 5',
      fillColor: '#38bdf8',
      fillOpacity: 0.04,
      interactive: false,
    }).addTo(map)
    glowRef.current = new GlowLayer().addTo(map)
    L.marker([lat, lon], { icon: reportIcon({ status }, false), keyboard: false, interactive: false, zIndexOffset: 1000 }).addTo(map)
    const ro = new ResizeObserver(() => map.invalidateSize())
    ro.observe(elRef.current)
    return () => {
      ro.disconnect()
      glowRef.current = null
      map.remove()
    }
  }, [lat, lon, status])

  // declared after the map effect, so it runs after the map (and its glow layer) is rebuilt
  useEffect(() => {
    if (!glowRef.current || !feed) return
    glowRef.current.setData(feed.detections, feed.window.end, feed.window.end - feed.window.start)
  }, [feed, lat, lon, status])

  return (
    <div className="sg-map relative isolate overflow-hidden rounded-xl border border-line bg-bg" style={{ height }}>
      <div ref={elRef} className="absolute inset-0 z-0" />
      <p className="pointer-events-none absolute bottom-2 left-2 z-10 rounded-md bg-[#0d1524]/85 px-2 py-1 text-[10.5px] text-muted backdrop-blur-md">
        Dashed circle: {CORROBORATION_KM} km search radius
      </p>
    </div>
  )
}
