/**
 * Readable labels for the two closed location enums.
 *
 * The API returns machine tokens (`not_applicable`, `indonesia_only`). The page
 * must never show those as prose. An unrecognised value is still displayed with
 * its underscores opened up, because dropping it would hide a real fact.
 */
const WORK_MODE_LABELS: Record<string, string> = {
  onsite: 'Onsite',
  hybrid: 'Hybrid',
  remote: 'Remote',
  unknown: 'Unknown',
}

const REMOTE_SCOPE_LABELS: Record<string, string> = {
  indonesia_only: 'Indonesia only',
  worldwide: 'Worldwide',
  country_restricted: 'Country restricted',
  unknown: 'Unknown',
  not_applicable: 'Not applicable',
}

function labelFor(labels: Record<string, string>, value: string): string {
  return labels[value] ?? value.replaceAll('_', ' ')
}

export function workModeLabel(value: string): string {
  return labelFor(WORK_MODE_LABELS, value)
}

export function remoteScopeLabel(value: string): string {
  return labelFor(REMOTE_SCOPE_LABELS, value)
}
