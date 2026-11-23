import { Navigate, Outlet } from 'react-router'
import { fetchIntegrationStatus } from '../api/googleSheetsSettings'
import { useResource } from '../hooks/useResource'

/**
 * Blocks the working routes until Google Sheets setup has completed once.
 *
 * The gate reads the persisted readiness flag, never live connectivity: a
 * revoked key or share must leave already-synchronized data readable. A status
 * request failure is shown as an error with a retry, not as "onboarding
 * required", because the backend may simply be restarting.
 */
export function WorkspaceGate() {
  const { state, reload } = useResource(fetchIntegrationStatus, 'workspace-gate')

  if (state.status === 'loading') {
    return (
      <section className="settings-panel">
        <p role="status">Checking the Google Sheets workspace…</p>
      </section>
    )
  }

  if (state.status === 'error') {
    return (
      <section className="settings-panel" aria-labelledby="workspace-gate-error">
        <h1 id="workspace-gate-error" className="page-title">
          Workspace status unavailable
        </h1>
        <p role="alert">{state.message}</p>
        <button type="button" onClick={reload}>
          Retry
        </button>
      </section>
    )
  }

  if (!state.data.workspace_ready) {
    return <Navigate to="/onboarding" replace />
  }

  return <Outlet />
}
