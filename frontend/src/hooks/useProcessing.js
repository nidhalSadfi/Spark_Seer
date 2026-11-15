import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { getProcessingDatasets, getProcessingJob, getProcessingJobs, getProcessingStatus } from '../services/processingApi'
import { jobListParams, processingUnavailable } from '../utils/processing'

/**
 * Processing Center queries. Each refetches every 30 s while the page is mounted and the browser
 * tab is visible (React Query pauses `refetchInterval` in a background tab). A 503 / unreachable
 * API is not retried: the page shows its "not connected" state at once.
 */
const live = {
  refetchInterval: 30 * 1000,
  refetchIntervalInBackground: false,
  staleTime: 15 * 1000,
  retry: (n, e) => n < 1 && !processingUnavailable(e) && !(e?.status >= 400 && e?.status < 500),
}

export function useProcessingStatus() {
  return useQuery({ queryKey: ['processing', 'status'], queryFn: getProcessingStatus, ...live })
}

/** `filters` = { kind, status, page, pageSize }; the previous page stays visible while the next loads. */
export function useProcessingJobs(filters, { enabled = true } = {}) {
  const params = jobListParams(filters)
  return useQuery({
    queryKey: ['processing', 'jobs', params],
    queryFn: () => getProcessingJobs(params),
    placeholderData: keepPreviousData,
    enabled,
    ...live,
  })
}

export function useProcessingJob(id) {
  return useQuery({
    queryKey: ['processing', 'job', id],
    queryFn: () => getProcessingJob(id),
    enabled: id != null,
    ...live,
  })
}

export function useProcessingDatasets({ enabled = true } = {}) {
  return useQuery({
    queryKey: ['processing', 'datasets'],
    queryFn: () => getProcessingDatasets({ limit: 10 }),
    enabled,
    ...live,
  })
}
