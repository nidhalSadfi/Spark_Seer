import { useState } from 'react'
import { Card, CardHeader, IllustrativeNotice, Notice, PageHeader, Select } from '../components/ui/primitives'
import HistoricalComparison from '../components/analytics/HistoricalComparison'
import AnomalyChart from '../components/analytics/AnomalyChart'
import RegionalRanking from '../components/analytics/RegionalRanking'
import SeasonalDistribution from '../components/analytics/SeasonalDistribution'
import { useMonthlyMatrix, useRegionSummaries, useSensorComparison } from '../hooks/useFireData'
import { useFilters } from '../context/FilterContext'
import { DATA_RANGE } from '../services/fireApi'
import { regionName, sensorLabel } from '../services/constants'

export default function AnalyticsPage() {
  const { region, sensor, startYear, endYear } = useFilters()
  const { data: matrix } = useMonthlyMatrix()
  const { data: comparison } = useSensorComparison()
  const { data: summaries } = useRegionSummaries()
  const [focusYear, setFocusYear] = useState(DATA_RANGE.endYear)

  const yearOptions = []
  for (let y = DATA_RANGE.endYear; y > DATA_RANGE.startYear; y--) yearOptions.push({ value: y, label: String(y) })

  return (
    <>
      <PageHeader
        title="Historical Analytics"
        description={`Seasonality, long-term trends and unusual conditions for ${regionName(region)}.`}
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Yearly totals by sensor"
            subtitle="Raw MODIS and VIIRS counts are shown separately from the harmonized estimate; they are not directly comparable."
          />
          <HistoricalComparison comparison={comparison} startYear={startYear} endYear={endYear} />
        </Card>
        <Card>
          <CardHeader
            title="Monthly anomaly vs. historical median"
            subtitle={`${sensorLabel(sensor)} · % deviation from the median of all earlier years`}
            action={<Select label="Year" value={focusYear} onChange={(v) => setFocusYear(+v)} options={yearOptions} />}
          />
          <AnomalyChart matrix={matrix} year={focusYear} />
        </Card>
        <Card>
          <CardHeader title="Seasonal distribution" subtitle="Average share of annual hotspots per calendar month" />
          <SeasonalDistribution matrix={matrix} startYear={startYear} endYear={endYear} />
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader title="Regional ranking" subtitle="Latest month, all regions" />
          <RegionalRanking summaries={summaries} />
        </Card>
      </div>
      <div className="mt-4 flex flex-col gap-3">
        <Notice title="Anomaly formula.">
          Anomaly (%) = ((current − historical median) ÷ historical median) × 100. Shown as n/a when the baseline is
          zero or fewer than three prior years exist.
        </Notice>
        <IllustrativeNotice />
      </div>
    </>
  )
}
