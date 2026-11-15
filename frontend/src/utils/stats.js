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

/** Coverage of one cell of a calendarToMatrix matrix ('full' when the matrix has none, e.g. a mock). */
export const cellCoverage = (matrix, yearIdx, month) => matrix.coverage?.[yearIdx]?.[month] ?? 'full'

/**
 * Historical median for a calendar month, using only years strictly before `year`.
 * `matrix` is { years: number[], data: (number|null)[][], coverage? } with data[yearIdx][monthIdx].
 * Months without data ("none" coverage: data is null, never a zero) and partial months (still
 * being observed, so undercounted) are left out of the baseline, as in the API's own z-score.
 */
export function historicalMedian(matrix, year, month) {
  const prior = []
  matrix.years.forEach((y, i) => {
    if (y < year && cellCoverage(matrix, i, month) === 'full') prior.push(matrix.data[i][month])
  })
  const valid = prior.filter((x) => x != null)
  if (valid.length < MIN_BASELINE_YEARS) return null
  return median(valid)
}

/**
 * Latest month that has data: { yearIdx, year, month, partial }, or null when nothing has data.
 * "none" months are null in the matrix, so a zero-filled future or not-yet-loaded month is never
 * "the latest observation"; `partial` says the month is still being observed.
 */
export function latestObservation(matrix) {
  for (let i = matrix.years.length - 1; i >= 0; i--) {
    for (let m = 11; m >= 0; m--) {
      if (matrix.data[i][m] != null && cellCoverage(matrix, i, m) !== 'none') {
        return { yearIdx: i, year: matrix.years[i], month: m, partial: cellCoverage(matrix, i, m) === 'partial' }
      }
    }
  }
  return null
}

export function yearTotal(matrix, yearIdx) {
  const row = matrix.data[yearIdx]
  if (row.every((x) => x == null)) return null
  return row.reduce((a, b) => a + (b ?? 0), 0)
}
