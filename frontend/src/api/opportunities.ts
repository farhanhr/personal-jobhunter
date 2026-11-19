/**
 * `GET /api/v1/jobs`.
 *
 * Field names and nullability mirror `OpportunityFeedResponse`,
 * `OpportunityResponse`, `SourceSummaryResponse`, and `TrackerSummaryResponse` in
 * `backend/src/personal_job_finder_api/api/v1/opportunities.py`. A change to
 * those models is a failure here, which is the contract check.
 *
 * Tracker semantics: `values` is null for both a cleared field and a field no
 * local source knows. `known_fields` is what separates the two. The client does
 * not reinterpret that rule.
 */

export type OpportunityFeedName = 'main' | 'review'

export type OpportunitySource = {
  source_type: string
  external_source_id: string
  source_url: string | null
  source_url_is_safe: boolean
  captured_at_utc: string
  provenance_note: string | null
  run_ids: string[]
}

export type OpportunityTrackerSummary = {
  application_status: string | null
  date_applied: string | null
  follow_up_date: string | null
  interview_date: string | null
  sync_status: string
  known_fields: string[]
}

export type Opportunity = {
  opportunity_id: string
  company_id: string
  company_name: string
  job_title: string
  target_role_id: string | null
  role_match_type: string | null
  role_match_reason: string | null
  location_raw: string | null
  country: string | null
  province: string | null
  city: string | null
  location_status: string
  work_mode: string
  remote_scope: string
  posted_at_utc: string | null
  first_seen_at_utc: string
  last_seen_at_utc: string
  availability_status: string
  feed_bucket: string
  description_excerpt: string | null
  has_more_description: boolean
  tracker_summary: OpportunityTrackerSummary
  sources: OpportunitySource[]
}

export type OpportunityFeedPage = {
  items: Opportunity[]
  page: number
  page_size: number
  total_items: number
  total_pages: number
}

/** The seven user-owned tracker columns, in canonical sheet order (ADR-021). */
export const TRACKER_FIELDS = [
  'job_type',
  'date_applied',
  'application_status',
  'follow_up_date',
  'interview_date',
  'vacancy_url',
  'notes',
] as const

export type TrackerField = (typeof TRACKER_FIELDS)[number]

/**
 * A partial tracker edit. Only the keys present are sent, so a field the user
 * did not touch is never part of the request and cannot be overwritten.
 * `null` is an explicit clear; omitting a field leaves it unchanged.
 */
export type TrackerUpdate = Partial<Record<TrackerField, string | null>>

/** `GET /api/v1/jobs/{id}` — discovery metadata, full description, tracker. */
export type OpportunityDetail = {
  opportunity_id: string
  company_id: string
  company_name: string
  job_title: string
  target_role_id: string | null
  role_match_type: string | null
  role_match_reason: string | null
  location_raw: string | null
  country: string | null
  province: string | null
  city: string | null
  location_status: string
  location_reason: string | null
  work_mode: string
  remote_scope: string
  posted_at_utc: string | null
  source_updated_at_utc: string | null
  first_seen_at_utc: string
  last_seen_at_utc: string
  availability_status: string
  feed_bucket: string
  description: string | null
  job_type: string | null
  date_applied: string | null
  application_status: string | null
  follow_up_date: string | null
  interview_date: string | null
  vacancy_url: string | null
  notes: string | null
  sync_status: string
  known_fields: string[]
  sources: OpportunitySource[]
}

/** The tracker portion shared by the detail response and the PATCH response. */
export type TrackerState = Record<TrackerField, string | null> & {
  opportunity_id: string
  sync_status: string
  known_fields: string[]
}

export type OpportunitiesApiErrorDetails = Record<string, unknown>
export class OpportunitiesApiError extends Error {
  readonly code: string
  readonly details: OpportunitiesApiErrorDetails
  readonly status: number

  constructor(
    message: string,
    options: {
      code: string
      details?: OpportunitiesApiErrorDetails
      status: number
    },
  ) {
    super(message)
    this.name = 'OpportunitiesApiError'
    this.code = options.code
    this.details = options.details ?? {}
    this.status = options.status
  }
}

