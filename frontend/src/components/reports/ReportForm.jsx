import { useState } from 'react'
import { CircleHelp, Info, LocateFixed, TriangleAlert } from 'lucide-react'
import { toast } from 'sonner'
import { useReports } from '../../hooks/useReports'
import { NOTE_MAX, OBSERVED, nearbyReport, parseCoord } from '../../utils/reports'

const field =
  'h-9 w-full rounded-lg border border-line bg-sidebar px-2.5 text-sm text-ink placeholder:text-faint hover:border-faint'

/**
 * Side panel for filing a community alert. The spot comes from a click on the map (`draft`) or from
 * the two coordinate fields; the alert is created as unverified and shows up as a "?" on the map.
 */
export default function ReportForm({ draft, onDraftChange, onSubmit, onCancel, className = '' }) {
  const { reports } = useReports()
  const [lat, setLat] = useState(draft ? String(draft.lat) : '')
  const [lon, setLon] = useState(draft ? String(draft.lon) : '')
  const [seen, setSeen] = useState(draft)
  const [observed, setObserved] = useState('smoke')
  const [note, setNote] = useState('')
  const [locating, setLocating] = useState(false)

  // follow the spot picked on the map (state is adjusted while rendering, as React recommends)
  if (draft !== seen) {
    setSeen(draft)
    if (draft && (parseCoord(lat, 90) !== draft.lat || parseCoord(lon, 180) !== draft.lon)) {
      setLat(String(draft.lat))
      setLon(String(draft.lon))
    }
  }

  const latN = parseCoord(lat, 90)
  const lonN = parseCoord(lon, 180)
  const valid = latN != null && lonN != null
  const duplicate = valid ? nearbyReport(reports, latN, lonN) : null
  const typed = lat !== '' || lon !== ''

  const onCoord = (which) => (e) => {
    const text = e.target.value
    ;(which === 'lat' ? setLat : setLon)(text)
    const nextLat = which === 'lat' ? parseCoord(text, 90) : latN
    const nextLon = which === 'lon' ? parseCoord(text, 180) : lonN
    onDraftChange(nextLat != null && nextLon != null ? { lat: nextLat, lon: nextLon } : null)
  }

  const locate = () => {
    if (!navigator.geolocation) {
      toast.error('Your browser cannot share its location. Click the map instead.')
      return
    }
    setLocating(true)
    navigator.geolocation.getCurrentPosition(
      (p) => {
        setLocating(false)
        onDraftChange({ lat: +p.coords.latitude.toFixed(4), lon: +p.coords.longitude.toFixed(4) })
      },
      (err) => {
        setLocating(false)
        toast.error(err.code === 1 ? 'Location permission was denied. Click the map instead.' : 'Could not get your location. Click the map instead.')
      },
      { enableHighAccuracy: true, timeout: 10_000 },
    )
  }

  const submit = (e) => {
    e.preventDefault()
    if (valid) onSubmit({ lat: latN, lon: lonN, observed, note })
  }

  return (
    <aside
      aria-label="Report a fire"
      className={`flex min-w-0 flex-col gap-4 rounded-2xl border border-line bg-gradient-to-br from-card to-[#131f31] p-4 ${className}`}
    >
      <div className="flex items-start gap-2.5">
        <span className="grid size-9 shrink-0 place-items-center rounded-xl border border-dashed border-yellow-300/70 bg-yellow-300/10 text-yellow-200">
          <CircleHelp className="size-5" aria-hidden />
        </span>
        <div>
          <h2 className="text-[15px] font-semibold text-ink">Report a fire or smoke</h2>
          <p className="text-xs text-muted">Seen something the satellites have not flagged? File it as an alert.</p>
        </div>
      </div>

      <form
        onSubmit={submit}
        className="flex flex-col gap-4"
        onKeyDown={(e) => {
          // Esc first leaves the field; a second Esc (outside a field) cancels the report
          if (e.key === 'Escape' && e.target.matches('input, textarea')) {
            e.nativeEvent.stopPropagation()
            e.target.blur()
          }
        }}
      >
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1.5 font-mono text-[10.5px] uppercase tracking-[0.14em] text-faint">1 · Where</legend>
          <p className="text-xs text-muted">
            {valid ? 'Spot selected. Click the map again to move it.' : 'Click the map, type coordinates, or use your location.'}
          </p>
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-[11px] text-faint">
              Latitude
              <input
                inputMode="decimal"
                value={lat}
                onChange={onCoord('lat')}
                placeholder="36.8065"
                aria-invalid={typed && latN == null}
                className={`${field} font-mono ${typed && latN == null ? 'border-fire-high!' : ''}`}
              />
            </label>
            <label className="flex flex-col gap-1 text-[11px] text-faint">
              Longitude
              <input
                inputMode="decimal"
                value={lon}
                onChange={onCoord('lon')}
                placeholder="10.1815"
                aria-invalid={typed && lonN == null}
                className={`${field} font-mono ${typed && lonN == null ? 'border-fire-high!' : ''}`}
              />
            </label>
          </div>
          <button
            type="button"
            onClick={locate}
            disabled={locating}
            className="inline-flex h-8 w-fit items-center gap-1.5 rounded-lg border border-line px-2.5 text-xs text-muted hover:border-fire/40 hover:text-ink disabled:opacity-60"
          >
            <LocateFixed className={`size-3.5 ${locating ? 'animate-pulse' : ''}`} aria-hidden />
            {locating ? 'Locating…' : 'Use my location'}
          </button>
          {duplicate && (
            <p className="flex items-start gap-1.5 text-xs text-yellow-200">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              {duplicate.report.status === 'confirmed' ? 'A confirmed fire' : 'An unverified alert'} was already reported about{' '}
              {duplicate.km < 0.1 ? 'here' : `${duplicate.km.toFixed(1)} km away`}.
            </p>
          )}
        </fieldset>

        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1.5 font-mono text-[10.5px] uppercase tracking-[0.14em] text-faint">2 · What do you see?</legend>
          <div className="grid grid-cols-2 gap-1.5" role="radiogroup" aria-label="What you see">
            {OBSERVED.map((o) => (
              <label
                key={o.id}
                className={`cursor-pointer rounded-lg border px-2.5 py-2 text-center text-xs transition-colors has-focus-visible:outline-2 has-focus-visible:outline-info ${
                  observed === o.id ? 'border-yellow-300/60 bg-yellow-300/10 font-medium text-yellow-100' : 'border-line text-muted hover:border-faint hover:text-ink'
                }`}
              >
                <input type="radio" name="observed" value={o.id} checked={observed === o.id} onChange={() => setObserved(o.id)} className="sr-only" />
                {o.label}
              </label>
            ))}
          </div>
        </fieldset>

        <label className="flex flex-col gap-1.5">
          <span className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-faint">3 · Anything that helps (optional)</span>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={NOTE_MAX}
            rows={3}
            placeholder="Size, wind direction, nearest road or village…"
            className={`${field} h-auto resize-none py-2`}
          />
          <span className="self-end text-[10.5px] tabular-nums text-faint">
            {note.length}/{NOTE_MAX}
          </span>
        </label>

        <p className="flex items-start gap-2 rounded-lg border-l-[3px] border-yellow-300/70 bg-yellow-300/5 px-3 py-2 text-xs text-muted">
          <Info className="mt-0.5 size-3.5 shrink-0 text-yellow-300" aria-hidden />
          <span>
            It shows as a <b className="text-yellow-200">?</b> on the map until someone confirms it in <b className="text-ink">Fire Reports</b>. Only
            confirmed alerts turn into a fire.
          </span>
        </p>

        <div className="flex gap-2">
          <button
            type="submit"
            disabled={!valid}
            className="inline-flex h-9 flex-1 items-center justify-center rounded-lg bg-fire px-3 text-sm font-medium text-[#1a0d02] transition-colors hover:bg-orange-400 disabled:cursor-not-allowed disabled:bg-line disabled:text-faint"
          >
            Submit alert
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="inline-flex h-9 items-center rounded-lg border border-line px-3 text-sm text-muted hover:border-faint hover:text-ink"
          >
            Cancel
          </button>
        </div>
      </form>
    </aside>
  )
}
