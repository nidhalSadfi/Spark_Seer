import { useMemo } from 'react'
import EChart, { FIRE_SCALE, axisStyle, tooltipStyle } from '../common/EChart'
import { Skeleton } from '../ui/primitives'
import { MONTHS } from '../../utils/stats'

/** Share of annual hotspots falling in each calendar month, averaged over complete years. */
export default function SeasonalDistribution({ matrix, startYear, endYear }) {
  const option = useMemo(() => {
    if (!matrix) return null
    const shares = Array(12).fill(0)
    let n = 0
    matrix.years.forEach((y, yi) => {
      if (y < startYear || y > endYear) return
      const row = matrix.data[yi]
      if (row.some((v) => v == null)) return // only complete years
      const total = row.reduce((a, b) => a + b, 0)
      if (!total) return
      row.forEach((v, m) => (shares[m] += v / total))
      n++
    })
    const pct = shares.map((s) => (n ? +((s / n) * 100).toFixed(1) : 0))
    const max = Math.max(...pct, 1)
    return {
      grid: { left: 44, right: 12, top: 16, bottom: 28 },
      tooltip: { trigger: 'axis', ...tooltipStyle, valueFormatter: (v) => `${v}% of annual total` },
      xAxis: { type: 'category', data: MONTHS, ...axisStyle },
      yAxis: { type: 'value', ...axisStyle, axisLabel: { ...axisStyle.axisLabel, formatter: (v) => `${v}%` } },
      series: [
        {
          type: 'bar',
          data: pct.map((v) => ({
            value: v,
            itemStyle: { color: FIRE_SCALE[Math.min(4, Math.floor((v / max) * 4.99))], borderRadius: [3, 3, 0, 0] },
          })),
        },
      ],
    }
  }, [matrix, startYear, endYear])

  if (!option) return <Skeleton className="h-[260px] w-full" />
  return <EChart option={option} height={260} ariaLabel="Average share of annual hotspots by calendar month" />
}
