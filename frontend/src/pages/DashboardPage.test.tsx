import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { DashboardSummary } from '../api/dashboard'
import type { SearchRunStatus } from '../api/searchRuns'
import type { SyncStatus } from '../api/sync'
import { DashboardPage } from './DashboardPage'

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

const health = {
  status: 'ok',
  service: 'personal-job-finder-api',
  version: '0.1.0',
}

const policy = {
  timezone: 'Asia/Jakarta',
  operational_day: '2026-11-18',
  max_runs: 2,
  accepted_runs: 1,
  remaining_runs: 1,
  last_accepted_at: '2026-11-18T01:00:00Z',
  cooldown_until: '2026-11-18T13:00:00Z',
  cooldown_remaining_seconds: 3600,
  next_eligible_at: '2026-11-18T13:00:00Z',
  next_reset_at: '2026-11-18T23:00:00Z',
  has_active_run: false,
  can_start: false,
  block_reason: 'cooldown_active',
  blocking_reasons: ['cooldown_active'],
}

const summary: DashboardSummary = {
  as_of_date_jkt: '2026-11-18',
  total_opportunities: 42,
  excluded_opportunities: 3,
  new_opportunities: 7,
  new_opportunities_window: { start: '2026-11-12', end: '2026-11-18' },
  applications_window: { start: '2026-11-12', end: '2026-11-18' },
  applications_last_7_days: 4,
  interviews_window: { start: '2026-11-18', end: '2026-11-25' },
  upcoming_interviews: 1,
  opportunities_by_target_role: [
    { role_id: 'role_1', label: 'Data Analyst', count: 30 },
    { role_id: null, label: 'Unmatched', count: 12 },
  ],
  opportunities_by_source: [
    { source_type: 'greenhouse', count: 28 },
    { source_type: 'lever', count: 15 },
  ],
  opportunities_by_location: [
    { province: 'DKI Jakarta', city: 'Jakarta Selatan', count: 20 },
    { province: 'Unknown', city: 'Unknown', count: 2 },
  ],
  latest_read_model_refresh_at_utc: '2026-11-18T01:00:00Z',
  data_quality: {
    invalid_date_value_count: 1,
    invalid_date_values: [
      {
        opportunity_id: 'opp_1',
        field: 'date_applied',
        raw_value: '31/02/2026',
        reason: 'invalid_calendar_date',
      },
    ],
    invalid_date_values_truncated: false,
  },
}

const latestRun: SearchRunStatus = {
  run_id: 'run_20261118',
  status: 'completed_with_warnings',
  accepted_at_utc: '2026-11-18T01:00:00Z',
  started_at_utc: '2026-11-18T01:00:05Z',
  finished_at_utc: '2026-11-18T01:04:00Z',
  operational_day: '2026-11-18',
  progress: { stage: 'completed', completed_sources: 1, total_sources: 2 },
  source_results: [
    {
      source_id: 'board_nusantara',
      status: 'ok',
      records_found: 12,
      error_code: null,
    },
    {
      source_id: 'board_contoh',
      status: 'failed',
      records_found: 0,
      error_code: 'http_429',
    },
  ],
  error_summary: 'One source was rate limited.',
}

const syncStatus: SyncStatus = {
  counts: { opportunity: { pending: 2 }, job_source: { blocked: 1 } },
  totals: { pending: 2, blocked: 1, synced: 5, outstanding: 3 },
  due_now: 2,
  action_required: true,
  blocked: [
    {
      outbox_id: 'outbox_1',
      entity_type: 'job_source',
      entity_id: 'source_contoh',
      attempt_count: 3,
      max_attempts: 3,
      last_error_code: 'schema_mismatch',
      next_attempt_at_utc: null,
      updated_at_utc: '2026-11-18T02:00:00Z',
    },
  ],
  blocked_truncated: false,
  last_synced_at_utc: '2026-11-18T01:30:00Z',
  checked_at_utc: '2026-11-18T03:00:00Z',
}

type Overrides = {
  summary?: Response
  latest?: Response
  sync?: Response
  policy?: Response
}

function mockApi(overrides: Overrides = {}) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input)

    if (url === '/api/v1/health') return Promise.resolve(mockResponse(health))
    if (url === '/api/v1/search-policy') {
      return Promise.resolve(overrides.policy ?? mockResponse(policy))
    }
    if (url === '/api/v1/dashboard/summary') {
      return Promise.resolve(overrides.summary ?? mockResponse(summary))
    }
    if (url === '/api/v1/search-runs/latest') {
      return Promise.resolve(overrides.latest ?? mockResponse(latestRun))
    }
    if (url === '/api/v1/sync/status') {
      return Promise.resolve(overrides.sync ?? mockResponse(syncStatus))
    }

    return Promise.reject(new Error(`Unexpected fetch request: ${url}`))
  })
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  )
}

