import { useEffect, useState } from 'react'
import { fetchHealth, type HealthResponse } from './api/health'
import './App.css'

const APP_NAME = 'Personal Job Finder'
const APP_VERSION = __APP_VERSION__

type ConnectionState =
  | { status: 'loading' }
  | { status: 'connected'; health: HealthResponse }
  | { status: 'unavailable'; message: string }

function App() {
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
    <main className="app-shell">
      <header className="app-header">
        <p className="eyebrow">Local-first job discovery</p>
        <h1>{APP_NAME}</h1>
        <p className="version">Version {APP_VERSION}</p>
      </header>

      <section
        className={`status-card status-${connection.status}`}
        aria-live="polite"
      >
        {connection.status === 'loading' && (
          <>
            <h2>Connecting to the backend</h2>
            <p>Checking backend connection…</p>
          </>
        )}

        {connection.status === 'connected' && (
          <>
            <h2>Backend connected</h2>
            <p>
              {connection.health.service} is responding with version{' '}
              {connection.health.version}.
            </p>
          </>
        )}

        {connection.status === 'unavailable' && (
          <>
            <h2>Backend unavailable</h2>
            <p>
              Start the API on <code>127.0.0.1:8000</code>, then reload this
              page.
            </p>
            <p className="error-detail">{connection.message}</p>
          </>
        )}
      </section>

      <footer className="app-footer">
        <span>Loopback only</span>
        <span>API: /api/v1</span>
      </footer>
    </main>
  )
}

export default App
