/**
 * Processing Center client: the read-only GET /processing/* endpoints (spec §10, backend README
 * "Processing endpoints"). There is deliberately no mock fallback: when the API answers 503 (no
 * database, as on the Render demo) or is unreachable, the page says so (utils/processing.js
 * `processingUnavailable`). Nothing here triggers a run.
 */
import { apiGet } from './http'

export const getProcessingStatus = () => apiGet('/processing/status')
export const getProcessingJobs = (params) => apiGet('/processing/jobs', params)
export const getProcessingJob = (id) => apiGet(`/processing/jobs/${encodeURIComponent(id)}`)
export const getProcessingDatasets = (params) => apiGet('/processing/datasets', params)