const DEFAULT_PAGE_SIZE = 25

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isSource(value: unknown): value is OpportunitySource {
  return (
    isRecord(value) &&
    typeof value.source_type === 'string' &&
    typeof value.external_source_id === 'string' &&
    isNullableString(value.source_url) &&
    typeof value.source_url_is_safe === 'boolean' &&
    typeof value.captured_at_utc === 'string' &&
    isNullableString(value.provenance_note) &&
    isStringArray(value.run_ids)
  )
}

function isTrackerSummary(value: unknown): value is OpportunityTrackerSummary {
  return (
    isRecord(value) &&
    isNullableString(value.application_status) &&
    isNullableString(value.date_applied) &&
    isNullableString(value.follow_up_date) &&
    isNullableString(value.interview_date) &&
    typeof value.sync_status === 'string' &&
    isStringArray(value.known_fields)
  )
}

function isOpportunity(value: unknown): value is Opportunity {
  return (
    isRecord(value) &&
    typeof value.opportunity_id === 'string' &&
    typeof value.company_id === 'string' &&
    typeof value.company_name === 'string' &&
    typeof value.job_title === 'string' &&
    isNullableString(value.target_role_id) &&
    isNullableString(value.role_match_type) &&
    isNullableString(value.role_match_reason) &&
    isNullableString(value.location_raw) &&
    isNullableString(value.country) &&
    isNullableString(value.province) &&
    isNullableString(value.city) &&
    typeof value.location_status === 'string' &&
    typeof value.work_mode === 'string' &&
    typeof value.remote_scope === 'string' &&
    isNullableString(value.posted_at_utc) &&
    typeof value.first_seen_at_utc === 'string' &&
    typeof value.last_seen_at_utc === 'string' &&
    typeof value.availability_status === 'string' &&
    typeof value.feed_bucket === 'string' &&
    isNullableString(value.description_excerpt) &&
    typeof value.has_more_description === 'boolean' &&
    isTrackerSummary(value.tracker_summary) &&
    Array.isArray(value.sources) &&
    value.sources.every(isSource)
  )
}

function isFeedPage(value: unknown): value is OpportunityFeedPage {
  return (
    isRecord(value) &&
    Array.isArray(value.items) &&
    value.items.every(isOpportunity) &&
    isNonNegativeInteger(value.page) &&
    isNonNegativeInteger(value.page_size) &&
    isNonNegativeInteger(value.total_items) &&
    isNonNegativeInteger(value.total_pages)
  )
}

function isOpportunityDetail(value: unknown): value is OpportunityDetail {
  return (
    isRecord(value) &&
    typeof value.opportunity_id === 'string' &&
    typeof value.company_id === 'string' &&
    typeof value.company_name === 'string' &&
    typeof value.job_title === 'string' &&
    isNullableString(value.target_role_id) &&
    isNullableString(value.role_match_type) &&
    isNullableString(value.role_match_reason) &&
    isNullableString(value.location_raw) &&
    isNullableString(value.country) &&
    isNullableString(value.province) &&
    isNullableString(value.city) &&
    typeof value.location_status === 'string' &&
    isNullableString(value.location_reason) &&
    typeof value.work_mode === 'string' &&
    typeof value.remote_scope === 'string' &&
    isNullableString(value.posted_at_utc) &&
    isNullableString(value.source_updated_at_utc) &&
    typeof value.first_seen_at_utc === 'string' &&
    typeof value.last_seen_at_utc === 'string' &&
    typeof value.availability_status === 'string' &&
    typeof value.feed_bucket === 'string' &&
    isNullableString(value.description) &&
    isNullableString(value.job_type) &&
    isNullableString(value.date_applied) &&
    isNullableString(value.application_status) &&
    isNullableString(value.follow_up_date) &&
    isNullableString(value.interview_date) &&
    isNullableString(value.vacancy_url) &&
    isNullableString(value.notes) &&
    typeof value.sync_status === 'string' &&
    isStringArray(value.known_fields) &&
    Array.isArray(value.sources) &&
    value.sources.every(isSource)
  )
}

