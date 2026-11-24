import { useState, type KeyboardEvent } from 'react'
import { useSearchParams } from 'react-router'
import type {
  OpportunityFeedName,
  OpportunityFeedSort,
} from '../api/opportunities'
import { FeedList } from '../components/FeedList'
import { PageHeader } from '../components/PageHeader'

const FEEDS = ['main', 'review'] as const

/** The tab controls and the panel they reveal share these ids. */
const PANEL_ID = 'opportunity-feed-panel'
const tabId = (feed: OpportunityFeedName) => `feed-tab-${feed}`

function feedFromParam(value: string | null): OpportunityFeedName {
  return FEEDS.includes(value as OpportunityFeedName)
    ? (value as OpportunityFeedName)
    : 'main'
}

function sortFromParam(value: string | null): OpportunityFeedSort {
  return value === 'best_fit' ? 'best_fit' : 'recent'
}

function pageFromParam(value: string | null): number {
  const parsed = Number(value)
  // A page from the URL must stay inside what the backend accepts; the feed
  // clamps it, so an out-of-range link shows a valid page instead of an error.
  return Number.isInteger(parsed) && parsed >= 1 ? parsed : 1
}

/**
 * The Opportunities page: two read-only buckets, Main and Review.
 *
 * The bucket and the page live in the URL search parameters, so back/forward
 * navigation and a shared link restore the same view; changing the bucket
 * resets the page, because pagination is per bucket.
 *
 * The FeedList key carries the full parameter state, so navigating to a
 * different feed or page remounts it and re-reads from the API instead of
 * showing another bucket's rows.
 */
export function OpportunitiesPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [refreshToken, setRefreshToken] = useState(0)

  const feed = feedFromParam(searchParams.get('feed'))
  const page = pageFromParam(searchParams.get('page'))
  const sortBy = sortFromParam(searchParams.get('sort'))

  function selectFeed(next: OpportunityFeedName) {
    if (next === feed) {
      return
    }
    setSearchParams(
      (current) => {
        const params = new URLSearchParams(current)
        params.set('feed', next)
        // Pagination is per bucket; page 3 of the main feed has no meaning here.
        params.delete('page')
        return params
      },
      { replace: true },
    )
  }

  function changeSort(next: OpportunityFeedSort) {
    setSearchParams(
      (current) => {
        const params = new URLSearchParams(current)
        if (next === 'recent') {
          params.delete('sort')
        } else {
          params.set('sort', next)
        }
        // A different order is a different page sequence.
        params.delete('page')
        return params
      },
      { replace: true },
    )
  }

  function changePage(next: number) {
    setSearchParams(
      (current) => {
        const params = new URLSearchParams(current)
        params.set('feed', feed)
        if (next <= 1) {
          params.delete('page')
        } else {
          params.set('page', String(next))
        }
        return params
      },
      { replace: true },
    )
  }

  function refreshAll() {
    setRefreshToken((token) => token + 1)
  }

  /**
   * Arrow keys move between buckets, as the tab pattern expects. Selection
   * follows focus, so the newly chosen tab is also focused after the change.
   */
  function handleTabKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const index = FEEDS.indexOf(feed)
    let next: OpportunityFeedName | null = null

    if (event.key === 'ArrowRight') {
      next = FEEDS[(index + 1) % FEEDS.length]
    } else if (event.key === 'ArrowLeft') {
      next = FEEDS[(index - 1 + FEEDS.length) % FEEDS.length]
    } else if (event.key === 'Home') {
      next = FEEDS[0]
    } else if (event.key === 'End') {
      next = FEEDS[FEEDS.length - 1]
    }

    if (next === null) {
      return
    }

    event.preventDefault()
    selectFeed(next)
    document.getElementById(tabId(next))?.focus()
  }

  return (
    <>
      <PageHeader
        title="Opportunities"
        description="Everything the search has found. The main feed is confident enough to keep; the review queue needs a location decision."
      />

      <div
        className="feed-tabs"
        role="tablist"
        aria-label="Opportunity buckets"
        onKeyDown={handleTabKeyDown}
      >
        {FEEDS.map((name) => (
          <button
            key={name}
            id={tabId(name)}
            type="button"
            role="tab"
            aria-selected={name === feed}
            aria-controls={PANEL_ID}
            // Roving tabindex: the tablist is one stop in the page's tab order.
            tabIndex={name === feed ? 0 : -1}
            className={name === feed ? 'feed-tab is-active' : 'feed-tab'}
            onClick={() => selectFeed(name)}
          >
            {name === 'main' ? 'Main feed' : 'Review queue'}
          </button>
        ))}
        <button
          type="button"
          className="button-secondary feed-refresh"
          onClick={refreshAll}
        >
          Refresh
        </button>
      </div>

      <div className="feed-controls">
        <label htmlFor="feed-sort">Sort</label>
        <select
          id="feed-sort"
          className="settings-select"
          value={sortBy}
          onChange={(event) => changeSort(sortFromParam(event.target.value))}
        >
          <option value="recent">Newest first</option>
          <option value="best_fit">Best Fit first</option>
        </select>
      </div>

      <FeedList
        key={`feed:${feed}:page:${page}:sort:${sortBy}:token:${refreshToken}`}
        feed={feed}
        page={page}
        sortBy={sortBy}
        refreshToken={refreshToken}
        onPageChange={changePage}
        panelId={PANEL_ID}
      />
    </>
  )
}
