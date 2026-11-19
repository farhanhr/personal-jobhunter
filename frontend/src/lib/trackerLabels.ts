/**
 * Tracker and synchronization vocabulary, shared by the feed and, later, the
 * detail editor, so both describe a field the same way.
 *
 * - **Not recorded** is a known blank: the backend returned the field and the
 *   canonical cell is empty.
 * - **Unknown** is absence: no local source knows the field. It must never be
 *   rendered as blank.
 *
 * The sync status values are the backend's own outbox states.
 */

export const TRACKER_FIELD_LABELS: Record<string, string> = {
  application_status: 'Application status',
  date_applied: 'Date applied',
  follow_up_date: 'Follow-up date',
  interview_date: 'Interview date',
}

export const TRACKER_SYNC_LABELS: Record<string, string> = {
  pending: 'Waiting for manual sync',
  in_progress: 'Sync in progress',
  retry_wait: 'Waiting for a sync attempt',
  blocked: 'Needs attention',
  synced: 'Synced',
  not_queued: 'Nothing waiting to sync',
}

/**
 * The same states, phrased for the editor right after a save.
 *
 * A save only ever reaches the local outbox, so nothing here may claim that
 * Google Sheets already holds the edit. `blocked` still means the edit is
 * queued: it needs a decision, it was not discarded.
 */
export const TRACKER_SAVED_SYNC_LABELS: Record<string, string> = {
  pending: 'Saved locally · Waiting to sync',
  in_progress: 'Synchronization in progress',
  retry_wait: 'Saved locally · Retry scheduled',
  blocked: 'Saved locally · Sync needs attention',
  synced: 'Synchronized',
  not_queued: 'No pending synchronization',
}

/** One sentence of context for a state that could be misread. */
export const TRACKER_SYNC_NOTES: Record<string, string> = {
  blocked:
    'Your edit is still queued locally. Resolve the cause in the sync controls, then requeue it; nothing has been discarded.',
  pending:
    'The edit is queued. Run a sync from the dashboard to write it to the spreadsheet.',
  retry_wait:
    'The edit is queued and will be attempted again on the next sync.',
  in_progress:
    'A sync is writing this record now. Reload to see the settled state.',
  synced: 'The spreadsheet holds this record as shown.',
  not_queued: 'Nothing is queued for this record.',
}