function isTrackerState(value: unknown): value is TrackerState {
  return (
    isRecord(value) &&
    typeof value.opportunity_id === 'string' &&
    isNullableString(value.job_type) &&
    isNullableString(value.date_applied) &&
    isNullableString(value.application_status) &&
    isNullableString(value.follow_up_date) &&
    isNullableString(value.interview_date) &&
    isNullableString(value.vacancy_url) &&
    isNullableString(value.notes) &&
    typeof value.sync_status === 'string' &&
    isStringArray(value.known_fields)
  )
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function errorFromPayload(payload: unknown, status: number): OpportunitiesApiError {
  if (isRecord(payload) && isRecord(payload.error)) {
    const envelope = payload.error
    const code =
      typeof envelope.code === 'string' ? envelope.code : 'request_failed'
    const message =
      typeof envelope.message === 'string'
        ? envelope.message
        : 'The opportunity feed request failed.'
    const details = isRecord(envelope.details) ? envelope.details : {}

    return new OpportunitiesApiError(message, { code, details, status })
  }

  return new OpportunitiesApiError('The opportunity feed request failed.', {
    code: 'request_failed',
    status,
  })
}

async function readResponse(
  response: Response,
): Promise<OpportunityFeedPage> {
  const payload = await readJson(response)

  if (!response.ok) {
    throw errorFromPayload(payload, response.status)
  }

  if (!isFeedPage(payload)) {
    throw new OpportunitiesApiError(
      'The opportunity feed endpoint returned an unexpected payload.',
      { code: 'unexpected_payload', status: response.status },
    )
  }

  return payload
}

/**
 * One opportunity with its full description and current tracker state.
 *
 * A 404 is reported as `opportunity_not_found`, which the page renders as a
 * not-found state rather than a failure.
 */
export async function fetchOpportunityDetail(
  opportunityId: string,
  signal?: AbortSignal,
): Promise<OpportunityDetail> {
  const response = await fetch(
    `/api/v1/jobs/${encodeURIComponent(opportunityId)}`,
    { headers: { Accept: 'application/json' }, signal },
  )
  const payload = await readJson(response)

  if (!response.ok) {
    throw errorFromPayload(payload, response.status)
  }

  if (!isOpportunityDetail(payload)) {
    throw new OpportunitiesApiError(
      'The opportunity detail endpoint returned an unexpected payload.',
      { code: 'unexpected_payload', status: response.status },
    )
  }

  return payload
}

/**
 * Queue a tracker edit.
 *
 * Only the supplied keys are sent. The backend merges them into the existing
 * outbox projection, so an omitted field keeps whatever the canonical row holds
 * and an explicit `null` clears one. No Google request happens here; the manual
 * sync dispatcher publishes the edit later.
 */
export async function updateTracker(
  opportunityId: string,
  update: TrackerUpdate,
  signal?: AbortSignal,
): Promise<TrackerState> {
  const response = await fetch(
    `/api/v1/opportunities/${encodeURIComponent(opportunityId)}/tracker`,
    {
      method: 'PATCH',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(update),
      signal,
    },
  )
  const payload = await readJson(response)

  if (!response.ok) {
    throw errorFromPayload(payload, response.status)
  }

  if (!isTrackerState(payload)) {
    throw new OpportunitiesApiError(
      'The tracker endpoint returned an unexpected payload.',
      { code: 'unexpected_payload', status: response.status },
    )
  }

  return payload
}

export async function fetchOpportunityFeed(
  options: {
    feed: OpportunityFeedName
    page?: number
    pageSize?: number
    signal?: AbortSignal
  },
): Promise<OpportunityFeedPage> {
  const params = new URLSearchParams()
  params.set('feed', options.feed)
  params.set('page', String(Math.max(1, options.page ?? 1)))
  params.set(
    'page_size',
    String(options.pageSize ?? DEFAULT_PAGE_SIZE),
  )

  const response = await fetch(`/api/v1/jobs?${params.toString()}`, {
    headers: { Accept: 'application/json' },
    signal: options.signal,
  })

  return readResponse(response)
}
