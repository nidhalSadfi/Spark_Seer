import { useMemo } from 'react'
import EChart, { axisStyle, tooltipStyle } from '../common/EChart'
import { Skeleton } from '../ui/primitives'
import { MONTHS, historicalMedian, latestObservation } from '../../utils/stats'
import { fmtInt } from '../../utils/format'

/**
 * Last 36 months up to the latest month with data, with the historical-median baseline overlaid.
 * Months without data ("none" coverage) stay in the axis as gaps in the line, never as zeros.
 */
export default function FireTrendChart({ matrix, months = 36 }) {
  const option = useMemo(() => {
    if (!matrix) return null
    const last = latestObservation(matrix)
    const points = []
    let started = false
    matrix.years.forEach((y, yi) =>
      matrix.data[yi].forEach((v, m) => {
        if (!last || yi > last.yearIdx || (yi === last.yearIdx && m > last.month)) return
        started ||= v != null
        if (started) points.push({ y, m, v, base: historicalMedian(matrix, y, m) })
      }),
    )
    const recent = points.slice(-months)
    const labels = recent.map((p) => `${MONTHS[p.m]} ${String(p.y).slice(2)}`)
    return {
      grid: { left: 48, right: 16, top: 24, bottom: 28 },
      tooltip: { trigger: 'axis', ...tooltipStyle, valueFormatter: (v) => (v == null ? 'n/a' : fmtInt(v)) },
      legend: { top: 0, right: 0, textStyle: { color: '#a7b5c8' }, itemWidth: 14, itemHeight: 8 },
      xAxis: { type: 'category', data: labels, ...axisStyle, axisLabel: { ...axisStyle.axisLabel, interval: 5 } },
      yAxis: { type: 'value', ...axisStyle, axisLabel: { ...axisStyle.axisLabel, formatter: (v) => fmtInt(v) } },
      series: [
        {
          name: 'Hotspots',
          type: 'line',
          smooth: 0.25,
          showSymbol: false,
          data: recent.map((p) => p.v),
          lineStyle: { color: '#f97316', width: 2.5 },
          itemStyle: { color: '#f97316' },
          areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: '#f9731655' }, { offset: 1, color: '#f9731600' }] } },
        },
        {
          name: 'Historical median',
          type: 'line',
          showSymbol: false,
          data: recent.map((p) => p.base),
          lineStyle: { color: '#38bdf8', width: 1.5, type: 'dashed' },
          itemStyle: { color: '#38bdf8' },
        },
      ],
    }
  }, [matrix, months])

  if (!option) return <Skeleton className="h-[260px] w-full" />
  return <EChart option={option} height={260} ariaLabel="Monthly hotspot trend versus historical median" />
}
