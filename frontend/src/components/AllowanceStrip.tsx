import type { SearchPolicy } from '../api/searchRuns'
import { formatJakartaInstant } from '../lib/datetime'

/**
 * The one bold element on the dashboard: the day's search allowance drawn as
 * slots. The count comes from the policy response — `max_runs`, `accepted_runs`,
 * and `remaining_runs` — so the strip cannot disagree with the backend.
 */
export function AllowanceStrip({ policy }: { policy: SearchPolicy }) {
  const used = Math.min(policy.accepted_runs, policy.max_runs)
  const slots = Array.from({ length: policy.max_runs }, (_, index) => index < used)

  return (
    <div className="allowance">
      <div className="allowance-figure">
        <span className="allowance-count tabular">{policy.remaining_runs}</span>
        <span className="allowance-unit">
          {policy.remaining_runs === 1 ? 'search left' : 'searches left'}
        </span>
      </div>

      <ul className="allowance-slots" aria-hidden="true">
        {slots.map((filled, index) => (
          <li
            key={index}
            className={filled ? 'allowance-slot is-used' : 'allowance-slot'}
          />
        ))}
      </ul>

      <dl className="allowance-facts">
        <div>
          <dt>Operational day</dt>
          <dd className="tabular">{policy.operational_day}</dd>
        </div>
        <div>
          <dt>Used today</dt>
          <dd className="tabular">
            {policy.accepted_runs} of {policy.max_runs}
          </dd>
        </div>
        <div>
          <dt>Next reset</dt>
          <dd>
            <time dateTime={policy.next_reset_at}>
              {formatJakartaInstant(policy.next_reset_at)}
            </time>
          </dd>
        </div>
        <div>
          <dt>Search in progress</dt>
          <dd>{policy.has_active_run ? 'Yes, a run is active' : 'No'}</dd>
        </div>
      </dl>

      {policy.cooldown_until && (
        <p className="allowance-note">
          Cooldown until{' '}
          <time dateTime={policy.cooldown_until}>
            {formatJakartaInstant(policy.cooldown_until)}
          </time>
          .
          {policy.next_eligible_at && (
            <>
              {' '}
              Next eligible at{' '}
              <time dateTime={policy.next_eligible_at}>
                {formatJakartaInstant(policy.next_eligible_at)}
              </time>
              .
            </>
          )}
        </p>
      )}
    </div>
  )
}
