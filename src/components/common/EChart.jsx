import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, HeatmapChart, LineChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  BarChart,
  HeatmapChart,
  LineChart,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
])

const reduced = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** Thin ECharts wrapper: init once, update on option change, resize with container. */
export default function EChart({ option, onEvents, height = 280, ariaLabel }) {
  const ref = useRef(null)
  const chart = useRef(null)
  const events = useRef(onEvents)
  useEffect(() => {
    events.current = onEvents
  })

  useEffect(() => {
    const c = echarts.init(ref.current, null, { renderer: 'canvas' })
    chart.current = c
    c.on('click', (p) => events.current?.click?.(p))
    const ro = new ResizeObserver(() => c.resize())
    ro.observe(ref.current)
    return () => {
      ro.disconnect()
      c.dispose()
      chart.current = null
    }
  }, [])

  useEffect(() => {
    chart.current?.setOption(
      {
        backgroundColor: 'transparent',
        textStyle: { fontFamily: 'Inter Variable, Inter, system-ui, sans-serif', color: '#a7b5c8' },
        animation: !reduced(),
        animationDuration: 600,
        ...option,
      },
      { notMerge: true },
    )
  }, [option])

  return <div ref={ref} role="img" aria-label={ariaLabel} style={{ height, width: '100%' }} />
}

export const axisStyle = {
  axisLine: { lineStyle: { color: '#26364d' } },
  axisTick: { show: false },
  axisLabel: { color: '#a7b5c8', fontSize: 11 },
  splitLine: { lineStyle: { color: '#1f2e44' } },
}

export const tooltipStyle = {
  backgroundColor: '#0f1828',
  borderColor: '#2c405b',
  textStyle: { color: '#edf3fb', fontSize: 12 },
  extraCssText: 'border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.4)',
}

/** Sequential pale-yellow → orange → dark-red scale for fire counts. */
export const FIRE_SCALE = ['#fde68a', '#f59e0b', '#f97316', '#dc2626', '#7f1d1d']
