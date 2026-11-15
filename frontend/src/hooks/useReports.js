import { useEffect, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createReport, loadReports, removeReport, setReportStatus, subscribeLocal } from '../services/reportsStore'

const KEY = ['reports']
const EMPTY = Object.freeze([])

/**
 * Community fire reports and the actions on them (see services/reportsStore.js).
 *
 *   reports          newest first; refreshed every 30 s so other people's reports show up
 *   source           'api' (shared through the server) | 'local' (this browser only) | undefined (loading)
 *   fallbackReason   why the API was not used, when source is 'local'
 *   addReport / reviewReport / reopenReport / deleteReport
 *                    async; they resolve to the saved report (or nothing for delete) and reject
 *                    with a readable Error (ReportsAuthError for a missing reviewer token)
 */
export function useReports() {
  const qc = useQueryClient()
  const query = useQuery({
    queryKey: KEY,
    queryFn: loadReports,
    staleTime: 15 * 1000,
    refetchInterval: 30 * 1000,
    retry: 1,
  })
  const source = query.data?.source
  const reports = query.data?.reports ?? EMPTY

  // browser-local mode: pick up reports filed in another tab
  useEffect(() => (source === 'local' ? subscribeLocal(() => qc.invalidateQueries({ queryKey: KEY })) : undefined), [source, qc])

  const counts = useMemo(() => {
    const c = { pending: 0, confirmed: 0, false: 0, total: reports.length }
    for (const r of reports) c[r.status]++
    return c
  }, [reports])

  const actions = useMemo(() => {
    const patchCache = (fn) => qc.setQueryData(KEY, (old) => (old ? { ...old, reports: fn(old.reports) } : old))
    const upsert = (report) => patchCache((list) => [report, ...list.filter((r) => r.id !== report.id)].sort((a, b) => b.createdAt - a.createdAt))
    const settle = () => qc.invalidateQueries({ queryKey: KEY })
    const setStatus = async (id, status) => {
      const updated = await setReportStatus(id, status, source)
      upsert(updated)
      settle()
      return updated
    }
    return {
      addReport: async (values) => {
        const created = await createReport(values, source)
        upsert(created)
        settle()
        return created
      },
      /** Mark a report as a real fire (`true`) or a false alarm (`false`). */
      reviewReport: (id, isFire) => setStatus(id, isFire ? 'confirmed' : 'false'),
      /** Put a reviewed report back to unverified. */
      reopenReport: (id) => setStatus(id, 'pending'),
      deleteReport: async (id) => {
        await removeReport(id, source)
        patchCache((list) => list.filter((r) => r.id !== id))
        settle()
      },
    }
  }, [qc, source])

  return {
    reports,
    counts,
    source,
    fallbackReason: query.data?.fallbackReason ?? null,
    isLoading: query.isPending,
    error: query.error,
    ...actions,
  }
}
