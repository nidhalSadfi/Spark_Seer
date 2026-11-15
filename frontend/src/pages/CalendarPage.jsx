import { useState } from 'react'
import { Card, CardHeader, PageHeader } from '../components/ui/primitives'
import { DataSourceNotice, EmptyAreaNotice } from '../components/common/DataSource'
import FireCalendar from '../components/calendar/FireCalendar'
import CalendarDetails from '../components/calendar/CalendarDetails'
import { useMonthlyMatrix } from '../hooks/useFireData'
import { useFilters } from '../context/FilterContext'
import { FLAGS, PARTIAL_BORDER, sensorLabel } from '../services/constants'

export default function CalendarPage() {
  const { target, sensor, startYear, endYear } = useFilters()
  const { data: matrix } = useMonthlyMatrix()
  const [selected, setSelected] = useState(null)

  return (
    <>
      <PageHeader
        kicker="Core view"
        title="Fire Activity Calendar"
        description={`When does burning occur in ${target.name}, and which years are unusual? ${sensorLabel(sensor)} hotspot counts by year and month.`}
      />
      {matrix?.meta.empty && (
        <div className="mb-4">
          <EmptyAreaNotice note={matrix.meta.note} />
        </div>
      )}
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Year × month activity" subtitle="Cell color = hotspot count. Click a cell for details." />
          <p className="-mt-2 mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
            {sensor === 'harmonized' && (
              <>
                <span className="inline-flex items-center gap-1.5">
                  <span className="size-3 rounded-sm border-2 border-fire-high" aria-hidden /> {FLAGS.high.label}
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="size-3 rounded-sm border-2 border-info" aria-hidden /> {FLAGS.low.label}
                </span>
              </>
            )}
            <span className="inline-flex items-center gap-1.5" title="Near-real-time month that is not complete yet: the count will still grow">
              <span className="size-3 rounded-sm border-2 border-dashed" style={{ borderColor: PARTIAL_BORDER }} aria-hidden /> Partial month
            </span>
            <span className="inline-flex items-center gap-1.5" title="No data loaded for this month (or it is in the future): unknown, not zero">
              <span className="size-3 rounded-sm border border-line bg-bg" aria-hidden /> Blank = no data, not zero
            </span>
            {sensor === 'harmonized' && (
              <span className="text-faint">Outlined: robust z-score ≥ 3 vs. the same month in other years</span>
            )}
          </p>
          <FireCalendar
            matrix={matrix}
            startYear={startYear}
            endYear={endYear}
            selected={selected}
            onSelect={setSelected}
          />
        </Card>
        <Card delay={0.1}>
          <CardHeader kicker="Inspector" title="Cell details" />
          <CalendarDetails key={target.key} matrix={matrix} selected={selected} sensor={sensor} />
        </Card>
      </div>
      <div className="mt-4">
        <DataSourceNotice />
      </div>
    </>
  )
}
