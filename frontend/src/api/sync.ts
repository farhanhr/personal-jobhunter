/**
 * `GET /api/v1/sync/status`.
 *
 * Mirrors `SyncStatusResponse` in
 * `backend/src/personal_job_finder_api/api/v1/sync.py`. `totals.outstanding`
 * is the backend's own count of every outbox row that is not `synced`; the
 * browser renders it rather than re-deriving it from the per-status counts.
 */

export type SyncBlockedItem = {
  outbox_id: string
  entity_type: string
  entity_id: string
  attempt_count: number
  max_attempts: number
  last_error_code: string | null
  next_attempt_at_utc: string | null
  updated_at_utc: string
}

export type SyncStatus = {
  counts: Record<string, Record<string, number>>
  totals: Record<string, number>
  due_now: number
  action_required: boolean
  blocked: SyncBlockedItem[]
  blocked_truncated: boolean
  last_synced_at_utc: string | null
  checked_at_utc: string
}

export type SyncApiErrorDetails = Record<string, unknown>

export class SyncApiError extends Error {
  readonly code: string
  readonly details: SyncApiErrorDetails
  readonly status: number

  constructor(
    message: string,
    options: {
      code: string
      details?: SyncApiErrorDetails
      status: number
    },
  ) {
    super(message)
    this.name = 'SyncApiError'
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

/** A status→count map. Missing statuses are valid; the backend omits zeros. */
function isCountMap(value: unknown): value is Record<string, number> {
  return (
    isRecord(value) &&
    Object.values(value).every((count) => isNonNegativeInteger(count))
  )
}

function isCountsByEntity(
  value: unknown,
): value is Record<string, Record<string, number>> {
  return isRecord(value) && Object.values(value).every(isCountMap)
}

function isBlockedItem(value: unknown): value is SyncBlockedItem {
  return (
    isRecord(value) &&
    typeof value.outbox_id === 'string' &&
    typeof value.entity_type === 'string' &&
    typeof value.entity_id === 'string' &&
    isNonNegativeInteger(value.attempt_count) &&
    isNonNegativeInteger(value.max_attempts) &&
    isNullableString(value.last_error_code) &&
    isNullableString(value.next_attempt_at_utc) &&
    typeof value.updated_at_utc === 'string'
  )
}

function isSyncStatus(value: unknown): value is SyncStatus {
  if (!isRecord(value)) {
    return false
  }

  return (
    isCountsByEntity(value.counts) &&
    isCountMap(value.totals) &&
    isNonNegativeInteger(value.due_now) &&
    typeof value.action_required === 'boolean' &&
    Array.isArray(value.blocked) &&
    value.blocked.every(isBlockedItem) &&
    typeof value.blocked_truncated === 'boolean' &&
    isNullableString(value.last_synced_at_utc) &&
    typeof value.checked_at_utc === 'string'
  )
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function errorFromPayload(payload: unknown, status: number): SyncApiError {
  if (isRecord(payload) && isRecord(payload.error)) {
    const envelope = payload.error
    const code =
      typeof envelope.code === 'string' ? envelope.code : 'request_failed'
    const message =
      typeof envelope.message === 'string'
        ? envelope.message
        : 'The synchronization status request failed.'
    const details = isRecord(envelope.details) ? envelope.details : {}

    return new SyncApiError(message, { code, details, status })
  }

  return new SyncApiError('The synchronization status request failed.', {
    code: 'request_failed',
    status,
  })
}

export async function fetchSyncStatus(signal?: AbortSignal): Promise<SyncStatus> {
  const response = await fetch('/api/v1/sync/status', {
    headers: { Accept: 'application/json' },
    signal,
  })
  const payload = await readJson(response)

  if (!response.ok) {
    throw errorFromPayload(payload, response.status)
  }

  if (!isSyncStatus(payload)) {
    throw new SyncApiError(
      'The synchronization status endpoint returned an unexpected payload.',
      { code: 'unexpected_payload', status: response.status },
    )
  }

  return payload
}
