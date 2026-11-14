import { AlertTriangle, Database, Info } from 'lucide-react'
import { Badge, Notice } from '../ui/primitives'
import { useDataMode } from '../../hooks/useFireData'
import { API_BASE } from '../../services/http'

const nf = new Intl.NumberFormat('en-US')

/** Header badge showing which data source is feeding the dashboard. */
export function DataModeBadge() {
  const { mode, rows } = useDataMode()
  if (mode === 'connecting') {
    return (
      <Badge>
        <span className="size-1.5 rounded-full bg-faint motion-safe:animate-pulse" aria-hidden /> Connecting to API…
      </Badge>
    )
  }
  if (mode === 'offline') {
    return (
      <Badge tone="fire" title={`Could not reach ${API_BASE}`}>
        <span className="size-1.5 rounded-full bg-fire" aria-hidden /> API offline · sample data
      </Badge>
    )
  }
  if (mode === 'synthetic') {
    return (
      <Badge tone="fire" title="The backend is serving output of make_fake_data.py">
        <span className="size-1.5 rounded-full bg-fire motion-safe:animate-pulse" aria-hidden /> Synthetic test data
      </Badge>
    )
  }
  return (
    <Badge tone="ok">
      <span className="size-1.5 rounded-full bg-ok motion-safe:animate-pulse" aria-hidden /> Live API{rows != null ? ` · ${nf.format(rows)} rows` : ''}
    </Badge>
  )
}

/**
 * Page footer notice that says honestly where the figures come from:
 * offline fallback / synthetic backend data / live FIRMS data (with the required acknowledgement).
 */
export function DataSourceNotice() {
  const { mode } = useDataMode()
  if (mode === 'connecting') return null
  if (mode === 'offline') {
    return (
      <Notice icon={AlertTriangle} title="Illustrative data.">
        The EmberLedger API at {API_BASE} is unreachable, so these figures are generated sample values, not MODIS or VIIRS
        observations. Start the backend, or set VITE_API_URL, and reload.
      </Notice>
    )
  }
  if (mode === 'synthetic') {
    return (
      <Notice icon={AlertTriangle} title="Synthetic test data.">
        The backend is serving output of its fake-data generator. These are not real FIRMS observations — do not present
        them as results.
      </Notice>
    )
  }
  return (
    <Notice icon={Database} tone="info" title="Source.">
      NASA FIRMS active-fire detections (MODIS Collection 6.1, VIIRS 375 m Suomi NPP) harmonized by EmberLedger. Coverage:
      Tunisia and northern Algeria (north of 34°N). We acknowledge the use of data from NASA&apos;s Fire Information for
      Resource Management System (FIRMS), part of NASA&apos;s LANCE and ESDIS.
    </Notice>
  )
}

/** Shown when the API answers with an empty grid (nothing detected in the chosen area). */
export function EmptyAreaNotice({ note }) {
  return (
    <Notice icon={Info} title="No detections in this area.">
      {note ?? 'The dataset has no hotspot detections here.'} The dataset only covers Tunisia and northern Algeria (north of
      34°N), so try a named area from the Region menu or draw a box over that region.
    </Notice>
  )
}