describe('DashboardPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders the reporting date and the summary figures', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDashboard()

    // The toolbar and the freshness section both state the reporting date.
    expect(
      await screen.findAllByText(/reporting date/i),
    ).not.toHaveLength(0)
    expect(screen.getByText('18 Nov 2026')).toBeInTheDocument()

    expect(await screen.findByText('42')).toBeInTheDocument()
    const activity = screen.getByRole('region', { name: /^activity$/i })
    expect(within(activity).getByText('42')).toBeInTheDocument()
    expect(within(activity).getByText('7')).toBeInTheDocument()
    expect(within(activity).getByText('4')).toBeInTheDocument()
    expect(within(activity).getByText('1')).toBeInTheDocument()
    expect(
      within(activity).getByText(/3 excluded by location scope/i),
    ).toBeInTheDocument()
  })

  it('states the exact windows the backend returned', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDashboard()

    expect(
      await screen.findAllByText('12 Nov – 18 Nov 2026'),
    ).toHaveLength(2)
    expect(screen.getByText('18 Nov – 25 Nov 2026')).toBeInTheDocument()
  })

  it('renders the search allowance from the policy response', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDashboard()

    expect(await screen.findByText(/search left/i)).toBeInTheDocument()
    const allowance = screen.getByRole('region', { name: /search allowance/i })
    // The allowance and the run card both name the operational day.
    expect(within(allowance).getByText('Operational day')).toBeInTheDocument()
    expect(within(allowance).getByText('2026-11-18')).toBeInTheDocument()
    expect(within(allowance).getByText('1 of 2')).toBeInTheDocument()
    expect(within(allowance).getByText('No')).toBeInTheDocument()
    expect(
      within(allowance).getByText(/the 12-hour cooldown is still active/i),
    ).toBeInTheDocument()
  })

  it('groups opportunities without re-sorting or re-summing them', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDashboard()

    expect(await screen.findByText('Data Analyst')).toBeInTheDocument()
    expect(screen.getByText('Unmatched')).toBeInTheDocument()
    expect(screen.getByText('greenhouse')).toBeInTheDocument()
    expect(screen.getByText('Jakarta Selatan, DKI Jakarta')).toBeInTheDocument()
    expect(screen.getByText('Location unknown')).toBeInTheDocument()
    expect(
      screen.getByText(/can add up to more than the opportunity total/i),
    ).toBeInTheDocument()
  })

  it('renders the latest run with its timestamps and source outcomes', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDashboard()

    expect(
      await screen.findByText(/completed with warnings/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/run_20261118/i)).toBeInTheDocument()
    // Accepted, started and finished are all 18 Nov 2026 in Jakarta.
    expect(
      screen.getAllByText(/18 Nov 2026, 0[89]:0\d/).length,
    ).toBeGreaterThan(0)
    expect(screen.getByText(/board_contoh/i)).toBeInTheDocument()
    expect(screen.getByText(/http_429/)).toBeInTheDocument()
    expect(screen.getByText(/one source was rate limited/i)).toBeInTheDocument()
  })

  it('treats a null latest run as an empty state, not an error', async () => {
    vi.stubGlobal('fetch', mockApi({ latest: mockResponse(null) }))

    renderDashboard()

    expect(
      await screen.findByText(/no search has been accepted yet/i),
    ).toBeInTheDocument()
    expect(
      screen.queryByText(/could not load the latest run/i),
    ).not.toBeInTheDocument()
  })

  it('renders synchronization state from the sync endpoint', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDashboard()

    expect(await screen.findByText(/outstanding/i)).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(
      screen.getByText(/action needed before these records can sync/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/schema_mismatch/)).toBeInTheDocument()
  })

  it('reports an unrefreshed read model instead of implying freshness', async () => {
    vi.stubGlobal(
      'fetch',
      mockApi({
        summary: mockResponse({
          ...summary,
          latest_read_model_refresh_at_utc: null,
        }),
      }),
    )

    renderDashboard()

    expect(
      await screen.findByText(/never refreshed from sheets/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/has not been refreshed yet/i),
    ).toBeInTheDocument()
  })

  it('lists invalid tracker dates with their original text', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDashboard()

    expect(
      await screen.findByText(/1 value could not be read as a date/i),
    ).toBeInTheDocument()
    expect(screen.getByText('31/02/2026')).toBeInTheDocument()
    expect(screen.getByText(/not a real calendar date/i)).toBeInTheDocument()
    expect(screen.getByText(/excluded from the application/i)).toBeInTheDocument()
  })

  it('keeps independent sections usable when the summary request fails', async () => {
    vi.stubGlobal(
      'fetch',
      mockApi({
        summary: mockResponse(
          { error: { code: 'read_model_error', message: 'Read model failed.' } },
          500,
        ),
      }),
    )

    renderDashboard()

    expect(
      await screen.findAllByText(/could not load the dashboard figures/i),
    ).toHaveLength(3)
    expect(screen.getAllByText('Read model failed.')).toHaveLength(3)

    // The allowance, latest run and sync sections are independent resources.
    expect(await screen.findByText(/search left/i)).toBeInTheDocument()
    expect(screen.getByText(/run_20261118/i)).toBeInTheDocument()
    expect(screen.getByText(/outstanding/i)).toBeInTheDocument()
    expect(
      screen.getByText(/reporting date unavailable/i),
    ).toBeInTheDocument()
  })

  it('shows a retryable error when the latest run request fails', async () => {
    vi.stubGlobal(
      'fetch',
      mockApi({
        latest: mockResponse(
          { error: { code: 'run_error', message: 'Run store unavailable.' } },
          503,
        ),
      }),
    )

    renderDashboard()

    expect(
      await screen.findByText(/could not load the latest run/i),
    ).toBeInTheDocument()
    expect(screen.getByText('Run store unavailable.')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /^retry$/i }),
    ).toBeInTheDocument()
  })

  it('reloads every section when Refresh dashboard is clicked', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi()
    vi.stubGlobal('fetch', fetchMock)

    renderDashboard()

    await screen.findByText('42')
    const countFor = (url: string) =>
      fetchMock.mock.calls.filter(([input]) => String(input) === url).length

    expect(countFor('/api/v1/dashboard/summary')).toBe(1)

    await user.click(
      screen.getByRole('button', { name: /refresh dashboard/i }),
    )

    await waitFor(() =>
      expect(countFor('/api/v1/dashboard/summary')).toBe(2),
    )
    expect(countFor('/api/v1/search-runs/latest')).toBe(2)
    expect(countFor('/api/v1/sync/status')).toBe(2)
    expect(countFor('/api/v1/search-policy')).toBe(2)
  })
})
