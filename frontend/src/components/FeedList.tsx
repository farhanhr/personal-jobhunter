import { fetchOpportunityFeed, type OpportunityFeedName } from '../api/opportunities'
import { useLocation } from 'react-router'
import { useResource } from '../hooks/useResource'
import { OpportunityRow } from '../components/OpportunityRow'

const FEED_COPY: Record<OpportunityFeedName, { tab: string; label: string }> = {
  main: { tab: 'Main feed', label: 'main feed' },
  review: { tab: 'Review queue', label: 'review queue' },
}

const number = new Intl.NumberFormat('en-GB')

/**
 * The page report. `total_items` counts opportunities in the bucket, not records
 * received; the range shown is the backend's own page arithmetic, never
 * re-derived from a different page size.
 */
function PageReport({
  page,
  pageSize,
  totalItems,
  totalPages,
}: {
  page: number
  pageSize: number
  totalItems: number
  totalPages: number
}) {
  const first = totalItems === 0 ? 0 : (page - 1) * pageSize + 1
  const last = Math.min(page * pageSize, totalItems)

  return (
    <p className="feed-summary">
      {totalItems === 0
        ? 'No opportunities in this bucket.'
        : `${number.format(first)}–${number.format(last)} of ${number.format(totalItems)}, page ${page} of ${totalPages}.`}
    </p>
  )
}

type FeedListProps = {
  feed: OpportunityFeedName
  page: number
  refreshToken?: number
  onPageChange: (page: number) => void
  /** The id the tab controls point at with `aria-controls`. */
  panelId: string
}

/**
 * One bucket of the feed.
 *
 * The section is named with `aria-label` rather than a heading, because the tab
 * control already names the bucket and repeating it on the page reads as
 * duplication. `role="region"` with a name keeps it a navigable landmark.
 *
 * The bucket and the page are supplied by the page, and the request key names
 * both, so a response can never land in the wrong view. Filtering is
 * deliberately absent: the backend offers bucket and pagination only.
 */
export function FeedList({
  feed,
  page,
  refreshToken = 0,
  onPageChange,
  panelId,
}: FeedListProps) {
  const copy = FEED_COPY[feed]
  const location = useLocation()
  // The exact list URL, so the detail view can send the user back to the same
  // bucket and page.
  const from = `${location.pathname}${location.search}`
  const { state, reload } = useResource(
    (signal) => fetchOpportunityFeed({ feed, page, signal }),
    `feed:${feed}:page:${page}:${refreshToken}`,
  )

  const loaded = state.status === 'loaded' ? state.data : null
  const totalPages = loaded?.total_pages ?? 0

  return (
    <section
      className="feed-section"
      id={panelId}
      role="tabpanel"
      aria-labelledby={`feed-tab-${feed}`}
    >
      {loaded !== null && (
        <div className="feed-heading">
          <PageReport
            page={loaded.page}
            pageSize={loaded.page_size}
            totalItems={loaded.total_items}
            totalPages={loaded.total_pages}
          />
        </div>
      )}

      {state.status === 'loading' && (
        <p className="feed-status" aria-live="polite">
          Loading opportunities…
        </p>
      )}

      {state.status === 'error' && (
        <div className="settings-alert settings-alert-error" role="alert">
          <h3>Could not load the {copy.label}</h3>
          <p>{state.message}</p>
          <button type="button" className="button-secondary" onClick={reload}>
            Retry
          </button>
        </div>
      )}

      {loaded !== null && loaded.items.length === 0 && (
        <p className="settings-empty">
          {feed === 'main'
            ? 'Nothing has been discovered yet. Run a search from the dashboard, then refresh this page.'
            : 'The review queue is empty. Records that need a location decision appear here after a search.'}
        </p>
      )}

      {loaded !== null && (
        <ul className="feed-list">
          {loaded.items.map((opportunity) => (
            <OpportunityRow
              key={opportunity.opportunity_id}
              opportunity={opportunity}
              from={from}
            />
          ))}
        </ul>
      )}

      {loaded !== null && totalPages > 1 && (
        <nav className="feed-pagination" aria-label="Feed pages">
          <button
            type="button"
            className="button-secondary"
            onClick={() => onPageChange(page - 1)}
            disabled={page <= 1}
          >
            Previous
          </button>
          <span className="feed-page-report" aria-live="polite">
            Page {page} of {totalPages}
          </span>
          <button
            type="button"
            className="button-secondary"
            onClick={() => onPageChange(page + 1)}
            disabled={page >= totalPages}
          >
            Next
          </button>
        </nav>
      )}
    </section>
  )
}
