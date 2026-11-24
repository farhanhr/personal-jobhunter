export type AIProviderId =
  | 'custom'
  | 'groq'
  | 'openrouter'
  | 'deepseek'
  | 'gemini'
  | 'ollama'

export type AIProviderPreset = {
  id: AIProviderId
  label: string
  baseUrl: string
  defaultModel: string
  requiresApiKey: boolean
  note: string
}

export type AISettings = {
  provider: string
  base_url: string
  api_key_masked: string | null
  model: string
  is_configured: boolean
  updated_at_utc: string | null
}

export type AISettingsUpdate = {
  provider: string
  base_url: string
  api_key: string | null
  model: string
}

export type AITestConnectionResult = {
  success: boolean
  latency_ms: number | null
  model: string | null
  error: string | null
}

/**
 * Presets only pre-fill the form. The saved values are always what the user
 * confirms, so editing a preset afterwards is allowed and never overwritten.
 */
export const AI_PROVIDER_PRESETS: AIProviderPreset[] = [
  {
    id: 'groq',
    label: 'Groq',
    baseUrl: 'https://api.groq.com/openai/v1',
    defaultModel: 'llama-3.3-70b-versatile',
    requiresApiKey: true,
    note: 'Fast hosted inference with a free tier.',
  },
  {
    id: 'openrouter',
    label: 'OpenRouter',
    baseUrl: 'https://openrouter.ai/api/v1',
    defaultModel: 'google/gemini-2.5-flash',
    requiresApiKey: true,
    note: 'One key for many hosted models.',
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com/v1',
    defaultModel: 'deepseek-chat',
    requiresApiKey: true,
    note: 'Low-cost hosted chat models.',
  },
  {
    id: 'gemini',
    label: 'Google Gemini (OpenAI-compatible)',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai/',
    defaultModel: 'gemini-2.5-flash',
    requiresApiKey: true,
    note: 'Gemini through its OpenAI-compatible endpoint.',
  },
  {
    id: 'ollama',
    label: 'Ollama (local)',
    baseUrl: 'http://localhost:11434/v1',
    defaultModel: 'llama3.2',
    requiresApiKey: false,
    note: 'Runs on this machine. No API key needed.',
  },
  {
    id: 'custom',
    label: 'Custom (OpenAI-compatible)',
    baseUrl: '',
    defaultModel: '',
    requiresApiKey: false,
    note: 'Any endpoint that speaks the OpenAI chat API.',
  },
]

export function presetFor(provider: string): AIProviderPreset | undefined {
  return AI_PROVIDER_PRESETS.find((preset) => preset.id === provider)
}

export type AISettingsApiErrorDetails = Record<string, unknown>

export class AISettingsApiError extends Error {
  readonly code: string
  readonly details: AISettingsApiErrorDetails
  readonly status: number

  constructor(
    message: string,
    options: { code: string; details?: AISettingsApiErrorDetails; status: number },
  ) {
    super(message)
    this.name = 'AISettingsApiError'
    this.code = options.code
    this.details = options.details ?? {}
    this.status = options.status
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isAISettings(value: unknown): value is AISettings {
  if (!isRecord(value)) {
    return false
  }

  return (
    typeof value.provider === 'string' &&
    typeof value.base_url === 'string' &&
    (value.api_key_masked === null || typeof value.api_key_masked === 'string') &&
    typeof value.model === 'string' &&
    typeof value.is_configured === 'boolean' &&
    (value.updated_at_utc === null || typeof value.updated_at_utc === 'string')
  )
}

function isTestConnectionResult(value: unknown): value is AITestConnectionResult {
  if (!isRecord(value)) {
    return false
  }

  return (
    typeof value.success === 'boolean' &&
    (value.latency_ms === null || typeof value.latency_ms === 'number') &&
    (value.model === null || typeof value.model === 'string') &&
    (value.error === null || typeof value.error === 'string')
  )
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function errorFromPayload(payload: unknown, status: number): AISettingsApiError {
  if (isRecord(payload) && isRecord(payload.error)) {
    const envelope = payload.error
    const code = typeof envelope.code === 'string' ? envelope.code : 'request_failed'
    const message =
      typeof envelope.message === 'string'
        ? envelope.message
        : 'The AI settings request failed.'
    const details = isRecord(envelope.details) ? envelope.details : {}

    return new AISettingsApiError(message, { code, details, status })
  }

  return new AISettingsApiError('The AI settings request failed.', {
    code: 'request_failed',
    status,
  })
}

async function readSettingsResponse(response: Response): Promise<AISettings> {
  const payload = await readJson(response)

  if (!response.ok) {
    throw errorFromPayload(payload, response.status)
  }

  if (!isAISettings(payload)) {
    throw new AISettingsApiError(
      'The AI settings endpoint returned an unexpected payload.',
      { code: 'unexpected_payload', status: response.status },
    )
  }

  return payload
}

export async function fetchAISettings(signal?: AbortSignal): Promise<AISettings> {
  const response = await fetch('/api/v1/ai/settings', {
    headers: { Accept: 'application/json' },
    signal,
  })

  return readSettingsResponse(response)
}

export async function saveAISettings(update: AISettingsUpdate): Promise<AISettings> {
  const response = await fetch('/api/v1/ai/settings', {
    method: 'PUT',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(update),
  })

  return readSettingsResponse(response)
}

export async function testAIConnection(
  update: Partial<AISettingsUpdate>,
): Promise<AITestConnectionResult> {
  const response = await fetch('/api/v1/ai/test-connection', {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(update),
  })
  const payload = await readJson(response)

  if (!response.ok) {
    throw errorFromPayload(payload, response.status)
  }

  if (!isTestConnectionResult(payload)) {
    throw new AISettingsApiError(
      'The connection test returned an unexpected payload.',
      { code: 'unexpected_payload', status: response.status },
    )
  }

  return payload
}
