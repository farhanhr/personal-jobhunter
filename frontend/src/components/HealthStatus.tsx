import { useEffect, useState } from 'react'
import { fetchHealth, type HealthResponse } from '../api/health'

type ConnectionState =
  | { status: 'loading' }
  | { status: 'connected'; health: HealthResponse }
  | { status: 'unavailable'; message: string }

/**
 * Reports whether the local API is reachable.
 *
 * This is a status line, not a section: it has no heading, because a heading
 * here would compete with the page's real sections. The coloured left bar
 * carries the state, and the text says what happened and what to do about it.
 */
export function HealthStatus() {
  const [connection, setConnection] = useState<ConnectionState>({
    status: 'loading',
  })

  useEffect(() => {
    const controller = new AbortController()

    fetchHealth(controller.signal)
      .then((health) => {
        setConnection({ status: 'connected', health })
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return
        }

        const message =
          error instanceof Error ? error.message : 'Unknown connection error'
        setConnection({ status: 'unavailable', message })
      })

    return () => controller.abort()
  }, [])

  return (
    <div
      className={`status-card status-${connection.status}`}
      role="status"
      aria-live="polite"
    >
      {connection.status === 'loading' && (
        <p className="status-line">
          <span className="status-label">Connecting to the backend</span>
          <span className="status-detail">Checking backend connection…</span>
        </p>
      )}

      {connection.status === 'connected' && (
        <p className="status-line">
          <span className="status-label">Backend connected</span>
          <span className="status-detail">
            {connection.health.service} is responding with version{' '}
            {connection.health.version}.
          </span>
        </p>
      )}

      {connection.status === 'unavailable' && (
        <>
          <p className="status-line">
            <span className="status-label">Backend unavailable</span>
            <span className="status-detail">
              Start the API on <code>127.0.0.1:8000</code>, then reload this
              page.
            </span>
          </p>
          <p className="error-detail">{connection.message}</p>
        </>
      )}
    </div>
  )
}
