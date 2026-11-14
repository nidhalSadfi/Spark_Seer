/** Bounding box [minLon, minLat, maxLon, maxLat] of a GeoJSON Feature/Geometry, or null. */
export function bboxOf(geo) {
  let minLon = Infinity
  let minLat = Infinity
  let maxLon = -Infinity
  let maxLat = -Infinity
  const walk = (c) => {
    if (typeof c[0] === 'number') {
      minLon = Math.min(minLon, c[0])
      maxLon = Math.max(maxLon, c[0])
      minLat = Math.min(minLat, c[1])
      maxLat = Math.max(maxLat, c[1])
    } else {
      c.forEach(walk)
    }
  }
  const coords = geo?.geometry?.coordinates ?? geo?.coordinates
  if (!coords) return null
  walk(coords)
  if (!Number.isFinite(minLon)) return null
  const r = (n) => +n.toFixed(4)
  return [r(minLon), r(minLat), r(maxLon), r(maxLat)]
}
