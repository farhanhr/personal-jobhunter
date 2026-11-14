import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const healthyPayload = {
  status: 'ok',
  service: 'personal-job-finder-api',
  version: '0.1.0',
}

function mockResponse(body: unknown, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 503,
    json: async () => body,
  } as Response
}

describe('App', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows a loading state before the health request resolves', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => undefined)),
    )

    render(<App />)

    expect(screen.getByText(/checking backend connection/i)).toBeInTheDocument()
    expect(screen.queryByText(/backend connected/i)).not.toBeInTheDocument()
  })

  it('shows the connected state when the backend is healthy', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(healthyPayload)))

    render(<App />)

    expect(await screen.findByText(/backend connected/i)).toBeInTheDocument()
    expect(screen.getByText(/personal-job-finder-api/i)).toBeInTheDocument()
    expect(screen.getByText('Version 0.1.0')).toBeInTheDocument()
  })

  it('requests the versioned health endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(healthyPayload))
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    await screen.findByText(/backend connected/i)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/health',
      expect.objectContaining({
        headers: { Accept: 'application/json' },
      }),
    )
  })

  it('shows the unavailable state when the request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error('Network down')),
    )

    render(<App />)

    expect(await screen.findByText(/backend unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/network down/i)).toBeInTheDocument()
  })

  it('shows the unavailable state for a non-successful response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(mockResponse({}, false)),
    )

    render(<App />)

    expect(await screen.findByText(/backend unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/status 503/i)).toBeInTheDocument()
  })

  it('rejects a malformed health payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(mockResponse({ status: 'ok' })),
    )

    render(<App />)

    expect(await screen.findByText(/backend unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/unexpected payload/i)).toBeInTheDocument()
  })

  it('rejects a non-ok health status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse({ ...healthyPayload, status: 'degraded' }),
      ),
    )

    render(<App />)

    expect(await screen.findByText(/backend unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/unexpected payload/i)).toBeInTheDocument()
  })
})
