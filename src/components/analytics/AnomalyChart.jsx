import { useMemo } from 'react'
import EChart, { axisStyle, tooltipStyle } from '../common/EChart'
import { Skeleton } from '../ui/primitives'
import { MONTHS, anomalyPct, historicalMedian } from '../../utils/stats'
import { fmtPct } from '../../utils/format'

/** Diverging bars: monthly anomaly (%) for one year against the historical median. */
export default function AnomalyChart({ matrix, year }) {
  const option = useMemo(() => {
    if (!matrix) return null
    const yi = matrix.years.indexOf(year)
    const values = MONTHS.map((_, m) => anomalyPct(matrix.data[yi]?.[m], historicalMedian(matrix, year, m)))
    return {
      grid: { left: 48, right: 12, top: 16, bottom: 28 },
      tooltip: { trigger: 'axis', ...tooltipStyle, valueFormatter: (v) => fmtPct(v, 1) },
      xAxis: { type: 'category', data: MONTHS, ...axisStyle, axisLabel: { ...axisStyle.axisLabel, margin: 12 } },
      yAxis: { type: 'value', ...axisStyle, axisLabel: { ...axisStyle.axisLabel, formatter: (v) => `${v}%` } },
      series: [
        {
          name: 'Anomaly',
          type: 'bar',
          data: values.map((v) => ({
            value: v,
            // above normal = orange/red, below normal = cyan (diverging, not green/red)
            itemStyle: { color: v == null ? '#26364d' : v >= 50 ? '#ef4444' : v >= 0 ? '#f97316' : '#38bdf8', borderRadius: v >= 0 ? [3, 3, 0, 0] : [0, 0, 3, 3] },
          })),
          markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: '#7c8ca3' }, data: [{ yAxis: 0 }] },
        },
      ],
    }
  }, [matrix, year])

  if (!option) return <Skeleton className="h-[260px] w-full" />
  return <EChart option={option} height={260} ariaLabel={`Monthly anomaly for ${year} versus historical median`} />
}
