import { useMemo } from 'react'
import EChart, { axisStyle, tooltipStyle } from '../common/EChart'
import { Badge, Skeleton } from '../ui/primitives'
import { useValidation } from '../../hooks/useFireData'

const BARS = [
  { key: 'error_pct_uncorrected', label: 'Raw MODIS (no correction)', color: '#5d6f89' },
  { key: 'error_pct_global_ratio', label: 'One global ratio', color: '#f59e0b' },
  { key: 'error_pct_cell_month_ratio', label: 'Per-cell, per-month ratio (used)', color: '#f97316' },
]

/** Held-out validation of the MODIS→VIIRS harmonization (GET /validation). */
export default function ValidationCard() {
  const { data: v, isLoading, error } = useValidation()

  const option = useMemo(() => {
    if (!v) return null
    return {
      grid: { left: 190, right: 48, top: 8, bottom: 8 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...tooltipStyle, valueFormatter: (x) => `${x}%` },
      xAxis: { type: 'value', ...axisStyle, axisLabel: { ...axisStyle.axisLabel, formatter: '{value}%' } },
      yAxis: { type: 'category', inverse: true, data: BARS.map((b) => b.label), ...axisStyle },
      series: [
        {
          type: 'bar',
          barWidth: 18,
          data: BARS.map((b) => ({ value: v[b.key], itemStyle: { color: b.color, borderRadius: [0, 3, 3, 0] } })),
          label: { show: true, position: 'right', color: '#a7b5c8', fontSize: 11, formatter: ({ value }) => `${value}%` },
        },
      ],
    }
  }, [v])

  if (isLoading) return <Skeleton className="h-48 w-full" />
  if (error) return <p className="text-sm text-muted">Validation report unavailable: {error.message}</p>

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge>Fit {v.train_years.join('–')}</Badge>
        <Badge>Tested {v.test_years.join('–')}</Badge>
        <Badge>{v.test_months} months scored</Badge>
        <Badge tone="info">k = {v.k_global}</Badge>
        {v.synthetic && <Badge tone="fire">Synthetic test data</Badge>}
      </div>
      <EChart option={option} height={150} ariaLabel="Held-out error of three ways to predict VIIRS from MODIS" />
      <p className="text-xs leading-relaxed text-muted">
        Mean absolute monthly error as a % of the mean observed VIIRS count, on years the model never saw. Lower is
        better. <strong className="text-ink">k</strong> is the overall VIIRS ÷ MODIS ratio over the training years.
      </p>
      {v.note && <p className="text-xs italic text-faint">{v.note}</p>}
    </div>
  )
}
