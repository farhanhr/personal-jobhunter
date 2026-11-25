export type IntegrationState =
  | 'not_configured'
  | 'awaiting_spreadsheet'
  | 'configured'
  | 'credentials_invalid'
  | 'ready'

export type IntegrationStatus = {
  state: IntegrationState
  message: string
  service_account_email: string | null
  key_installed: boolean
  spreadsheet_configured: boolean
  spreadsheet_source: 'ui' | 'cli' | 'environment' | 'none'
  last_setup_stage: string | null
  workspace_ready: boolean
  onboarding_required: boolean
  configured_via: 'ui' | 'cli' | 'environment' | null
  setup_completed_at: string | null
  configuration_error: string | null
}

export type SetupStage =
  | 'credentials'
  | 'spreadsheet'
  | 'access'
  | 'workspace'
  | 'schema'
  | null

export type SetupOutcome = {
  ready: boolean
  stage: SetupStage
  code: string
  message: string
  service_account_email: string | null
  source: 'ui' | 'cli' | 'environment' | 'none'
  retry_safe: boolean
  preserved: string
  optional_warning: string | null
  workspace_ready: boolean
  adopted_existing: boolean
  refresh_warning: string | null
  counts: Record<string, number> | null
}

export class GoogleSheetsSettingsError extends Error {
  readonly code: string
  readonly status: number

  constructor(message: string, options: { code: string; status: number }) {
    super(message)
    this.name = 'GoogleSheetsSettingsError'
    this.code = options.code
    this.status = options.status
  }
}

const BASE = '/api/v1/integrations/google-sheets'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

async function failure(response: Response): Promise<GoogleSheetsSettingsError> {
  const payload = await readJson(response)
  if (isRecord(payload) && isRecord(payload.error)) {
    const code = typeof payload.error.code === 'string' ? payload.error.code : 'request_failed'
    const message =
      typeof payload.error.message === 'string'
        ? payload.error.message
        : 'The Google Sheets request failed.'
    return new GoogleSheetsSettingsError(message, { code, status: response.status })
  }
  return new GoogleSheetsSettingsError('The Google Sheets request failed.', {
    code: 'request_failed',
    status: response.status,
  })
}

async function expectJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw await failure(response)
  }
  const payload = await readJson(response)
  if (!isRecord(payload)) {
    throw new GoogleSheetsSettingsError('The settings endpoint returned an unexpected payload.', {
      code: 'unexpected_payload',
      status: response.status,
    })
  }
  return payload as T
}

export async function fetchIntegrationStatus(signal?: AbortSignal): Promise<IntegrationStatus> {
  const response = await fetch(`${BASE}/status`, {
    headers: { Accept: 'application/json' },
    signal,
  })
  return expectJson<IntegrationStatus>(response)
}

export async function uploadServiceAccountKey(file: File): Promise<IntegrationStatus> {
  const body = new FormData()
  body.append('file', file)
  const response = await fetch(`${BASE}/service-account`, {
    method: 'PUT',
    headers: { Accept: 'application/json' },
    body,
    credentials: 'same-origin',
  })
  return expectJson<IntegrationStatus>(response)
}

export async function runSaveAndSetup(spreadsheetUrl: string | undefined): Promise<SetupOutcome> {
  const response = await fetch(`${BASE}/setup`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(spreadsheetUrl ? { spreadsheet_url: spreadsheetUrl } : {}),
    credentials: 'same-origin',
  })
  return expectJson<SetupOutcome>(response)
}

export async function disconnectServiceAccount(): Promise<IntegrationStatus> {
  const response = await fetch(`${BASE}/service-account`, {
    method: 'DELETE',
    headers: { Accept: 'application/json' },
    credentials: 'same-origin',
  })
  return expectJson<IntegrationStatus>(response)
}
