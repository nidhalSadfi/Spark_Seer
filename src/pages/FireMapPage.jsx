import { PageHeader } from '../components/ui/primitives'
import FireMap from '../components/map/FireMap'

/** The map itself is built by another team member — this page only hosts the slot. */
export default function FireMapPage() {
  return (
    <>
      <PageHeader kicker="Geospatial" title="Global Fire Map" description="Interactive geographic investigation workspace." />
      <FireMap height="calc(100vh - 220px)" />
    </>
  )
}
