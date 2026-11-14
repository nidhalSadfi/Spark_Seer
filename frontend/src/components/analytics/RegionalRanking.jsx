import { useMemo } from 'react'
import EChart, { axisStyle, tooltipStyle } from '../common/EChart'
import { Skeleton } from '../ui/primitives'
import { fmtInt } from '../../utils/format'

export default function RegionalRanking({ summaries }) {
  const option = useMemo(() => {
    if (!summaries) return null
    const sorted = [...summaries].sort((a, b) => a.current - b.current) // ascending so largest is on top
    return {
      grid: { left: 130, right: 28, top: 8, bottom: 8 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...tooltipStyle, valueFormatter: fmtInt },
      xAxis: { type: 'value', ...axisStyle, axisLabel: { ...axisStyle.axisLabel, formatter: (v) => `${v / 1000}k` } },
      yAxis: { type: 'category', data: sorted.map((r) => r.name), ...axisStyle },
      series: [
        {
          type: 'bar',
          data: sorted.map((r) => r.current),
          itemStyle: { color: '#f97316', borderRadius: [0, 3, 3, 0] },
          label: { show: true, position: 'right', color: '#a7b5c8', fontSize: 11, formatter: ({ value }) => fmtInt(value) },
        },
      ],
    }
  }, [summaries])

  if (!option) return <Skeleton className="h-[260px] w-full" />
  return <EChart option={option} height={260} ariaLabel="Regions ranked by latest-month hotspot count" />
}
