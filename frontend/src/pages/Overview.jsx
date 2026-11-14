import { Activity, CalendarClock, Gauge, Scale } from 'lucide-react'
import { motion } from 'motion/react'
import { Card, CardHeader } from '../components/ui/primitives'
import { DataSourceNotice, EmptyAreaNotice } from '../components/common/DataSource'
import WeatherCard from '../components/dashboard/WeatherCard'
import HeroBanner from '../components/dashboard/HeroBanner'
import KPICard from '../components/dashboard/KPICard'
import FireTrendChart from '../components/dashboard/FireTrendChart'
import TopRegions from '../components/dashboard/TopRegions'
import RecentActivity from '../components/dashboard/RecentActivity'
import FireMap from '../components/map/FireMap'
import { useMonthlyMatrix, useRegionSummaries } from '../hooks/useFireData'
import { MONTHS, anomalyPct, historicalMedian, latestObservation } from '../utils/stats'
import { anomalyTone } from '../utils/format'
import { sensorLabel } from '../services/constants'
import { useFilters } from '../context/FilterContext'

const container = { hidden: {}, show: { transition: { staggerChildren: 0.07 } } }

export default function Overview() {
  const { target, sensor } = useFilters()
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
    }
  }

  return (
    <>
      <HeroBanner
        regionLabel={target.name}
        sensorLabel={sensorLabel(sensor)}
        latest={kpi?.label}
      />

      {matrix?.meta.empty && (
        <div className="mb-4">
          <EmptyAreaNotice note={matrix.meta.note} />
        </div>
      )}

      <motion.div variants={container} initial="hidden" animate="show" className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <KPICard label="Grid cells with detections" icon={Activity} value={matrix?.meta.cells} loading={isLoading} hint="0.5° cells overlapping the area" />
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
          <CardHeader kicker="Geospatial" title="Hotspot activity" subtitle="Interactive map preview · illustrative near-real-time layer" />
          <FireMap height={300} compact />
        </Card>
        <Card delay={0.08}>
          <CardHeader kicker="Ranking" title="Top regions" subtitle="Latest month, ranked by hotspot count" />
          <TopRegions summaries={summaries} />
        </Card>
        <Card className="xl:col-span-2">
          <CardHeader kicker="Time series" title="Monthly trend" subtitle="Last 36 months vs. historical median" />
          <FireTrendChart matrix={matrix} />
        </Card>
        <Card delay={0.08}>
          <CardHeader kicker="Watchlist" title="Anomaly alerts" subtitle="Regions deviating ≥ 20% from median" />
          <RecentActivity summaries={summaries} />
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader kicker="Context" title="Weather outlook" subtitle="Next 3 days for the selected area" />
        <WeatherCard />
      </Card>

      <div className="mt-4">
        <DataSourceNotice />
      </div>
    </>
  )
}
