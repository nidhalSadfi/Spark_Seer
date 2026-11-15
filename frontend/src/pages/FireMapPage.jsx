import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { Badge, Card, IllustrativeNotice, PageHeader } from '../components/ui/primitives'
import FireMap from '../components/map/FireMap'
import FireDetails from '../components/map/FireDetails'
import FirePassChart from '../components/map/FirePassChart'
import ReportDetails from '../components/reports/ReportDetails'
import ReportForm from '../components/reports/ReportForm'
import { fmtUtc } from '../components/map/mapConfig'
import { useActiveFires } from '../hooks/useFireData'
import { useReports } from '../hooks/useReports'

const side = 'xl:row-span-2 xl:max-h-[calc(100vh-150px)] xl:overflow-y-auto'

/**
 * Live fire monitor: the side panel (selected fire, selected community report, or the form for a
 * new alert), the map (centre) and the satellite-pass / fire-weather chart for the selected fire.
 */
export default function FireMapPage() {
  const { data } = useActiveFires()
  const { reports, addReport } = useReports()
  const [params] = useSearchParams()
  const navigate = useNavigate()

  // opened from Fire Reports with ?report=<id>: select that report and centre the map on it
  const [focus] = useState(() => {
    const r = reports.find((x) => x.id === params.get('report'))
    return r ? { key: r.id, lat: r.lat, lon: r.lon } : null
  })
  const [selected, setSelected] = useState(focus?.key ?? null)
  const [reportMode, setReportMode] = useState(false)
  const [draft, setDraft] = useState(null) // {lat, lon} chosen for a new alert

  const fires = data?.fires ?? []
  // a dismissed report is no longer on the map, so it cannot stay selected
  const report = reports.find((r) => r.id === selected && r.status !== 'false') ?? null
  // Default to the most intense fire; fall back when the selection leaves the current region.
  const fire = report ? null : (fires.find((f) => f.id === selected) ?? fires[0] ?? null)

  const select = (id) => {
    setSelected(id)
    setReportMode(false)
    setDraft(null)
  }
  const changeMode = (on) => {
    setReportMode(on)
    if (!on) setDraft(null)
  }
  const submit = (values) => {
    try {
      const created = addReport(values)
      setSelected(created.id)
      setReportMode(false)
      setDraft(null)
      toast.success('Alert filed as unverified (?). Someone has to confirm it before it counts as a fire.', {
        action: { label: 'Review', onClick: () => navigate(`/dashboard/reports?report=${encodeURIComponent(created.id)}`) },
      })
    } catch (e) {
      toast.error(e.message)
    }
  }

  return (
    <>
      <PageHeader
        kicker="Geospatial"
        title="Global Fire Map"
        description="Near-real-time active fires from VIIRS and MODIS. Click a fire to inspect it, play back the last 24 hours, draw an area to focus the other pages, or report a fire the satellites have not seen."
        actions={
          data && (
            <Badge tone="ok">
              <span className="size-1.5 rounded-full bg-ok" aria-hidden /> Updated {fmtUtc(data.snapshot)} · ~3 h latency
            </Badge>
          )
        }
      />

      <div className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        {reportMode ? (
          <ReportForm draft={draft} onDraftChange={setDraft} onSubmit={submit} onCancel={() => changeMode(false)} className={side} />
        ) : report ? (
          <ReportDetails report={report} className={side} />
        ) : (
          <FireDetails fire={fire} fires={fires} onSelect={select} className={side} />
        )}
        <div className="min-w-0 overflow-hidden rounded-2xl border border-line">
          <FireMap
            height="clamp(420px, calc(100vh - 520px), 760px)"
            selectedId={report?.id ?? fire?.id ?? null}
            onSelect={select}
            reportMode={reportMode}
            onReportModeChange={changeMode}
            draft={draft}
            onPick={(lat, lon) => setDraft({ lat, lon })}
            focus={focus}
          />
        </div>
        {!report && !reportMode && (
          <Card className="p-0!">
            <FirePassChart fire={fire} range={data?.chartRange} now={data?.snapshot} />
          </Card>
        )}
      </div>

      <div className="mt-4">
        <IllustrativeNotice />
      </div>
    </>
  )
}
