import { useMemo } from 'react'
import EChart, { FIRE_SCALE, axisStyle, tooltipStyle } from '../common/EChart'
import { Skeleton } from '../ui/primitives'
import { MONTHS, anomalyPct, historicalMedian } from '../../utils/stats'
import { fmtInt, fmtPct } from '../../utils/format'

/**
 * Year × month heatmap of hotspot counts.
 * `selected` is { year, month } | null; `onSelect` receives the same shape.
 */
export default function FireCalendar({ matrix, startYear, endYear, selected, onSelect }) {
  const option = useMemo(() => {
    if (!matrix) return null
    const rows = matrix.years
      .map((y, yi) => ({ y, yi }))
      .filter(({ y }) => y >= startYear && y <= endYear)
      .reverse() // newest year on top
    const yLabels = rows.map((r) => String(r.y))
    const cells = []
    let max = 0
    rows.forEach(({ y, yi }, ri) =>
      matrix.data[yi].forEach((v, m) => {
        if (v == null) return
        max = Math.max(max, v)
        const isSel = selected && selected.year === y && selected.month === m
        cells.push({
          value: [m, ri, v],
          itemStyle: isSel ? { borderColor: '#ffffff', borderWidth: 2 } : undefined,
        })
      }),
    )
    return {
      grid: { left: 52, right: 16, top: 8, bottom: 56 },
      tooltip: {
        ...tooltipStyle,
        formatter: ({ value }) => {
          const [m, ri, v] = value
          const year = rows[ri].y
          const base = historicalMedian(matrix, year, m)
          return `<b>${MONTHS[m]} ${year}</b><br/>Hotspots: ${fmtInt(v)}<br/>Median: ${fmtInt(base)}<br/>Anomaly: ${fmtPct(anomalyPct(v, base))}`
        },
      },
      xAxis: { type: 'category', data: MONTHS, position: 'top', ...axisStyle, splitArea: { show: false } },
      yAxis: { type: 'category', data: yLabels, ...axisStyle, axisLabel: { ...axisStyle.axisLabel, interval: 0 } },
      visualMap: {
        min: 0,
        max: Math.max(max, 1),
        calculable: true,
        orient: 'horizontal',
        left: 'center',
        bottom: 4,
        itemWidth: 12,
        itemHeight: 220,
        textStyle: { color: '#a7b5c8' },
        inRange: { color: FIRE_SCALE },
        formatter: (v) => fmtInt(v),
      },
      series: [
        {
          type: 'heatmap',
          data: cells,
          itemStyle: { borderColor: '#0b1220', borderWidth: 2, borderRadius: 3 },
          emphasis: { itemStyle: { borderColor: '#fff', borderWidth: 1.5 } },
          label: { show: false },
          // cells "ignite" in a diagonal wave: older years first, January → December
          animationDuration: 500,
          animationDelay: (idx) => {
            const [m, ri] = cells[idx].value
            return (rows.length - 1 - ri) * 22 + m * 14
          },
        },
      ],
    }
  }, [matrix, startYear, endYear, selected])

  const rowsShown = Math.max(1, endYear - startYear + 1)
  const height = Math.max(240, rowsShown * 26 + 110)

  if (!option) return <Skeleton className="h-[420px] w-full" />
  return (
    <EChart
      option={option}
      height={height}
      ariaLabel="Heatmap of monthly hotspot counts by year. Select a cell for details."
      onEvents={{
        click: (p) => {
          if (p.componentSubType !== 'heatmap') return
          const [m, ri] = p.value
          const years = matrix.years.filter((y) => y >= startYear && y <= endYear).reverse()
          onSelect({ year: years[ri], month: m })
        },
      }}
    />
  )
}
