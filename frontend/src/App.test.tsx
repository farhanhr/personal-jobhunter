import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppRoutes } from './App'

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

const emptySummaryPayload = {
  as_of_date_jkt: '2026-11-18',
  total_opportunities: 0,
  excluded_opportunities: 0,
  new_opportunities: 0,
  new_opportunities_window: { start: '2026-11-12', end: '2026-11-18' },
  applications_window: { start: '2026-11-12', end: '2026-11-18' },
  applications_last_7_days: 0,
  interviews_window: { start: '2026-11-18', end: '2026-11-25' },
  upcoming_interviews: 0,
  opportunities_by_target_role: [],
  opportunities_by_source: [],
  opportunities_by_location: [],
  latest_read_model_refresh_at_utc: null,
  data_quality: {
    invalid_date_value_count: 0,
    invalid_date_values: [],
    invalid_date_values_truncated: false,
  },
}

const emptySyncPayload = {
  counts: { opportunity: {}, job_source: {} },
  totals: { outstanding: 0 },
  due_now: 0,
  action_required: false,
  blocked: [],
  blocked_truncated: false,
  last_synced_at_utc: null,
  checked_at_utc: '2026-11-18T03:00:00Z',
}

const emptyFeedPayload = {
  items: [],
  page: 1,
  page_size: 25,
  total_items: 0,
  total_pages: 0,
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
    if (url === '/api/v1/dashboard/summary') {
      return Promise.resolve(mockResponse(emptySummaryPayload))
    }
    if (url === '/api/v1/search-runs/latest') {
      return Promise.resolve(mockResponse(null))
    }
    if (url === '/api/v1/sync/status') {
      return Promise.resolve(mockResponse(emptySyncPayload))
    }
    if (url.startsWith('/api/v1/jobs?')) {
      return Promise.resolve(mockResponse(emptyFeedPayload))
    }

    return Promise.reject(new Error(`Unexpected fetch request: ${url}`))
  })
}

function renderRoute(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  )
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

    renderRoute()

    expect(screen.getByText(/checking backend connection/i)).toBeInTheDocument()
    expect(screen.queryByText(/backend connected/i)).not.toBeInTheDocument()
  })

  it('shows the connected state when the backend is healthy', async () => {
    vi.stubGlobal('fetch', mockApiFetch())

    renderRoute()

    expect(await screen.findByText(/backend connected/i)).toBeInTheDocument()
    expect(screen.getByText(/personal-job-finder-api/i)).toBeInTheDocument()
    expect(screen.getByText('Version 0.1.0')).toBeInTheDocument()
    expect(
      await screen.findByRole('heading', { name: /search allowance/i }),
    ).toBeInTheDocument()
  })

  it('requests the versioned health endpoint', async () => {
    const fetchMock = mockApiFetch()
    vi.stubGlobal('fetch', fetchMock)

    renderRoute()

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

    renderRoute()

    expect(await screen.findByText(/backend unavailable/i)).toBeInTheDocument()
    expect(screen.getAllByText(/network down/i).length).toBeGreaterThan(0)
  })

  it('shows the unavailable state for a non-successful response', async () => {
    vi.stubGlobal('fetch', mockApiFetch(mockResponse({}, false)))

    renderRoute()

    expect(await screen.findByText(/backend unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/status 503/i)).toBeInTheDocument()
  })

  it('rejects a malformed health payload', async () => {
    vi.stubGlobal('fetch', mockApiFetch(mockResponse({ status: 'ok' })))

    renderRoute()

    expect(await screen.findByText(/backend unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/unexpected payload/i)).toBeInTheDocument()
  })

  it('rejects a non-ok health status', async () => {
    vi.stubGlobal(
      'fetch',
      mockApiFetch(mockResponse({ ...healthyPayload, status: 'degraded' })),
    )

    renderRoute()

    expect(await screen.findByText(/backend unavailable/i)).toBeInTheDocument()
    expect(screen.getByText(/unexpected payload/i)).toBeInTheDocument()
  })

  it('shows target roles and company sources on the settings page', async () => {
    vi.stubGlobal('fetch', mockApiFetch())

    renderRoute('/settings')

    expect(
      await screen.findByRole('heading', { name: /target roles/i }),
    ).toBeInTheDocument()
    expect(
      await screen.findByRole('heading', { name: /company sources/i }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: /search allowance/i }),
    ).not.toBeInTheDocument()
  })

  it('renders the opportunities feed from the backend without inventing data', async () => {
    vi.stubGlobal('fetch', mockApiFetch())

    renderRoute('/opportunities')

    // The page heading is the h1; the bucket is a named tab panel.
    expect(
      await screen.findByRole('heading', { level: 1, name: /opportunities/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole('tabpanel', { name: 'Main feed' })).toBeInTheDocument()
    // The empty bucket states itself rather than showing placeholder rows.
    expect(
      await screen.findByText(/nothing has been discovered yet/i),
    ).toBeInTheDocument()
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
  })

  it('offers navigation between the three destinations', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', mockApiFetch())

    renderRoute()

    await user.click(screen.getByRole('link', { name: 'Settings' }))

    expect(
      await screen.findByRole('heading', { name: /target roles/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute(
      'aria-current',
      'page',
    )
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

      return Promise.reject(
        new Error(`Unexpected fetch request: ${url} ${method}`),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    renderRoute('/settings')

    const approveButton = await screen.findByRole('button', {
      name: /approve source/i,
    })
    await user.click(approveButton)

    await waitFor(() => expect(approved).toBe(true))

    await user.click(screen.getByRole('link', { name: 'Dashboard' }))

    expect(
      await screen.findByRole('button', { name: /start search/i }),
    ).toBeEnabled()
    expect(policyCalls).toBeGreaterThan(0)
  })
})
