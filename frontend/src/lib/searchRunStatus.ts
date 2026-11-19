/**
 * Search run vocabulary, shared by the live panel and the latest-run section.
 *
 * These are display labels only. The status values themselves are defined by
 * the backend; an unknown status is rendered as its raw code rather than
 * guessed at.
 */

export const TERMINAL_STATUSES = new Set([
  'completed',
  'completed_with_warnings',
  'completed_pending_sync',
  'failed',
  'interrupted',
])

export const STATUS_LABELS: Record<string, string> = {
  accepted: 'Accepted',
  running: 'Running',
  completed: 'Completed',
  completed_with_warnings: 'Completed with warnings',
  completed_pending_sync: 'Completed, waiting for sync',
  failed: 'Failed',
  interrupted: 'Interrupted',
}
