import { AlertTriangle, Database, Info } from 'lucide-react'
import { Badge, Notice } from '../ui/primitives'
import { useDataMode } from '../../hooks/useFireData'

const nf = new Intl.NumberFormat('en-US')

/** Header badge showing which data source is feeding the dashboard. */
export function DataModeBadge() {
  const { mode, rows } = useDataMode()
  if (mode === 'connecting') {
    return (
      <Badge>
        <span className="size-1.5 rounded-full bg-faint motion-safe:animate-pulse" aria-hidden /> Connecting…
      </Badge>
    )
  }
  if (mode === 'offline') {
    return (
      <Badge tone="fire" title="The data service is not responding, so the dashboard shows sample data">
        <span className="size-1.5 rounded-full bg-fire" aria-hidden /> Offline · sample data
      </Badge>
    )
  }
  if (mode === 'synthetic') {
    return (
      <Badge tone="fire" title="The data service is running on generated test data, not real observations">
        <span className="size-1.5 rounded-full bg-fire motion-safe:animate-pulse" aria-hidden /> Synthetic test data
      </Badge>
    )
  }
  return (
    <Badge tone="ok">
      <span className="size-1.5 rounded-full bg-ok motion-safe:animate-pulse" aria-hidden /> Live data{rows != null ? ` · ${nf.format(rows)} records` : ''}
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
        The data service is not responding, so these figures are generated sample values, not MODIS or VIIRS observations. Real
        figures come back by themselves as soon as the service is available again.
      </Notice>
    )
  }
  if (mode === 'synthetic') {
    return (
      <Notice icon={AlertTriangle} title="Synthetic test data.">
        The data service is running on generated test data. These are not real FIRMS observations: do not present them as
        results.
      </Notice>
    )
  }
  return (
    <Notice icon={Database} tone="info" title="Source.">
      NASA FIRMS active-fire detections (MODIS Collection 6.1, VIIRS 375 m Suomi NPP), harmonized by Spark Seer into one
      record. We acknowledge the use of data from NASA&apos;s Fire Information for
      Resource Management System (FIRMS), part of NASA&apos;s LANCE and ESDIS.
    </Notice>
  )
}

/** Shown when the record is empty for the chosen area (nothing detected there, or not covered). */
export function EmptyAreaNotice() {
  return (
    <Notice icon={Info} title="No detections in this area.">
      The record has no hotspot detections here, possibly because it does not cover this part of the world yet. Try one of the
      Tunisia and Algeria areas in the Area menu, or draw an area over a place with fires.
    </Notice>
  )
}
