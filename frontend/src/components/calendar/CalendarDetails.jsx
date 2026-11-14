import { useMemo } from 'react'
import { motion } from 'motion/react'
import { MousePointerClick } from 'lucide-react'
import EChart, { axisStyle, tooltipStyle } from '../common/EChart'
import { Badge } from '../ui/primitives'
import { MONTHS, anomalyPct, historicalMedian } from '../../utils/stats'
import { anomalyTone, fmtInt, fmtPct } from '../../utils/format'
import { sensorLabel } from '../../services/constants'

function Stat({ label, value, tone = 'text-ink' }) {
  return (
    <div className="rounded-xl border border-line bg-sidebar/60 p-3">
      <p className="text-[11px] uppercase tracking-wider text-faint">{label}</p>
      <p className={`mt-0.5 text-xl font-semibold tabular-nums ${tone}`}>{value}</p>
    </div>
  )
}

export default function CalendarDetails({ matrix, selected, sensor }) {
  const detail = useMemo(() => {
    if (!matrix || !selected) return null
    const yi = matrix.years.indexOf(selected.year)
    const count = matrix.data[yi]?.[selected.month]
    if (count == null) return null
    const baseline = historicalMedian(matrix, selected.year, selected.month)
    const series = matrix.years
      .map((y, i) => ({ y, v: matrix.data[i][selected.month] }))
      .filter((p) => p.v != null && p.y <= selected.year)
      .slice(-10)
    return { count, baseline, anomaly: anomalyPct(count, baseline), series }
  }, [matrix, selected])

  const chart = useMemo(() => {
    if (!detail) return null
    return {
      grid: { left: 44, right: 8, top: 10, bottom: 22 },
      tooltip: { trigger: 'axis', ...tooltipStyle, valueFormatter: fmtInt },
      xAxis: { type: 'category', data: detail.series.map((p) => String(p.y).slice(2)), ...axisStyle },
      yAxis: { type: 'value', ...axisStyle, axisLabel: { ...axisStyle.axisLabel, formatter: fmtInt } },
      series: [
        {
          type: 'bar',
          data: detail.series.map((p) => ({
            value: p.v,
            itemStyle: { color: p.y === selected.year ? '#f97316' : '#3b4b60', borderRadius: [3, 3, 0, 0] },
          })),
          markLine: detail.baseline == null ? { data: [] } : {
            symbol: 'none',
            label: { formatter: 'median', color: '#38bdf8', fontSize: 10 },
            lineStyle: { color: '#38bdf8', type: 'dashed' },
            data: [{ yAxis: detail.baseline }],
          },
        },
      ],
    }
  }, [detail, selected])

  if (!selected || !detail) {
    return (
      <div className="grid h-full min-h-48 place-items-center text-center text-sm text-muted">
        <div className="flex flex-col items-center gap-2">
          <MousePointerClick className="size-6 text-faint motion-safe:animate-pulse" aria-hidden />
          Select a calendar cell to see its statistics.
        </div>
      </div>
    )
  }

  return (
    <motion.div
      key={`${selected.year}-${selected.month}`}
      initial={{ opacity: 0, x: 14 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className="flex flex-col gap-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-lg font-semibold">
          {MONTHS[selected.month]} {selected.year}
        </h4>
        <Badge tone={sensor === 'harmonized' ? 'info' : 'neutral'}>{sensorLabel(sensor)}</Badge>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Stat label="Hotspots" value={fmtInt(detail.count)} />
        <Stat label="Hist. median" value={fmtInt(detail.baseline)} />
        <Stat label="Anomaly" value={fmtPct(detail.anomaly)} tone={anomalyTone(detail.anomaly)} />
        <Stat
          label="vs. prev. year"
          value={(() => {
            const prev = detail.series.find((p) => p.y === selected.year - 1)
            return prev ? fmtPct(anomalyPct(detail.count, prev.v)) : 'n/a'
          })()}
        />
      </div>
      <div>
        <p className="mb-1 text-xs text-muted">
          {MONTHS[selected.month]} counts, previous years
        </p>
        <EChart option={chart} height={150} ariaLabel={`${MONTHS[selected.month]} hotspot counts across years`} />
      </div>
      <p className="text-xs leading-relaxed text-faint">
        {sensor === 'harmonized'
          ? 'Harmonized estimate (method: placeholder-v0). Not directly comparable with raw MODIS or VIIRS counts.'
          : `Raw ${sensorLabel(sensor)} detections. Not directly comparable with other sensors.`}{' '}
        Baseline: median of this calendar month in all earlier years of the record.
      </p>
    </motion.div>
  )
}
