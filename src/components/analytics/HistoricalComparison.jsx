import { useMemo } from 'react'
import EChart, { axisStyle, tooltipStyle } from '../common/EChart'
import { Skeleton } from '../ui/primitives'
import { fmtAxis, fmtInt } from '../../utils/format'

const SERIES = [
  { key: 'modis', name: 'MODIS (raw)', color: '#f59e0b', dash: 'solid' },
  { key: 'viirs', name: 'VIIRS (raw)', color: '#38bdf8', dash: 'solid' },
  { key: 'harmonized', name: 'Harmonized (estimate)', color: '#f97316', dash: 'solid', width: 3 },
]

/** Yearly totals per sensor, keeping raw observations visually separate from the harmonized estimate. */
export default function HistoricalComparison({ comparison, startYear, endYear }) {
  const option = useMemo(() => {
    if (!comparison) return null
    const idx = comparison.years.map((y, i) => (y >= startYear && y <= endYear ? i : -1)).filter((i) => i >= 0)
    return {
      grid: { left: 56, right: 16, top: 36, bottom: 28 },
      tooltip: { trigger: 'axis', ...tooltipStyle, valueFormatter: (v) => (v == null ? 'n/a' : fmtInt(v)) },
      legend: { top: 0, textStyle: { color: '#a7b5c8' }, itemWidth: 16, itemHeight: 8 },
      xAxis: { type: 'category', data: idx.map((i) => comparison.years[i]), ...axisStyle },
      yAxis: { type: 'value', ...axisStyle, axisLabel: { ...axisStyle.axisLabel, formatter: fmtAxis } },
      series: SERIES.map((s) => ({
        name: s.name,
        type: 'line',
        showSymbol: false,
        smooth: 0.15,
        data: idx.map((i) => comparison.totals[s.key][i]),
        lineStyle: { color: s.color, width: s.width ?? 2, type: s.dash },
        itemStyle: { color: s.color },
      })),
    }
  }, [comparison, startYear, endYear])

  if (!option) return <Skeleton className="h-[300px] w-full" />
  return <EChart option={option} height={300} ariaLabel="Yearly hotspot totals for MODIS, VIIRS and harmonized data" />
}
