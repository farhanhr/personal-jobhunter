import { fetchSyncStatus } from '../api/sync'
import { useResource } from '../hooks/useResource'
import { formatJakartaInstant } from '../lib/datetime'

/**
 * Google Sheets synchronization state, read from `/api/v1/sync/status`.
 *
 * `totals.outstanding` is the backend's own count of unsynced outbox rows and
 * `action_required` is its own judgement. Neither is re-derived here.
 */
export function SyncStatusSection({ refreshToken = 0 }: { refreshToken?: number }) {
  const { state, reload } = useResource(fetchSyncStatus, refreshToken)

  return (
    <section className="settings-panel" aria-labelledby="sync-heading">
      <div className="settings-heading">
        <h2 id="sync-heading">Google Sheets sync</h2>
        <p>
          Discovery writes reach your spreadsheet only when you run a sync. This
          is a queue count, not an automatic background job.
        </p>
      </div>

      {state.status === 'loading' && (
        <p className="settings-note" aria-live="polite">
          Loading synchronization status…
        </p>
      )}

      {state.status === 'error' && (
        <div className="settings-alert settings-alert-error" role="alert">
          <h3>Could not load synchronization status</h3>
          <p>{state.message}</p>
          <button type="button" className="button-secondary" onClick={reload}>
            Retry
          </button>
        </div>
      )}

      {state.status === 'loaded' && (
        <>
          <dl className="sync-facts">
            <div>
              <dt>Outstanding</dt>
              <dd className="tabular">
                {state.data.totals.outstanding ?? 0}
              </dd>
            </div>
            <div>
              <dt>Due now</dt>
              <dd className="tabular">{state.data.due_now}</dd>
            </div>
            <div>
              <dt>Last successful sync</dt>
              <dd>
                {state.data.last_synced_at_utc ? (
                  <time dateTime={state.data.last_synced_at_utc}>
                    {formatJakartaInstant(state.data.last_synced_at_utc)}
                  </time>
                ) : (
                  'Never synced'
                )}
              </dd>
            </div>
            <div>
              <dt>Checked</dt>
              <dd>
                <time dateTime={state.data.checked_at_utc}>
                  {formatJakartaInstant(state.data.checked_at_utc)}
                </time>
              </dd>
            </div>
          </dl>

          {state.data.action_required ? (
            <div className="settings-alert settings-alert-warning" role="status">
              <h3>Action needed before these records can sync</h3>
              <p>
                Blocked records need you to resolve the cause, then requeue them
                from the sync controls. They will not retry on their own.
              </p>
              {state.data.blocked.length > 0 && (
                <ul className="blocker-list">
                  {state.data.blocked.map((item) => (
                    <li key={item.outbox_id}>
                      <span className="nowrap">{item.entity_type}</span>{' '}
                      <span className="nowrap">{item.entity_id}</span> —{' '}
                      {item.attempt_count} of {item.max_attempts} attempts
                      {item.last_error_code
                        ? `, last error ${item.last_error_code}`
                        : ''}
                      .
                    </li>
                  ))}
                </ul>
              )}
              {state.data.blocked_truncated && (
                <p className="field-hint">
                  Only the first {state.data.blocked.length} blocked records are
                  listed.
                </p>
              )}
            </div>
          ) : state.data.totals.outstanding > 0 ? (
            <p className="settings-note">
              Everything queued is waiting for a manual sync. Nothing is blocked.
            </p>
          ) : (
            <p className="settings-note">
              Nothing is waiting to sync. The spreadsheet matches local data as
              of the last refresh.
            </p>
          )}
        </>
      )}
    </section>
  )
}
