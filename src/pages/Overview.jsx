import { Activity, CalendarClock, Gauge, Scale } from 'lucide-react'
import { motion } from 'motion/react'
import { Card, CardHeader, IllustrativeNotice, PageHeader } from '../components/ui/primitives'
import KPICard from '../components/dashboard/KPICard'
import FireTrendChart from '../components/dashboard/FireTrendChart'
import TopRegions from '../components/dashboard/TopRegions'
import RecentActivity from '../components/dashboard/RecentActivity'
import FireMap from '../components/map/FireMap'
import { useMonthlyMatrix, useRegionSummaries } from '../hooks/useFireData'
import { MONTHS, anomalyPct, historicalMedian, latestObservation } from '../utils/stats'
import { anomalyTone } from '../utils/format'
import { regionName, sensorLabel } from '../services/constants'
import { useFilters } from '../context/FilterContext'

const container = { hidden: {}, show: { transition: { staggerChildren: 0.07 } } }

export default function Overview() {
  const { region, sensor } = useFilters()
  const { data: matrix, isLoading } = useMonthlyMatrix()
  const { data: summaries } = useRegionSummaries()

  let kpi = null
  if (matrix) {
    const last = latestObservation(matrix)
    const current = matrix.data[last.yearIdx][last.month]
    const baseline = historicalMedian(matrix, last.year, last.month)
    kpi = {
      label: `${MONTHS[last.month]} ${last.year}`,
      current,
      baseline,
      anomaly: anomalyPct(current, baseline),
      weekly: Math.round(current / 4.3),
    }
  }

  return (
    <>
      <PageHeader
        title="Global Overview"
        description={`${regionName(region)} · ${sensorLabel(sensor)} detections. A quick operational summary of current fire activity against the historical baseline.`}
      />

      <motion.div variants={container} initial="hidden" animate="show" className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <KPICard label="Active hotspots (7-day est.)" icon={Activity} value={kpi?.weekly} loading={isLoading} hint="Estimated from latest month" />
        <KPICard label="Latest month" icon={CalendarClock} value={kpi?.current} loading={isLoading} hint={kpi?.label} />
        <KPICard label="Historical median" icon={Scale} value={kpi?.baseline} loading={isLoading} hint="Same calendar month, prior years" />
        <KPICard
          label="Activity anomaly"
          icon={Gauge}
          value={kpi?.anomaly == null ? null : Math.round(kpi.anomaly)}
          suffix="%"
          signed
          tone={anomalyTone(kpi?.anomaly)}
          loading={isLoading}
          hint="vs. historical median"
        />
      </motion.div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Hotspot activity" subtitle="Interactive map preview" />
          <FireMap height={300} compact />
        </Card>
        <Card>
          <CardHeader title="Top regions" subtitle="Latest month, ranked by hotspot count" />
          <TopRegions summaries={summaries} />
        </Card>
        <Card className="xl:col-span-2">
          <CardHeader title="Monthly trend" subtitle="Last 36 months vs. historical median" />
          <FireTrendChart matrix={matrix} />
        </Card>
        <Card>
          <CardHeader title="Anomaly alerts" subtitle="Regions deviating ≥ 20% from median" />
          <RecentActivity summaries={summaries} />
        </Card>
      </div>

      <div className="mt-4">
        <IllustrativeNotice />
      </div>
    </>
  )
}
