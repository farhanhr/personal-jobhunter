import { useEffect, useRef, useState } from 'react'
import { Link, useParams, useLocation } from 'react-router'
import {
  fetchOpportunityDetail,
  type OpportunityDetail,
  type OpportunitySource,
  type TrackerState,
} from '../api/opportunities'
import { fetchTargetRoles } from '../api/targetRoles'
import { FitAnalysisSection } from '../components/FitAnalysisSection'
import { PageHeader } from '../components/PageHeader'
import { TrackerEditor } from '../components/TrackerEditor'
import { useResource } from '../hooks/useResource'
import { formatJakartaInstant } from '../lib/datetime'
import { descriptionAsText } from '../lib/description'
import { remoteScopeLabel, workModeLabel } from '../lib/locationLabels'
import { targetRoleName } from '../lib/targetRoleLabel'
import {
  TRACKER_SAVED_SYNC_LABELS,
  TRACKER_SYNC_NOTES,
} from '../lib/trackerLabels'

const TRACKER_KEYS = [
  'job_type',
  'date_applied',
  'application_status',
  'follow_up_date',
  'interview_date',
  'vacancy_url',
  'notes',
] as const

/**
 * Apply a saved tracker response onto the loaded detail.
 *
 * Only the tracker fields, `sync_status`, and `known_fields` change; discovery
 * metadata comes from the GET and is never overwritten by a PATCH response.
 * `known_fields` is taken from the response so a field that is still unknown
 * stays unknown.
 */
function mergeTracker(
  detail: OpportunityDetail,
  state: TrackerState,
): OpportunityDetail {
  const merged = { ...detail }
  for (const key of TRACKER_KEYS) {
    merged[key] = state[key]
  }
  merged.sync_status = state.sync_status
  merged.known_fields = state.known_fields
  return merged
}

function trackerSignature(detail: OpportunityDetail): string {
  return [
    ...TRACKER_KEYS.map((key) => detail[key] ?? '\u0000'),
    detail.known_fields.slice().sort().join(','),
  ].join('|')
}

function availabilityLabel(status: string): string {
  if (status === 'open') return 'Open'
  if (status === 'closed_confirmed') return 'Closed (confirmed)'
  if (status === 'unknown') return 'Unknown'
  return status
}

function bucketLabel(bucket: string): string {
  if (bucket === 'main') return 'Main feed'
  if (bucket === 'review') return 'Review queue'
  if (bucket === 'excluded') return 'Excluded by location'
  return bucket
}

/**
 * The role name, read from the configured target roles.
 *
 * One request for the whole list, not one per opportunity. Until it arrives, or
 * if the saved role was deleted, the page says a configured role matched rather
 * than rendering the internal id.
 */
function TargetRoleFact({ roleId }: { roleId: string | null }) {
  const { state } = useResource(
    (signal) => fetchTargetRoles(signal),
    'detail:target-roles',
  )

  if (roleId === null) {
    return <dd>Not matched to a configured role</dd>
  }

  const name =
    state.status === 'loaded'
      ? targetRoleName(state.data.target_roles, roleId)
      : null

  return <dd>{name ?? 'Matched to a configured target role'}</dd>
}

function locationLabel(detail: OpportunityDetail): string {
  const { city, province, location_raw: raw } = detail
  if (!city && !province) {
    return raw ?? 'Location unknown'
  }
  if (!city) return province as string
  if (!province) return city
  return `${city}, ${province}`
}

function SourceList({ sources }: { sources: OpportunitySource[] }) {
  if (sources.length === 0) {
    return <p className="settings-note">No source record was kept for this opportunity.</p>
  }

  return (
    <ul className="detail-sources">
      {sources.map((source) => (
        <li key={`${source.source_type}-${source.external_source_id}`}>
          <p className="detail-source-head">
            <strong>{source.source_type}</strong>{' '}
            <span className="nowrap detail-source-id">
              {source.external_source_id}
            </span>
          </p>
          <p className="detail-source-meta">
            Captured {formatJakartaInstant(source.captured_at_utc)}
            {source.run_ids.length > 0 && (
              <> · run {source.run_ids[source.run_ids.length - 1]}</>
            )}
          </p>
          {source.provenance_note !== null && (
            <p className="detail-source-note">{source.provenance_note}</p>
          )}
          {source.source_url_is_safe && source.source_url !== null ? (
            <p className="detail-source-link">
              <a href={source.source_url} rel="noopener noreferrer">
                Open the original posting
              </a>
            </p>
          ) : (
            <p className="detail-source-unsafe">
              This source's link was not stored as safe to open, so it is not
              shown as a link.
            </p>
          )}
        </li>
      ))}
    </ul>
  )
}

