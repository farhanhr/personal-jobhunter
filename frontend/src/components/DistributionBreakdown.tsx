import type { DashboardSummary } from '../api/dashboard'

type Bar = {
  key: string
  label: string
  count: number
}

type GroupBarsProps = {
  id: string
  title: string
  bars: Bar[]
  emptyMessage: string
  note?: string
}

/**
 * A horizontal bar list drawn with CSS only. The number is always rendered as
 * text, so the bar is decoration on top of a readable table rather than the
 * only carrier of the value.
 */
function GroupBars({ id, title, bars, emptyMessage, note }: GroupBarsProps) {
  const max = bars.reduce((highest, bar) => Math.max(highest, bar.count), 0)

  return (
    <div className="distribution-group">
      <h3 id={id}>{title}</h3>
      {bars.length === 0 ? (
        <p className="settings-empty">{emptyMessage}</p>
      ) : (
        <ul className="bars" aria-labelledby={id}>
          {bars.map((bar) => (
            <li className="bar-row" key={bar.key}>
              <span className="bar-label">{bar.label}</span>
              <span className="bar-track" aria-hidden="true">
                {bar.count > 0 && (
                  <span
                    className="bar-fill"
                    style={{
                      width: max > 0 ? `${(bar.count / max) * 100}%` : '0%',
                    }}
                  />
                )}
              </span>
              <span className="bar-count tabular">{bar.count}</span>
            </li>
          ))}
        </ul>
      )}
      {note ? <p className="distribution-note">{note}</p> : null}
    </div>
  )
}

function locationLabel(province: string, city: string): string {
  const cityUnknown = city === 'Unknown' || city === ''
  const provinceUnknown = province === 'Unknown' || province === ''

  if (cityUnknown && provinceUnknown) {
    return 'Location unknown'
  }
  if (cityUnknown) {
    return province
  }
  if (provinceUnknown) {
    return city
  }

  return `${city}, ${province}`
}

export function DistributionBreakdown({
  summary,
}: {
  summary: DashboardSummary
}) {
  const roleBars = summary.opportunities_by_target_role.map((group) => ({
    key: group.role_id ?? `unmatched-${group.label}`,
    label: group.label,
    count: group.count,
  }))

  const sourceBars = summary.opportunities_by_source.map((group) => ({
    key: group.source_type,
    label: group.source_type,
    count: group.count,
  }))

  const locationBars = summary.opportunities_by_location.map((group) => ({
    key: `${group.province}|${group.city}`,
    label: locationLabel(group.province, group.city),
    count: group.count,
  }))

  return (
    <div className="distribution">
      <GroupBars
        id="distribution-roles"
        title="By target role"
        bars={roleBars}
        emptyMessage="No opportunity has been matched to a role yet."
      />
      <GroupBars
        id="distribution-sources"
        title="By source type"
        bars={sourceBars}
        emptyMessage="No source records yet."
        note="One opportunity can be found on more than one source, so these counts can add up to more than the opportunity total."
      />
      <GroupBars
        id="distribution-locations"
        title="By location"
        bars={locationBars}
        emptyMessage="No location data yet."
      />
    </div>
  )
}
