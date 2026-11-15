import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const healthyPayload = {
  status: 'ok',
  service: 'personal-job-finder-api',
  version: '0.1.0',
}

const emptySettingsPayload = {
  target_roles: [],
  max_target_roles: 3,
}

const blockedPolicyPayload = {
  timezone: 'Asia/Jakarta',
  operational_day: '2026-11-12',
  max_runs: 2,
  accepted_runs: 0,
  remaining_runs: 2,
  last_accepted_at: null,
  cooldown_until: null,
  cooldown_remaining_seconds: 0,
  next_eligible_at: null,
  next_reset_at: '2026-11-12T23:00:00Z',
  has_active_run: false,
  can_start: false,
  block_reason: 'no_usable_source',
  blocking_reasons: ['no_usable_source'],
}

const eligiblePolicyPayload = {
  ...blockedPolicyPayload,
  can_start: true,
  block_reason: null,
  blocking_reasons: [],
}

function mockResponse(body: unknown, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 503,
    json: async () => body,
  } as Response
}

function mockApiFetch(healthResponse: Response = mockResponse(healthyPayload)) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input)

    if (url === '/api/v1/health') {
      return Promise.resolve(healthResponse)
    }
    if (url === '/api/v1/settings/search') {
      return Promise.resolve(mockResponse(emptySettingsPayload))
    }
    if (url === '/api/v1/search-policy') {
      return Promise.resolve(mockResponse(blockedPolicyPayload))
    }
    if (url === '/api/v1/companies') {
      return Promise.resolve(mockResponse({ companies: [] }))
    }

    return Promise.reject(new Error(`Unexpected fetch request: ${url}`))
  })
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
    vi.stubGlobal('fetch', mockApiFetch())

    render(<App />)

    expect(await screen.findByText(/backend connected/i)).toBeInTheDocument()
    expect(screen.getByText(/personal-job-finder-api/i)).toBeInTheDocument()
    expect(screen.getByText('Version 0.1.0')).toBeInTheDocument()
    expect(
      await screen.findByRole('heading', { name: /target roles/i }),
    ).toBeInTheDocument()
    expect(
      await screen.findByRole('heading', { name: /manual search/i }),
    ).toBeInTheDocument()
    expect(
      await screen.findByRole('heading', { name: /company sources/i }),
    ).toBeInTheDocument()
  })

  it('requests the versioned health endpoint', async () => {
    const fetchMock = mockApiFetch()
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
    expect(screen.getAllByText(/network down/i).length).toBeGreaterThan(0)
  })

  it('shows the unavailable state for a non-successful response', async () => {
    vi.stubGlobal('fetch', mockApiFetch(mockResponse({}, false)))

    render(<App />)

    expect(await screen.findByText(/backend unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/status 503/i)).toBeInTheDocument()
  })

  it('rejects a malformed health payload', async () => {
    vi.stubGlobal('fetch', mockApiFetch(mockResponse({ status: 'ok' })))

    render(<App />)

    expect(await screen.findByText(/backend unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/unexpected payload/i)).toBeInTheDocument()
  })

  it('rejects a non-ok health status', async () => {
    vi.stubGlobal(
      'fetch',
      mockApiFetch(mockResponse({ ...healthyPayload, status: 'degraded' })),
    )

    render(<App />)

    expect(await screen.findByText(/backend unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/unexpected payload/i)).toBeInTheDocument()
  })

  it('re-checks search eligibility after a source is approved', async () => {
    const user = userEvent.setup()
    let policyCalls = 0
    let approved = false
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'

      if (url === '/api/v1/health') {
        return Promise.resolve(mockResponse(healthyPayload))
      }
      if (url === '/api/v1/settings/search') {
        return Promise.resolve(mockResponse(emptySettingsPayload))
      }
      if (url === '/api/v1/companies' && method === 'GET') {
        return Promise.resolve(
          mockResponse({
            companies: [
              {
                company_id: 'company_1',
                company_name: 'Example Company',
                ats_type: 'greenhouse',
                board_token: 'example-board',
                enabled: true,
                policy_status: approved ? 'permitted' : 'unreviewed',
                can_fetch: approved,
              },
            ],
          }),
        )
      }
      if (url === '/api/v1/companies/company_1' && method === 'PATCH') {
        approved = true
        return Promise.resolve(
          mockResponse({
            company_id: 'company_1',
            company_name: 'Example Company',
            ats_type: 'greenhouse',
            board_token: 'example-board',
            enabled: true,
            policy_status: 'permitted',
            can_fetch: true,
          }),
        )
      }
      if (url === '/api/v1/search-policy') {
        policyCalls += 1
        return Promise.resolve(
          mockResponse(approved ? eligiblePolicyPayload : blockedPolicyPayload),
        )
      }

      return Promise.reject(new Error(`Unexpected fetch request: ${url} ${method}`))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    const approveButton = await screen.findByRole('button', {
      name: /approve source/i,
    })
    expect(policyCalls).toBe(1)

    await user.click(approveButton)

    await waitFor(() => expect(policyCalls).toBe(2))
    expect(
      await screen.findByRole('button', { name: /start search/i }),
    ).toBeEnabled()
  })
})
