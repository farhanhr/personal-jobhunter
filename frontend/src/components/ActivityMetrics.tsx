import type { DashboardSummary } from '../api/dashboard'
import { formatDateWindow } from '../lib/datetime'

const number = new Intl.NumberFormat('en-GB')

type MetricProps = {
  label: string
  value: number
  caption: string
  note?: string
}

/**
 * One figure in the activity ledger. The caption always states the exact
 * window the backend used, so a number is never shown without its period.
 */
function Metric({ label, value, caption, note }: MetricProps) {
  return (
    <div className="metric">
      <p className="metric-label">{label}</p>
      <p className="metric-value tabular">{number.format(value)}</p>
      <p className="metric-caption">{caption}</p>
      {note ? <p className="metric-note">{note}</p> : null}
    </div>
  )
}

export function ActivityMetrics({ summary }: { summary: DashboardSummary }) {
  return (
    <div className="metrics">
      <Metric
        label="Opportunities"
        value={summary.total_opportunities}
        caption="Everything kept after location filtering."
        note={
          summary.excluded_opportunities > 0
            ? `${number.format(summary.excluded_opportunities)} excluded by location scope, counted separately.`
            : undefined
        }
      />
      <Metric
        label="New"
        value={summary.new_opportunities}
        caption={formatDateWindow(
          summary.new_opportunities_window.start,
          summary.new_opportunities_window.end,
        )}
        note="First seen inside this window."
      />
      <Metric
        label="Applications"
        value={summary.applications_last_7_days}
        caption={formatDateWindow(
          summary.applications_window.start,
          summary.applications_window.end,
        )}
        note="Counted from the date applied you recorded."
      />
      <Metric
        label="Upcoming interviews"
        value={summary.upcoming_interviews}
        caption={formatDateWindow(
          summary.interviews_window.start,
          summary.interviews_window.end,
        )}
        note="Interview dates inside this window."
      />
    </div>
  )
}
