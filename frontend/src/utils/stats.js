export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function median(values) {
  const v = values.filter((x) => x != null && !Number.isNaN(x)).sort((a, b) => a - b)
  if (!v.length) return null
  const mid = Math.floor(v.length / 2)
  return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2
}

/**
 * Anomaly (%) = ((current - historical median) / historical median) * 100
 * Returns null when the baseline is missing or zero (avoids division by zero).
 */
export function anomalyPct(current, baseline) {
  if (current == null || baseline == null || baseline === 0) return null
  return ((current - baseline) / baseline) * 100
}

/** Minimum number of prior years required before a baseline is considered meaningful. */
export const MIN_BASELINE_YEARS = 3

/**
 * Historical median for a calendar month, using only years strictly before `year`.
 * `matrix` is { years: number[], data: (number|null)[][] } with data[yearIdx][monthIdx].
 */
export function historicalMedian(matrix, year, month) {
  const prior = []
  matrix.years.forEach((y, i) => {
    if (y < year) prior.push(matrix.data[i][month])
  })
  const valid = prior.filter((x) => x != null)
  if (valid.length < MIN_BASELINE_YEARS) return null
  return median(valid)
}

/** Last [yearIdx, monthIdx] with data. */
export function latestObservation(matrix) {
  for (let i = matrix.years.length - 1; i >= 0; i--) {
    for (let m = 11; m >= 0; m--) {
      if (matrix.data[i][m] != null) return { yearIdx: i, year: matrix.years[i], month: m }
    }
  }
  return null
}

export function yearTotal(matrix, yearIdx) {
  const row = matrix.data[yearIdx]
  if (row.every((x) => x == null)) return null
  return row.reduce((a, b) => a + (b ?? 0), 0)
}
