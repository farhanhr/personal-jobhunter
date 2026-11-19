import { useState } from 'react'
import { fetchDashboardSummary } from '../api/dashboard'
import { ActivityMetrics } from '../components/ActivityMetrics'
import { DataFreshness } from '../components/DataFreshness'
import { DistributionBreakdown } from '../components/DistributionBreakdown'
import { HealthStatus } from '../components/HealthStatus'
import { LatestRunSection } from '../components/LatestRunSection'
import { PageHeader } from '../components/PageHeader'
import { SearchRunPanel } from '../components/SearchRunPanel'
import { SyncStatusSection } from '../components/SyncStatusSection'
import { useResource } from '../hooks/useResource'
import { formatJakartaDate } from '../lib/datetime'

/**
 * The dashboard composes four independent API resources. Each section owns its
 * request, so a failure in one leaves the others readable. Every number and
 * every date on this page is rendered exactly as the backend returned it.
 */
export function DashboardPage() {
  const [refreshToken, setRefreshToken] = useState(0)
  const { state: summaryState, reload: reloadSummary } = useResource(
    fetchDashboardSummary,
    refreshToken,
  )

  const summary =
    summaryState.status === 'loaded' ? summaryState.data : undefined
  const summaryError =
    summaryState.status === 'error' ? summaryState.message : undefined

  function refreshAll() {
    setRefreshToken((token) => token + 1)
  }

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="What the search has found, where it came from, and whether a search can start right now."
      />

      <div className="dashboard-toolbar">
        <p className="dashboard-asof">
          {summaryState.status === 'loading' && 'Reading the local record…'}
          {summaryState.status === 'error' &&
            'Reporting date unavailable. The figures below could not be read.'}
          {summary && (
            <>
              Reporting date{' '}
              <span className="nowrap">{formatJakartaDate(summary.as_of_date_jkt)}</span>
              , Asia/Jakarta
            </>
          )}
        </p>
        <button type="button" className="button-secondary" onClick={refreshAll}>
          Refresh dashboard
        </button>
      </div>

      <HealthStatus />

      <SearchRunPanel refreshToken={refreshToken} />

      <section className="settings-panel" aria-labelledby="activity-heading">
        <div className="settings-heading">
          <h2 id="activity-heading">Activity</h2>
        </div>
        {summary ? (
          <ActivityMetrics summary={summary} />
        ) : (
          <SummaryUnavailable message={summaryError} onRetry={reloadSummary} />
        )}
      </section>

      <section className="settings-panel" aria-labelledby="distribution-heading">
        <div className="settings-heading">
          <h2 id="distribution-heading">Where opportunities come from</h2>
          <p>
            Groupings come from the local read model. They are counts of
            opportunities, not of records received.
          </p>
        </div>
        {summary ? (
          <DistributionBreakdown summary={summary} />
        ) : (
          <SummaryUnavailable message={summaryError} onRetry={reloadSummary} />
        )}
      </section>

      <LatestRunSection refreshToken={refreshToken} />

      <SyncStatusSection refreshToken={refreshToken} />

      <section className="settings-panel" aria-labelledby="freshness-heading">
        <div className="settings-heading">
          <h2 id="freshness-heading">Data freshness</h2>
        </div>
        {summary ? (
          <DataFreshness summary={summary} />
        ) : (
          <SummaryUnavailable message={summaryError} onRetry={reloadSummary} />
        )}
      </section>
    </>
  )
}

function SummaryUnavailable({
  message,
  onRetry,
}: {
  message?: string
  onRetry: () => void
}) {
  if (message === undefined) {
    return (
      <p className="settings-note" aria-live="polite">
        Loading dashboard figures…
      </p>
    )
  }

  return (
    <div className="settings-alert settings-alert-error" role="alert">
      <h3>Could not load the dashboard figures</h3>
      <p>{message}</p>
      <button type="button" className="button-secondary" onClick={onRetry}>
        Retry
      </button>
    </div>
  )
}
