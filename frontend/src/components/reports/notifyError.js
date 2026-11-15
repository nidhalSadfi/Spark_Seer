import { toast } from 'sonner'
import { ReportsAuthError, getAdminToken, setAdminToken } from '../../services/reportsStore'

/**
 * Toast for a failed report action. A 401/403 from PATCH/DELETE (the server sets
 * REPORTS_ADMIN_TOKEN) gets a readable message and a way to enter the reviewer token.
 */
export function notifyReportError(e) {
  if (!(e instanceof ReportsAuthError)) {
    toast.error(e?.message ?? 'Something went wrong.')
    return
  }
  toast.error(e.message, {
    duration: 12_000,
    action: {
      label: getAdminToken() ? 'Change token' : 'Enter token',
      onClick: () => {
        const token = window.prompt('Reviewer token (kept for this tab only):', '')
        if (token == null) return
        setAdminToken(token)
        toast(token.trim() ? 'Reviewer token saved for this tab. Try again.' : 'Reviewer token cleared.')
      },
    },
  })
}
