import { useState } from 'react'
import { Badge, Card, IllustrativeNotice, PageHeader } from '../components/ui/primitives'
import FireMap from '../components/map/FireMap'
import FireDetails from '../components/map/FireDetails'
import FirePassChart from '../components/map/FirePassChart'
import { fmtUtc } from '../components/map/mapConfig'
import { useActiveFires } from '../hooks/useFireData'

/**
 * Live fire monitor: selected-fire details (left), the map (centre) and the
 * satellite-pass / fire-weather chart for the selected fire (bottom).
 */
export default function FireMapPage() {
  const { data } = useActiveFires()
  const [selected, setSelected] = useState(null)
  const fires = data?.fires ?? []
  // Default to the most intense fire; fall back when the selection leaves the current region.
  const fire = fires.find((f) => f.id === selected) ?? fires[0] ?? null

  return (
    <>
      <PageHeader
        title="Global Fire Map"
        description="Near-real-time active fires from VIIRS and MODIS. Click a fire to inspect it, play back the last 24 hours, or draw an area to focus the other pages."
        actions={
          data && (
            <Badge tone="ok">
              <span className="size-1.5 rounded-full bg-ok" aria-hidden /> Updated {fmtUtc(data.snapshot)} · ~3 h latency
            </Badge>
          )
        }
      />

      <div className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        <FireDetails
          fire={fire}
          fires={fires}
          onSelect={setSelected}
          className="xl:row-span-2 xl:max-h-[calc(100vh-150px)] xl:overflow-y-auto"
        />
        <div className="min-w-0 overflow-hidden rounded-2xl border border-line">
          <FireMap height="clamp(420px, calc(100vh - 520px), 760px)" selectedId={fire?.id ?? null} onSelect={setSelected} />
        </div>
        <Card className="p-0!">
          <FirePassChart fire={fire} range={data?.chartRange} now={data?.snapshot} />
        </Card>
      </div>

      <div className="mt-4">
        <IllustrativeNotice />
      </div>
    </>
  )
}
