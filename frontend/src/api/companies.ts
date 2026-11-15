export type CompanyPolicyStatus = 'unreviewed' | 'permitted' | 'blocked'

export type CompanySource = {
  company_id: string
  company_name: string
  ats_type: string
  board_token: string
  enabled: boolean
  policy_status: CompanyPolicyStatus
  can_fetch: boolean
}

export type CompanySourceDraft = {
  company_name: string
  board_token: string
}

export type CompanySourceUpdate = {
  policy_status?: CompanyPolicyStatus
  enabled?: boolean
}

export type CompaniesApiErrorDetails = Record<string, unknown>

export class CompaniesApiError extends Error {
  readonly code: string
  readonly details: CompaniesApiErrorDetails
  readonly status: number

  constructor(
    message: string,
    options: {
      code: string
      details?: CompaniesApiErrorDetails
      status: number
    },
  ) {
    super(message)
    this.name = 'CompaniesApiError'
    this.code = options.code
    this.details = options.details ?? {}
    this.status = options.status
  }
}

const POLICY_STATUSES = new Set<string>(['unreviewed', 'permitted', 'blocked'])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isCompanySource(value: unknown): value is CompanySource {
  if (!isRecord(value)) {
    return false
  }

  return (
    typeof value.company_id === 'string' &&
    value.company_id.length > 0 &&
    typeof value.company_name === 'string' &&
    value.company_name.length > 0 &&
    typeof value.ats_type === 'string' &&
    typeof value.board_token === 'string' &&
    typeof value.enabled === 'boolean' &&
    typeof value.policy_status === 'string' &&
    POLICY_STATUSES.has(value.policy_status) &&
    typeof value.can_fetch === 'boolean'
  )
}

function isCompanyList(value: unknown): value is { companies: CompanySource[] } {
  return (
    isRecord(value) &&
    Array.isArray(value.companies) &&
    value.companies.every(isCompanySource)
  )
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function errorFromPayload(payload: unknown, status: number): CompaniesApiError {
  if (isRecord(payload) && isRecord(payload.error)) {
    const envelope = payload.error
    const code =
      typeof envelope.code === 'string' ? envelope.code : 'request_failed'
    const message =
      typeof envelope.message === 'string'
        ? envelope.message
        : 'The company source request failed.'
    const details = isRecord(envelope.details) ? envelope.details : {}

    return new CompaniesApiError(message, { code, details, status })
  }

  return new CompaniesApiError('The company source request failed.', {
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
    throw new CompaniesApiError(
      'The company source endpoint returned an unexpected payload.',
      { code: 'unexpected_payload', status: response.status },
    )
  }

  return payload
}

export async function fetchCompanySources(
  signal?: AbortSignal,
): Promise<CompanySource[]> {
  const response = await fetch('/api/v1/companies', {
    headers: { Accept: 'application/json' },
    signal,
  })

  const payload = await readResponse(response, isCompanyList)
  return payload.companies
}

export async function createCompanySource(
  draft: CompanySourceDraft,
): Promise<CompanySource> {
  const response = await fetch('/api/v1/companies', {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      company_name: draft.company_name,
      board_token: draft.board_token,
    }),
  })

  return readResponse(response, isCompanySource)
}

export async function updateCompanySource(
  companyId: string,
  update: CompanySourceUpdate,
): Promise<CompanySource> {
  const response = await fetch(
    `/api/v1/companies/${encodeURIComponent(companyId)}`,
    {
      method: 'PATCH',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(update),
    },
  )

  return readResponse(response, isCompanySource)
}
