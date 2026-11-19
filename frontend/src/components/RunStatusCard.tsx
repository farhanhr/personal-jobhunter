import type { SearchRunStatus, SourceRunResult } from '../api/searchRuns'
import { formatJakartaInstant } from '../lib/datetime'
import { STATUS_LABELS, TERMINAL_STATUSES } from '../lib/searchRunStatus'

type RunStatusCardProps = {
  run: SearchRunStatus
  pollingStopped?: boolean
}

/**
 * The run record: status, the three timestamps, progress, per-source outcomes,
 * and any error summary. Shared by the live panel and the persisted latest-run
 * section so both describe a run the same way.
 */
export function RunStatusCard({ run, pollingStopped = false }: RunStatusCardProps) {
  const label = STATUS_LABELS[run.status] ?? run.status
  const terminal = TERMINAL_STATUSES.has(run.status)

  return (
    <div className="run-status-card">
      <h3>
        Run <span className="nowrap">{run.run_id}</span>
      </h3>
      <p className="run-status" aria-live="polite">
        Status: <strong>{label}</strong>
      </p>

      <dl className="run-times">
        <div>
          <dt>Accepted</dt>
          <dd>
            <time dateTime={run.accepted_at_utc}>
              {formatJakartaInstant(run.accepted_at_utc)}
            </time>
          </dd>
        </div>
        <div>
          <dt>Started</dt>
          <dd>
            {run.started_at_utc ? (
              <time dateTime={run.started_at_utc}>
                {formatJakartaInstant(run.started_at_utc)}
              </time>
            ) : (
              'Not started'
            )}
          </dd>
        </div>
        <div>
          <dt>Finished</dt>
          <dd>
            {run.finished_at_utc ? (
              <time dateTime={run.finished_at_utc}>
                {formatJakartaInstant(run.finished_at_utc)}
              </time>
            ) : (
              'Not finished'
            )}
          </dd>
        </div>
        <div>
          <dt>Operational day</dt>
          <dd className="tabular">{run.operational_day}</dd>
        </div>
      </dl>

      {run.progress ? (
        <p className="run-progress">
          {run.progress.completed_sources} of {run.progress.total_sources}{' '}
          configured sources completed successfully.
          {run.progress.stage.toLowerCase() !== run.status.toLowerCase() && (
            <span> Stage: {run.progress.stage}.</span>
          )}
        </p>
      ) : (
        <p className="field-hint">
          No stage detail was reported for this run.
        </p>
      )}

      {run.error_summary && (
        <p className="error-detail" role="alert">
          {run.error_summary}
        </p>
      )}

      {run.source_results.length > 0 && (
        <ul className="source-results">
          {run.source_results.map((result) => (
            <SourceResultItem
              key={`${result.source_id}-${result.status}`}
              result={result}
            />
          ))}
        </ul>
      )}

      {terminal && (
        <p className="field-hint">
          This run is finished and its accepted quota remains consumed.
        </p>
      )}

      {pollingStopped && (
        <p className="settings-note">
          Status polling stopped after the configured limit. Reload the page to
          check the latest state.
        </p>
      )}
    </div>
  )
}

function SourceResultItem({ result }: { result: SourceRunResult }) {
  const hasCounters =
    result.records_relevant !== undefined ||
    result.records_filtered !== undefined ||
    result.records_invalid !== undefined

  return (
    <li>
      <strong>{result.source_id}</strong>: {result.status},{' '}
      {result.records_found} record{result.records_found === 1 ? '' : 's'} found
      {result.error_code ? ` (${result.error_code})` : ''}.
      {hasCounters && (
        <span className="source-counters">
          {result.records_relevant !== undefined && (
            <> {result.records_relevant} matched your target roles.</>
          )}
          {result.records_filtered !== undefined && (
            <> {result.records_filtered} filtered out by role or location.</>
          )}
          {result.records_invalid !== undefined && result.records_invalid > 0 && (
            <> {result.records_invalid} unusable entries skipped.</>
          )}
        </span>
      )}
    </li>
  )
}
