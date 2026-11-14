export type TargetRole = {
  role_id: string
  canonical_name: string
  aliases: string[]
}

export type TargetRoleDraft = {
  role_id?: string
  canonical_name: string
  aliases: string[]
}

export type TargetRoleSettings = {
  target_roles: TargetRole[]
  max_target_roles: number
}

export type TargetRolesApiErrorDetails = Record<string, unknown>

export class TargetRolesApiError extends Error {
  readonly code: string
  readonly details: TargetRolesApiErrorDetails
  readonly status: number

  constructor(
    message: string,
    options: {
      code: string
      details?: TargetRolesApiErrorDetails
      status: number
    },
  ) {
    super(message)
    this.name = 'TargetRolesApiError'
    this.code = options.code
    this.details = options.details ?? {}
    this.status = options.status
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isTargetRole(value: unknown): value is TargetRole {
  if (!isRecord(value)) {
    return false
  }

  return (
    typeof value.role_id === 'string' &&
    value.role_id.length > 0 &&
    typeof value.canonical_name === 'string' &&
    value.canonical_name.length > 0 &&
    isStringArray(value.aliases)
  )
}

function isTargetRoleSettings(value: unknown): value is TargetRoleSettings {
  if (!isRecord(value)) {
    return false
  }

  return (
    Array.isArray(value.target_roles) &&
    value.target_roles.every(isTargetRole) &&
    typeof value.max_target_roles === 'number' &&
    Number.isInteger(value.max_target_roles) &&
    value.max_target_roles > 0
  )
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function errorFromPayload(
  payload: unknown,
  status: number,
): TargetRolesApiError {
  if (isRecord(payload) && isRecord(payload.error)) {
    const envelope = payload.error
    const code =
      typeof envelope.code === 'string' ? envelope.code : 'request_failed'
    const message =
      typeof envelope.message === 'string'
        ? envelope.message
        : 'The settings request failed.'
    const details = isRecord(envelope.details) ? envelope.details : {}

    return new TargetRolesApiError(message, { code, details, status })
  }

  return new TargetRolesApiError('The settings request failed.', {
    code: 'request_failed',
    status,
  })
}

async function readSettingsResponse(
  response: Response,
): Promise<TargetRoleSettings> {
  const payload = await readJson(response)

  if (!response.ok) {
    throw errorFromPayload(payload, response.status)
  }

  if (!isTargetRoleSettings(payload)) {
    throw new TargetRolesApiError(
      'The settings endpoint returned an unexpected payload.',
      { code: 'unexpected_payload', status: response.status },
    )
  }

  return payload
}

export async function fetchTargetRoles(
  signal?: AbortSignal,
): Promise<TargetRoleSettings> {
  const response = await fetch('/api/v1/settings/search', {
    headers: { Accept: 'application/json' },
    signal,
  })

  return readSettingsResponse(response)
}

export async function saveTargetRoles(
  roles: TargetRoleDraft[],
): Promise<TargetRoleSettings> {
  const response = await fetch('/api/v1/settings/search', {
    method: 'PUT',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ target_roles: roles }),
  })

  return readSettingsResponse(response)
}
