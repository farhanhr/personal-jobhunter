import { fetchLatestSearchRun } from '../api/searchRuns'
import { useResource } from '../hooks/useResource'
import { RunStatusCard } from './RunStatusCard'

/**
 * The most recently accepted run, as persisted by the backend.
 *
 * `null` is a documented success response meaning "no run has ever been
 * accepted". It renders as an empty state, not as an error and not as an
 * invented run.
 */
export function LatestRunSection({ refreshToken = 0 }: { refreshToken?: number }) {
  const { state, reload } = useResource(fetchLatestSearchRun, refreshToken)

  return (
    <section className="settings-panel" aria-labelledby="latest-run-heading">
      <div className="settings-heading">
        <h2 id="latest-run-heading">Latest search run</h2>
      </div>

      {state.status === 'loading' && (
        <p className="settings-note" aria-live="polite">
          Loading the latest run…
        </p>
      )}

      {state.status === 'error' && (
        <div className="settings-alert settings-alert-error" role="alert">
          <h3>Could not load the latest run</h3>
          <p>{state.message}</p>
          <button type="button" className="button-secondary" onClick={reload}>
            Retry
          </button>
        </div>
      )}

      {state.status === 'loaded' && state.data === null && (
        <p className="settings-empty">
          No search has been accepted yet. When you start one, its status,
          timestamps, and per-source outcomes appear here.
        </p>
      )}

      {state.status === 'loaded' && state.data !== null && (
        <RunStatusCard run={state.data} />
      )}
    </section>
  )
}
