/** Great-circle distance in km between two points (degrees). */
export function haversineKm(lat1, lon1, lat2, lon2) {
  const rad = Math.PI / 180
  const dLat = (lat2 - lat1) * rad
  const dLon = (lon2 - lon1) * rad
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2
  return 12742 * Math.asin(Math.min(1, Math.sqrt(a)))
}

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