export function OpportunityDetailPage() {
  const { opportunityId = '' } = useParams()
  const location = useLocation()
  const [saved, setSaved] = useState<TrackerState | null>(null)
  const [savedAt, setSavedAt] = useState(0)
  const trackerHeadingRef = useRef<HTMLHeadingElement>(null)
  const justSaved = savedAt > 0

  const { state, reload } = useResource(
    (signal) => fetchOpportunityDetail(opportunityId, signal),
    `detail:${opportunityId}`,
  )

  // The editor remounts on the merged tracker values, which would otherwise
  // drop keyboard focus to the document. Move it to the section heading so the
  // next Tab reaches the first field; the status region still announces.
  useEffect(() => {
    if (savedAt > 0) {
      trackerHeadingRef.current?.focus()
    }
  }, [savedAt])

  // The feed passes the exact list URL it came from, so Back restores the same
  // bucket and page even after a reload of the detail view.
  const fromFeed: string =
    typeof (location.state as { from?: unknown } | null)?.from === 'string'
      ? ((location.state as { from: string }).from)
      : '/opportunities'

  const backLink = (
    <p className="detail-back">
      <Link to={fromFeed}>← Back to opportunities</Link>
    </p>
  )

  if (state.status === 'loading') {
    return (
      <>
        {backLink}
        <p className="settings-note" aria-live="polite">
          Loading the opportunity…
        </p>
      </>
    )
  }

  if (state.status === 'error') {
    const notFound = state.code === 'opportunity_not_found'

    return (
      <>
        <PageHeader
          title={notFound ? 'Opportunity not found' : 'Could not load this opportunity'}
        />
        <section className="settings-panel">
          <p className="settings-empty">
            {notFound
              ? 'No local record matches this address. It may have been removed, or the link may be mistyped.'
              : state.message}
          </p>
          <div className="settings-actions">
            {!notFound && (
              <button type="button" className="button-secondary" onClick={reload}>
                Retry
              </button>
            )}
            <Link className="button-secondary" to={fromFeed}>
              Back to opportunities
            </Link>
          </div>
        </section>
      </>
    )
  }

  const detail = saved === null ? state.data : mergeTracker(state.data, saved)

  return (
    <>
      {backLink}

      <PageHeader title={detail.job_title} description={detail.company_name} />

      <dl className="detail-facts">
        <div>
          <dt>Location</dt>
          <dd>{locationLabel(detail)}</dd>
        </div>
        <div>
          <dt>Work mode</dt>
          <dd>{workModeLabel(detail.work_mode)}</dd>
        </div>
        <div>
          <dt>Remote scope</dt>
          <dd>{remoteScopeLabel(detail.remote_scope)}</dd>
        </div>
        <div>
          <dt>Availability</dt>
          <dd>{availabilityLabel(detail.availability_status)}</dd>
        </div>
        <div>
          <dt>Bucket</dt>
          <dd>{bucketLabel(detail.feed_bucket)}</dd>
        </div>
        <div>
          <dt>Target role</dt>
          <TargetRoleFact roleId={detail.target_role_id} />
        </div>
        {detail.role_match_reason !== null && (
          <div className="detail-fact-wide">
            <dt>Why it matched</dt>
            <dd>{detail.role_match_reason}</dd>
          </div>
        )}
        <div>
          <dt>First seen</dt>
          <dd>{formatJakartaInstant(detail.first_seen_at_utc)}</dd>
        </div>
        <div>
          <dt>Last seen</dt>
          <dd>{formatJakartaInstant(detail.last_seen_at_utc)}</dd>
        </div>
        <div>
          <dt>Posted</dt>
          <dd>
            {detail.posted_at_utc === null
              ? 'Not supplied by the source'
              : formatJakartaInstant(detail.posted_at_utc)}
          </dd>
        </div>
      </dl>

      <section className="settings-panel" aria-labelledby="description-heading">
        <div className="settings-heading">
          <h2 id="description-heading">Description</h2>
        </div>
        {detail.description === null ? (
          <p className="settings-empty">
            The source did not include a description. Nothing is shown rather
            than a placeholder.
          </p>
        ) : (
          <div className="detail-description">
            {descriptionAsText(detail.description)}
          </div>
        )}
      </section>

      <FitAnalysisSection detail={detail} />

      <section className="settings-panel" aria-labelledby="sources-heading">
        <div className="settings-heading">
          <h2 id="sources-heading">Sources</h2>
          <p>
            Every place this opportunity was found. Only links the backend
            marked safe are openable.
          </p>
        </div>
        <SourceList sources={detail.sources} />
      </section>

      <section className="settings-panel" aria-labelledby="tracker-heading">
        <div className="settings-heading">
          <h2 id="tracker-heading" ref={trackerHeadingRef} tabIndex={-1}>
            Application tracker
          </h2>
          <p>
            These seven fields belong to you. A save queues the change locally;
            the spreadsheet is written on the next manual sync.
          </p>
        </div>

        <p
          className="tracker-sync detail-sync"
          data-sync={detail.sync_status}
          aria-live="polite"
        >
          {TRACKER_SAVED_SYNC_LABELS[detail.sync_status] ?? detail.sync_status}
        </p>
        {justSaved && (
          <p className="tracker-saved-note" role="status">
            Changes saved locally. The spreadsheet has not been updated yet.
          </p>
        )}
        <p className="settings-note">
          {TRACKER_SYNC_NOTES[detail.sync_status] ?? null}
        </p>

        <TrackerEditor
          key={trackerSignature(detail)}
          detail={detail}
          onSaved={(state) => {
            setSaved(state)
            setSavedAt((count) => count + 1)
          }}
        />
      </section>
    </>
  )
}
