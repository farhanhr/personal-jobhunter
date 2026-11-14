export type HealthResponse = {
  status: string
  service: string
  version: string
}

function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== 'object' || value === null) {
    return false
  }

  const candidate = value as Record<string, unknown>
  return (
    candidate.status === 'ok' &&
    typeof candidate.service === 'string' &&
    typeof candidate.version === 'string'
  )
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch('/api/v1/health', {
    headers: { Accept: 'application/json' },
    signal,
  })

  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`)
  }

  const payload: unknown = await response.json()
  if (!isHealthResponse(payload)) {
    throw new Error('Health check returned an unexpected payload')
  }

  return payload
}
