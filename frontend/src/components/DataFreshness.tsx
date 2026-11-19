import type { DashboardSummary } from '../api/dashboard'
import { formatJakartaInstant } from '../lib/datetime'

const REASON_LABELS: Record<string, string> = {
  invalid_calendar_date: 'not a real calendar date',
  unrecognized_format: 'not a date format this app reads',
}

const FIELD_LABELS: Record<string, string> = {
  date_applied: 'date applied',
  follow_up_date: 'follow-up date',
  interview_date: 'interview date',
}

/**
 * How current the read model is, and which tracker dates could not be read.
 *
 * An unreadable date is reported with its raw value and left exactly as the
 * user typed it. It is excluded from the metrics above, never guessed at.
 */
export function DataFreshness({ summary }: { summary: DashboardSummary }) {
  const { latest_read_model_refresh_at_utc: refreshedAt, data_quality: quality } =
    summary

  return (
    <div className="freshness">
      <dl className="sync-facts">
        <div>
          <dt>Read model refreshed</dt>
          <dd>
            {refreshedAt ? (
              <time dateTime={refreshedAt}>
                {formatJakartaInstant(refreshedAt)}
              </time>
            ) : (
              'Never refreshed from Sheets'
            )}
          </dd>
        </div>
        <div>
          <dt>Reporting date</dt>
          <dd className="tabular">{summary.as_of_date_jkt}</dd>
        </div>
        <div>
          <dt>Unreadable tracker dates</dt>
          <dd className="tabular">{quality.invalid_date_value_count}</dd>
        </div>
      </dl>

      {!refreshedAt && (
        <p className="settings-note">
          These figures come from the local read model, which has not been
          refreshed yet. Refresh from Sheets before trusting the totals.
        </p>
      )}

      {quality.invalid_date_value_count > 0 && (
        <details className="quality-details">
          <summary>
            {quality.invalid_date_value_count}{' '}
            {quality.invalid_date_value_count === 1 ? 'value' : 'values'} could
            not be read as a date
          </summary>
          <p className="field-hint">
            These cells are excluded from the application and interview counts.
            The spreadsheet keeps the original text; fix the cell to include it.
          </p>
          <ul className="quality-list">
            {quality.invalid_date_values.map((entry) => (
              <li key={`${entry.opportunity_id}-${entry.field}`}>
                <span className="nowrap">{entry.opportunity_id}</span>,{' '}
                {FIELD_LABELS[entry.field] ?? entry.field}:{' '}
                <span className="quality-raw">{entry.raw_value}</span> —{' '}
                {REASON_LABELS[entry.reason] ?? entry.reason}.
              </li>
            ))}
          </ul>
          {quality.invalid_date_values_truncated && (
            <p className="field-hint">
              More invalid values exist than are listed here.
            </p>
          )}
        </details>
      )}
    </div>
  )
}
