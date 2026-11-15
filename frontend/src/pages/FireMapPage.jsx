import { useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { AlertTriangle, Database } from 'lucide-react'
import { Badge, Card, Notice, PageHeader } from '../components/ui/primitives'
import FireMap from '../components/map/FireMap'
import FireDetails from '../components/map/FireDetails'
import FirePassChart from '../components/map/FirePassChart'
import ReportDetails from '../components/reports/ReportDetails'
import ReportForm from '../components/reports/ReportForm'
import { fmtUtc } from '../components/map/mapConfig'
import { useActiveFires } from '../hooks/useFireData'
import { useReports } from '../hooks/useReports'
import { notifyReportError } from '../components/reports/notifyError'
import { API_BASE } from '../services/http'
import { LIVE_WINDOW_H } from '../services/constants'

/** When the newest stored detection is older than this, say the feed is behind. */
const STALE_H = 12

/** Where the fires on this page come from: live FIRMS detections, or the illustrative fallback. */
function FeedNotice({ data, error }) {
  if (error) {
    return (
      <Notice icon={AlertTriangle} title="Could not load active fires.">
        {error.message}
      </Notice>
    )
  }
  if (!data) return null
  if (data.meta.source === 'mock') {
    return (
      <Notice icon={AlertTriangle} title="Illustrative data: the live fire feed is unavailable.">
        {data.meta.fallbackReason ? `${data.meta.fallbackReason} ` : ''}The fires below are generated sample values (names, FRP, weather
        and FWI included), not MODIS or VIIRS detections. Start the backend at {API_BASE}, or set VITE_API_URL, and reload.
      </Notice>
    )
  }
  const ageH = ((data.meta.fetchedAt ?? data.window.end) - data.window.end) / 3600000
  const sources = data.meta.sources?.filter((s) => s.maxAcqDate) ?? []
  return (
    <Notice icon={Database} tone="info" title="Source.">
      NASA FIRMS near-real-time detections (MODIS and VIIRS 375 m), the {LIVE_WINDOW_H} h up to the newest stored detection, grouped in the
      browser into clusters of hotspots a few kilometres apart. A cluster is not necessarily one wildfire. Fire weather, burned area and
      place names are not part of this feed.
      {sources.length > 0 && <> Latest data per source: {sources.map((s) => `${s.source} ${s.maxAcqDate}`).join(' · ')}.</>}
      {ageH > STALE_H && (
        <b className="font-semibold text-yellow-200"> The newest stored detection is {Math.round(ageH)} h old: the feed is behind.</b>
      )}{' '}
      We acknowledge the use of data from NASA&apos;s Fire Information for Resource Management System (FIRMS), part of NASA&apos;s LANCE
      and ESDIS.
    </Notice>
  )
}

const side = 'xl:row-span-2 xl:max-h-[calc(100vh-150px)] xl:overflow-y-auto'

/**
 * Live fire monitor: the side panel (selected fire, selected community report, or the form for a
 * new alert), the map (centre) and the satellite-pass / fire-weather chart for the selected fire.
 */
export default function FireMapPage() {
  const { data, error } = useActiveFires()
  const { reports, addReport, source: reportsSource } = useReports()
  const [params] = useSearchParams()
  const navigate = useNavigate()

  // opened from Fire Reports with ?report=<id>: select that report and centre the map on it
  // (once the reports have loaded; FireMap centres on a given focus key only once)
  const [linkedId] = useState(() => params.get('report'))
  const linked = reports.find((x) => x.id === linkedId)
  const focus = useMemo(() => (linked ? { key: linked.id, lat: linked.lat, lon: linked.lon } : null), [linked])
  const [selected, setSelected] = useState(linkedId)
  const [saving, setSaving] = useState(false)
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
  const submit = async (values) => {
    if (saving) return
    setSaving(true)
    try {
      const created = await addReport(values)
      setSelected(created.id)
      setReportMode(false)
      setDraft(null)
      const where = reportsSource === 'local' ? ' It is saved in this browser only: the reports server is unreachable.' : ''
      toast.success(`Alert filed as unverified (?). Someone has to confirm it before it counts as a fire.${where}`, {
        action: { label: 'Review', onClick: () => navigate(`/dashboard/reports?report=${encodeURIComponent(created.id)}`) },
      })
    } catch (e) {
      notifyReportError(e)
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <PageHeader
        kicker="Geospatial"
        title="Global Fire Map"
        description="Near-real-time active fires from VIIRS and MODIS. Click a fire to inspect it, play back the last 24 hours, draw an area to focus the other pages, or report a fire the satellites have not seen."
        actions={
          data &&
          (data.meta.source === 'mock' ? (
            <Badge tone="fire" title={data.meta.fallbackReason ?? undefined}>
              <span className="size-1.5 rounded-full bg-fire" aria-hidden /> Live feed unavailable · sample fires
            </Badge>
          ) : (
            <Badge tone="ok" title="Time of the newest detection stored by the backend">
              <span className="size-1.5 rounded-full bg-ok" aria-hidden /> Latest detection {fmtUtc(data.window.end)}
            </Badge>
          ))
        }
      />

      {data?.meta.source === 'mock' && (
        <div className="mb-4">
          <FeedNotice data={data} error={null} />
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        {reportMode ? (
          <ReportForm draft={draft} onDraftChange={setDraft} onSubmit={submit} onCancel={() => changeMode(false)} className={side} />
        ) : report ? (
          <ReportDetails report={report} className={side} />
        ) : (
          <FireDetails
            fire={fire}
            fires={fires}
            onSelect={select}
            emptyText={
              data && !fires.length
                ? `No active fires detected in this area in the last ${LIVE_WINDOW_H} h. Draw a wider area, or pick another region.`
                : undefined
            }
            className={side}
          />
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
            <FirePassChart fire={fire} range={data?.chartRange} now={data?.meta.source === 'api' ? data.window.end : data?.snapshot} />
          </Card>
        )}
      </div>

      {data?.meta.source !== 'mock' && (
        <div className="mt-4">
          <FeedNotice data={data} error={error} />
        </div>
      )}
    </>
  )
}
