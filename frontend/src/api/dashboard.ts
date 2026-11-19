/**
 * `GET /api/v1/dashboard/summary`.
 *
 * Field names and nullability mirror `DashboardSummaryResponse` in
 * `backend/src/personal_job_finder_api/api/v1/dashboard.py`. Every window
 * boundary is supplied by the backend: the browser renders these dates and
 * never computes "today" or a window of its own.
 */

export type DateWindow = {
  start: string
  end: string
}

export type InvalidTrackerDate = {
  opportunity_id: string
  field: string
  raw_value: string
  reason: string
}

export type RoleGroup = {
  role_id: string | null
  label: string
  count: number
}

export type SourceGroup = {
  source_type: string
  count: number
}

export type LocationGroup = {
  province: string
  city: string
  count: number
}

export type DashboardDataQuality = {
  invalid_date_value_count: number
  invalid_date_values: InvalidTrackerDate[]
  invalid_date_values_truncated: boolean
}

export type DashboardSummary = {
  as_of_date_jkt: string
  total_opportunities: number
  excluded_opportunities: number
  new_opportunities: number
  new_opportunities_window: DateWindow
  applications_window: DateWindow
  applications_last_7_days: number
  interviews_window: DateWindow
  upcoming_interviews: number
  opportunities_by_target_role: RoleGroup[]
  opportunities_by_source: SourceGroup[]
  opportunities_by_location: LocationGroup[]
  latest_read_model_refresh_at_utc: string | null
  data_quality: DashboardDataQuality
}

export type DashboardApiErrorDetails = Record<string, unknown>

export class DashboardApiError extends Error {
  readonly code: string
  readonly details: DashboardApiErrorDetails
  readonly status: number

  constructor(
    message: string,
    options: {
      code: string
      details?: DashboardApiErrorDetails
      status: number
    },
  ) {
    super(message)
    this.name = 'DashboardApiError'
    this.code = options.code
    this.details = options.details ?? {}
    this.status = options.status
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

function isDateWindow(value: unknown): value is DateWindow {
  return (
    isRecord(value) &&
    typeof value.start === 'string' &&
    typeof value.end === 'string'
  )
}

function isRoleGroup(value: unknown): value is RoleGroup {
  return (
    isRecord(value) &&
    isNullableString(value.role_id) &&
    typeof value.label === 'string' &&
    isNonNegativeInteger(value.count)
  )
}

function isSourceGroup(value: unknown): value is SourceGroup {
  return (
    isRecord(value) &&
    typeof value.source_type === 'string' &&
    isNonNegativeInteger(value.count)
  )
}

function isLocationGroup(value: unknown): value is LocationGroup {
  return (
    isRecord(value) &&
    typeof value.province === 'string' &&
    typeof value.city === 'string' &&
    isNonNegativeInteger(value.count)
  )
}

function isInvalidTrackerDate(value: unknown): value is InvalidTrackerDate {
  return (
    isRecord(value) &&
    typeof value.opportunity_id === 'string' &&
    typeof value.field === 'string' &&
    typeof value.raw_value === 'string' &&
    typeof value.reason === 'string'
  )
}

function isDataQuality(value: unknown): value is DashboardDataQuality {
  return (
    isRecord(value) &&
    isNonNegativeInteger(value.invalid_date_value_count) &&
    Array.isArray(value.invalid_date_values) &&
    value.invalid_date_values.every(isInvalidTrackerDate) &&
    typeof value.invalid_date_values_truncated === 'boolean'
  )
}

function isDashboardSummary(value: unknown): value is DashboardSummary {
  if (!isRecord(value)) {
    return false
  }

  return (
    typeof value.as_of_date_jkt === 'string' &&
    isNonNegativeInteger(value.total_opportunities) &&
    isNonNegativeInteger(value.excluded_opportunities) &&
    isNonNegativeInteger(value.new_opportunities) &&
    isDateWindow(value.new_opportunities_window) &&
    isDateWindow(value.applications_window) &&
    isNonNegativeInteger(value.applications_last_7_days) &&
    isDateWindow(value.interviews_window) &&
    isNonNegativeInteger(value.upcoming_interviews) &&
    Array.isArray(value.opportunities_by_target_role) &&
    value.opportunities_by_target_role.every(isRoleGroup) &&
    Array.isArray(value.opportunities_by_source) &&
    value.opportunities_by_source.every(isSourceGroup) &&
    Array.isArray(value.opportunities_by_location) &&
    value.opportunities_by_location.every(isLocationGroup) &&
    isNullableString(value.latest_read_model_refresh_at_utc) &&
    isDataQuality(value.data_quality)
  )
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function errorFromPayload(payload: unknown, status: number): DashboardApiError {
  if (isRecord(payload) && isRecord(payload.error)) {
    const envelope = payload.error
    const code =
      typeof envelope.code === 'string' ? envelope.code : 'request_failed'
    const message =
      typeof envelope.message === 'string'
        ? envelope.message
        : 'The dashboard summary request failed.'
    const details = isRecord(envelope.details) ? envelope.details : {}

    return new DashboardApiError(message, { code, details, status })
  }

  return new DashboardApiError('The dashboard summary request failed.', {
    code: 'request_failed',
    status,
  })
}

async function readResponse(
  response: Response,
  isExpected: (value: unknown) => boolean,
): Promise<DashboardSummary> {
  const payload = await readJson(response)

  if (!response.ok) {
    throw errorFromPayload(payload, response.status)
  }

  if (!isExpected(payload)) {
    throw new DashboardApiError(
      'The dashboard summary endpoint returned an unexpected payload.',
      { code: 'unexpected_payload', status: response.status },
    )
  }

  return payload as DashboardSummary
}

export async function fetchDashboardSummary(
  signal?: AbortSignal,
): Promise<DashboardSummary> {
  const response = await fetch('/api/v1/dashboard/summary', {
    headers: { Accept: 'application/json' },
    signal,
  })

  return readResponse(response, isDashboardSummary)
}
