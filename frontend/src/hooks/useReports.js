import { useMemo, useSyncExternalStore } from 'react'
import { addReport, deleteReport, getReports, getServerReports, reopenReport, reviewReport, subscribe } from '../services/reportsStore'

/** Community fire reports (browser-local, see services/reportsStore.js) and the actions on them. */
export function useReports() {
  const reports = useSyncExternalStore(subscribe, getReports, getServerReports)
  const counts = useMemo(() => {
    const c = { pending: 0, confirmed: 0, false: 0, total: reports.length }
    for (const r of reports) c[r.status]++
    return c
  }, [reports])
  return { reports, counts, addReport, reviewReport, reopenReport, deleteReport }
}
