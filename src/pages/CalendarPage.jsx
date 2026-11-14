import { useState } from 'react'
import { Card, CardHeader, IllustrativeNotice, PageHeader } from '../components/ui/primitives'
import FireCalendar from '../components/calendar/FireCalendar'
import CalendarDetails from '../components/calendar/CalendarDetails'
import { useMonthlyMatrix } from '../hooks/useFireData'
import { useFilters } from '../context/FilterContext'
import { regionName, sensorLabel } from '../services/constants'

export default function CalendarPage() {
  const { region, sensor, startYear, endYear } = useFilters()
  const { data: matrix } = useMonthlyMatrix()
  const [selected, setSelected] = useState(null)

  return (
    <>
      <PageHeader
        kicker="Core view"
        title="Fire Activity Calendar"
        description={`When does burning occur in ${regionName(region)}, and which years are unusual? ${sensorLabel(sensor)} hotspot counts by year and month.`}
      />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Year × month activity" subtitle="Cell color = hotspot count. Click a cell for details." />
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
          <CalendarDetails matrix={matrix} selected={selected} sensor={sensor} />
        </Card>
      </div>
      <div className="mt-4">
        <IllustrativeNotice />
      </div>
    </>
  )
}
