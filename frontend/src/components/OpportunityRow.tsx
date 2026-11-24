import type { Opportunity } from '../api/opportunities'
import { Link } from 'react-router'
import { formatJakartaInstant } from '../lib/datetime'
import { fitBadgeClass, fitBadgeText } from '../lib/fitLabels'
import { remoteScopeLabel, workModeLabel } from '../lib/locationLabels'
import { TRACKER_FIELD_LABELS, TRACKER_SYNC_LABELS } from '../lib/trackerLabels'

const TRACKER_SUMMARY_FIELDS = [
  'application_status',
  'date_applied',
  'follow_up_date',
  'interview_date',
] as const

function locationLabel(opportunity: Opportunity): string {
  const { province, city, location_raw: raw } = opportunity
  const cityUnknown = city === null || city === ''
  const provinceUnknown = province === null || province === ''

  if (cityUnknown && provinceUnknown) {
    return raw === null || raw === '' ? 'Location unknown' : raw
  }
  if (cityUnknown) {
    return province as string
  }
  if (provinceUnknown) {
    return city as string
  }

  return `${city}, ${province}`
}

function matchLabel(opportunity: Opportunity): string | null {
  const { role_match_type: type, role_match_reason: reason } = opportunity
  if (!type && !reason) {
    return null
  }
  return reason ?? type
}

function availabilityLabel(opportunity: Opportunity): string {  const status = opportunity.availability_status
  if (status === 'open') return 'Open'
  if (status === 'closed_confirmed') return 'Closed (confirmed)'
  if (status === 'unknown') return 'Availability unknown'
  return status
}

function seenLabel(opportunity: Opportunity): string {
  // `posted_at_utc` is a claim from the source, so it is preferred when the
  // source actually supplied one; otherwise the first time we observed it.
  const value = opportunity.posted_at_utc ?? opportunity.first_seen_at_utc
  return formatJakartaInstant(value)
}

/**
 * One tracker field rendered from `tracker_summary`.
 *
 * A known blank is "Not recorded"; a field absent from `known_fields` is
 * "Unknown". Those are different facts about the data, not two spellings of it.
 */
function TrackerField({
  field,
  value,
  known,
}: {
  field: string
  value: string | null
  known: boolean
}) {
  return (
    <div className="tracker-field">
      <dt>{TRACKER_FIELD_LABELS[field] ?? field}</dt>
      <dd className={value === null ? 'tracker-value is-unset' : 'tracker-value'}>
        {value !== null ? value : known ? 'Not recorded' : 'Unknown'}
      </dd>
    </div>
  )
}

/**
 * Source provenance. Each source type is named once; a type the backend marked
 * safe is a link, an unsafe one is named without a link. Unsafe URLs are never
 * made clickable, whatever their text looks like.
 */
function Provenance({ opportunity }: { opportunity: Opportunity }) {
  const types: Array<{ type: string; url: string | null }> = []
  for (const source of opportunity.sources) {
    if (!types.some((entry) => entry.type === source.source_type)) {
      types.push({
        type: source.source_type,
        url: source.source_url_is_safe ? source.source_url : null,
      })
    }
  }

  const unsafeCount = opportunity.sources.length - opportunity.sources.filter(
    (source) => source.source_url_is_safe,
  ).length

  if (types.length === 0) {
    return <p className="opportunity-provenance">No source recorded.</p>
  }

  return (
    <p className="opportunity-provenance">
      Found on{' '}
      {types.map((entry, index) => (
        <span key={entry.type}>
          {index > 0 ? ', ' : ''}
          {entry.url !== null ? (
            <a href={entry.url} rel="noopener noreferrer">
              {entry.type}
            </a>
          ) : (
            entry.type
          )}
        </span>
      ))}
      .
      {unsafeCount > 0 && (
        <span>
          {' '}
          {unsafeCount} further {unsafeCount === 1 ? 'source' : 'sources'} cannot
          be linked safely.
        </span>
      )}
    </p>
  )
}

/**
 * One opportunity row: a list entry on a rule, not a card. The Ledger system
 * uses rules for structure and keeps boxes for controls.
 *
 * The title is the row's one real link, stretched over the whole entry with CSS,
 * so the entire row is clickable without nesting anchors inside a link. The
 * source links sit above that overlay and keep their own destination.
 */
export function OpportunityRow({
  opportunity,
  from,
}: {
  opportunity: Opportunity
  /** The feed URL to restore when the detail view navigates back. */
  from: string
}) {
  const tracker = opportunity.tracker_summary
  const known = new Set(tracker.known_fields)

  return (
    <li className="opportunity-row">
      <div className="opportunity-main">
        {opportunity.candidate_fit_score !== null && (
          <p className="opportunity-fit">
            <span className={fitBadgeClass(opportunity.candidate_fit_score)}>
              {fitBadgeText(opportunity.candidate_fit_score)}
            </span>
            {opportunity.candidate_fit_summary !== null && (
              <span className="fit-verdict">
                {opportunity.candidate_fit_summary.fit_verdict}
              </span>
            )}
          </p>
        )}
        <h2 className="opportunity-title">
          <Link
            className="opportunity-title-link"
            to={`/opportunities/${encodeURIComponent(opportunity.opportunity_id)}`}
            state={{ from }}
          >
            {opportunity.job_title}
          </Link>
        </h2>
        <p className="opportunity-company">{opportunity.company_name}</p>

        <dl className="opportunity-facts">
          <div>
            <dt>Location</dt>
            <dd>{locationLabel(opportunity)}</dd>
          </div>
          <div>
            <dt>Work mode</dt>
            <dd>{workModeLabel(opportunity.work_mode)}</dd>
          </div>
          <div>
            <dt>Remote scope</dt>
            <dd>{remoteScopeLabel(opportunity.remote_scope)}</dd>
          </div>
          <div>
            <dt>Availability</dt>
            <dd>{availabilityLabel(opportunity)}</dd>
          </div>
          <div>
            <dt>Seen</dt>
            <dd>{seenLabel(opportunity)}</dd>
          </div>
          {matchLabel(opportunity) !== null && (
            <div>
              <dt>Role match</dt>
              <dd>{matchLabel(opportunity)}</dd>
            </div>
          )}
        </dl>

        {opportunity.description_excerpt !== null && (
          <p className="opportunity-excerpt">{opportunity.description_excerpt}</p>
        )}

        <Provenance opportunity={opportunity} />
      </div>

      <div className="opportunity-tracker">
        <p className="tracker-sync" data-sync={tracker.sync_status}>
          {TRACKER_SYNC_LABELS[tracker.sync_status] ?? tracker.sync_status}
        </p>
        <dl className="tracker-fields">
          {TRACKER_SUMMARY_FIELDS.map((field) => (
            <TrackerField
              key={field}
              field={field}
              value={tracker[field]}
              known={known.has(field)}
            />
          ))}
        </dl>
      </div>
    </li>
  )
}
