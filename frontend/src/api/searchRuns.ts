export type SearchPolicy = {
  timezone: string
  operational_day: string
  max_runs: number
  accepted_runs: number
  remaining_runs: number
  last_accepted_at: string | null
  cooldown_until: string | null
  cooldown_remaining_seconds: number
  next_eligible_at: string | null
  next_reset_at: string
  has_active_run: boolean
  can_start: boolean
  block_reason: string | null
  blocking_reasons: string[]
}

export type SearchRunProgress = {
  stage: string
  completed_sources: number
  total_sources: number
}

export type SourceRunResult = {
  source_id: string
  status: string
  records_found: number
  error_code: string | null
}

export type SearchRunStatus = {
  run_id: string
  status: string
  accepted_at_utc: string
  started_at_utc: string | null
  finished_at_utc: string | null
  operational_day: string
  progress: SearchRunProgress | null
  source_results: SourceRunResult[]
  error_summary: string | null
}

export type StartSearchRunResponse = {
  run_id: string
  status: string
  accepted_at_utc: string
  operational_day: string
  remaining_runs: number
  status_url: string
}

export type SearchRunsApiErrorDetails = Record<string, unknown>

export class SearchRunsApiError extends Error {
  readonly code: string
  readonly details: SearchRunsApiErrorDetails
  readonly status: number

  constructor(
    message: string,
    options: {
      code: string
      details?: SearchRunsApiErrorDetails
      status: number
    },
  ) {
    super(message)
    this.name = 'SearchRunsApiError'
    this.code = options.code
    this.details = options.details ?? {}
    this.status = options.status
  }
}

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

function isSearchPolicy(value: unknown): value is SearchPolicy {
  if (!isRecord(value)) {
    return false
  }

  return (
    typeof value.timezone === 'string' &&
    typeof value.operational_day === 'string' &&
    isNonNegativeInteger(value.max_runs) &&
    isNonNegativeInteger(value.accepted_runs) &&
    isNonNegativeInteger(value.remaining_runs) &&
    isNullableString(value.last_accepted_at) &&
    isNullableString(value.cooldown_until) &&
    isNonNegativeInteger(value.cooldown_remaining_seconds) &&
    isNullableString(value.next_eligible_at) &&
    typeof value.next_reset_at === 'string' &&
    typeof value.has_active_run === 'boolean' &&
    typeof value.can_start === 'boolean' &&
    isNullableString(value.block_reason) &&
    isStringArray(value.blocking_reasons)
  )
}

function isProgress(value: unknown): value is SearchRunProgress {
  return (
    isRecord(value) &&
    typeof value.stage === 'string' &&
    isNonNegativeInteger(value.completed_sources) &&
    isNonNegativeInteger(value.total_sources)
  )
}

function isSourceResult(value: unknown): value is SourceRunResult {
  return (
    isRecord(value) &&
    typeof value.source_id === 'string' &&
    typeof value.status === 'string' &&
    isNonNegativeInteger(value.records_found) &&
    isNullableString(value.error_code)
  )
}

function isSearchRunStatus(value: unknown): value is SearchRunStatus {
  if (!isRecord(value)) {
    return false
  }

  return (
    typeof value.run_id === 'string' &&
    typeof value.status === 'string' &&
    typeof value.accepted_at_utc === 'string' &&
    isNullableString(value.started_at_utc) &&
    isNullableString(value.finished_at_utc) &&
    typeof value.operational_day === 'string' &&
    (value.progress === null || isProgress(value.progress)) &&
    Array.isArray(value.source_results) &&
    value.source_results.every(isSourceResult) &&
    isNullableString(value.error_summary)
  )
}

function isStartResponse(value: unknown): value is StartSearchRunResponse {
  return (
    isRecord(value) &&
    typeof value.run_id === 'string' &&
    typeof value.status === 'string' &&
    typeof value.accepted_at_utc === 'string' &&
    typeof value.operational_day === 'string' &&
    isNonNegativeInteger(value.remaining_runs) &&
    typeof value.status_url === 'string'
  )
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function errorFromPayload(payload: unknown, status: number): SearchRunsApiError {
  if (isRecord(payload) && isRecord(payload.error)) {
    const envelope = payload.error
    const code =
      typeof envelope.code === 'string' ? envelope.code : 'request_failed'
    const message =
      typeof envelope.message === 'string'
        ? envelope.message
        : 'The search request failed.'
    const details = isRecord(envelope.details) ? envelope.details : {}

    return new SearchRunsApiError(message, { code, details, status })
  }

  return new SearchRunsApiError('The search request failed.', {
    code: 'request_failed',
    status,
  })
}

async function readResponse<T>(
  response: Response,
  isExpected: (value: unknown) => value is T,
): Promise<T> {
  const payload = await readJson(response)

  if (!response.ok) {
    throw errorFromPayload(payload, response.status)
  }

  if (!isExpected(payload)) {
    throw new SearchRunsApiError(
      'The search endpoint returned an unexpected payload.',
      { code: 'unexpected_payload', status: response.status },
    )
  }

  return payload
}

export async function fetchSearchPolicy(
  signal?: AbortSignal,
): Promise<SearchPolicy> {
  const response = await fetch('/api/v1/search-policy', {
    headers: { Accept: 'application/json' },
    signal,
  })

  return readResponse(response, isSearchPolicy)
}

export async function startSearchRun(
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<StartSearchRunResponse> {
  const response = await fetch('/api/v1/search-runs', {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: '{}',
    signal,
  })

  return readResponse(response, isStartResponse)
}

export async function fetchSearchRun(
  runId: string,
  signal?: AbortSignal,
): Promise<SearchRunStatus> {
  const response = await fetch(
    `/api/v1/search-runs/${encodeURIComponent(runId)}`,
    {
      headers: { Accept: 'application/json' },
      signal,
    },
  )

  return readResponse(response, isSearchRunStatus)
}
