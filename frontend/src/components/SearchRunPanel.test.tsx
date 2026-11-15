import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SearchPolicy, SearchRunStatus } from '../api/searchRuns'
import { SearchRunPanel } from './SearchRunPanel'

const eligiblePolicy: SearchPolicy = {
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
  can_start: true,
  block_reason: null,
  blocking_reasons: [],
}

const blockedPolicy: SearchPolicy = {
  ...eligiblePolicy,
  can_start: false,
  block_reason: 'no_usable_source',
  blocking_reasons: ['no_usable_source'],
}

const startResponse = {
  run_id: 'run_abc',
  status: 'accepted',
  accepted_at_utc: '2026-11-12T00:00:00Z',
  operational_day: '2026-11-12',
  remaining_runs: 1,
  status_url: '/api/v1/search-runs/run_abc',
}

const completedRun: SearchRunStatus = {
  run_id: 'run_abc',
  status: 'completed',
  accepted_at_utc: '2026-11-12T00:00:00Z',
  started_at_utc: '2026-11-12T00:00:01Z',
  finished_at_utc: '2026-11-12T00:00:02Z',
  operational_day: '2026-11-12',
  progress: {
    stage: 'completed',
    completed_sources: 1,
    total_sources: 1,
  },
  source_results: [
    {
      source_id: 'company_1',
      status: 'completed',
      records_found: 1,
      error_code: null,
    },
  ],
  error_summary: null,
}

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

function setupFetch({
  policy = eligiblePolicy,
  start,
  status = completedRun,
}: {
  policy?: unknown
  start?: () => Promise<Response>
  status?: unknown
} = {}) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)

    if (url === '/api/v1/search-policy') {
      return Promise.resolve(mockResponse(policy))
    }
    if (url === '/api/v1/search-runs') {
      return start
        ? start()
        : Promise.resolve(mockResponse(startResponse, 202))
    }
    if (url.startsWith('/api/v1/search-runs/')) {
      return Promise.resolve(mockResponse(status))
    }

    return Promise.reject(new Error(`Unexpected fetch request: ${url}`))
  })

  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function startCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(
    ([input, init]) =>
      String(input) === '/api/v1/search-runs' &&
      (init as RequestInit | undefined)?.method === 'POST',
  )
}

function statusCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(([input]) =>
    String(input).startsWith('/api/v1/search-runs/'),
  )
}

describe('SearchRunPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('disables Start Search and explains why when no usable source exists', async () => {
    const fetchMock = setupFetch({ policy: blockedPolicy })

    render(<SearchRunPanel />)

    expect(
      await screen.findByText(/no approved source is available/i),
    ).toBeInTheDocument()
    const startButton = screen.getByRole('button', { name: /start search/i })
    expect(startButton).toBeDisabled()

    fireEvent.click(startButton)
    expect(startCalls(fetchMock)).toHaveLength(0)
  })

  it('prevents duplicate submissions while the first start request is pending', async () => {
    let resolveStart: ((response: Response) => void) | undefined
    const pendingStart = new Promise<Response>((resolve) => {
      resolveStart = resolve
    })
    const fetchMock = setupFetch({ start: () => pendingStart })

    render(<SearchRunPanel />)

    const startButton = await screen.findByRole('button', {
      name: /start search/i,
    })
    await waitFor(() => expect(startButton).toBeEnabled())

    fireEvent.click(startButton)
    fireEvent.click(startButton)

    expect(startCalls(fetchMock)).toHaveLength(1)

    await act(async () => {
      resolveStart?.(mockResponse(startResponse, 202))
      await pendingStart
    })

    expect(await screen.findByText(/^completed$/i)).toBeInTheDocument()
  })

  it('reuses the same idempotency key when the same start action is retried', async () => {
    const user = userEvent.setup()
    let startAttempt = 0
    setupFetch({
      start: () => {
        startAttempt += 1
        if (startAttempt === 1) {
          return Promise.reject(new Error('Network down'))
        }
        return Promise.resolve(mockResponse(startResponse, 202))
      },
    })
    const fetchMock = vi.mocked(fetch)

    render(<SearchRunPanel />)

    const startButton = await screen.findByRole('button', {
      name: /start search/i,
    })
    await waitFor(() => expect(startButton).toBeEnabled())

    await user.click(startButton)
    expect(await screen.findByText(/network down/i)).toBeInTheDocument()

    await user.click(startButton)
    await screen.findByText(/^completed$/i)

    const calls = startCalls(fetchMock)
    expect(calls).toHaveLength(2)
    const firstHeaders = (calls[0][1] as RequestInit).headers as Record<
      string,
      string
    >
    const secondHeaders = (calls[1][1] as RequestInit).headers as Record<
      string,
      string
    >
    expect(firstHeaders['Idempotency-Key']).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    )
    expect(secondHeaders['Idempotency-Key']).toBe(
      firstHeaders['Idempotency-Key'],
    )
  })

  it('renders the terminal run state, progress, and source results', async () => {
    const user = userEvent.setup()
    setupFetch()

    render(<SearchRunPanel />)

    await user.click(
      await screen.findByRole('button', { name: /start search/i }),
    )

    expect(await screen.findByText(/^completed$/i)).toBeInTheDocument()
    expect(
      screen.getByText(/1 of 1 configured sources completed successfully/i),
    ).toBeInTheDocument()
    expect(screen.getByText('company_1')).toBeInTheDocument()
    expect(screen.getByText(/1 record found/i)).toBeInTheDocument()
  })

  it('renders connector counters for a completed source', async () => {
    const user = userEvent.setup()
    setupFetch({
      status: {
        ...completedRun,
        source_results: [
          {
            source_id: 'company_1',
            status: 'completed',
            records_found: 3,
            error_code: null,
            records_relevant: 1,
            records_filtered: 2,
            records_invalid: 0,
          },
        ],
      },
    })

    render(<SearchRunPanel />)

    await user.click(
      await screen.findByRole('button', { name: /start search/i }),
    )

    expect(await screen.findByText(/3 records found/i)).toBeInTheDocument()
    expect(screen.getByText(/1 matched your target roles/i)).toBeInTheDocument()
    expect(
      screen.getByText(/2 filtered out by role or location/i),
    ).toBeInTheDocument()
  })

  it('shows a failed source with its stable error code', async () => {
    const user = userEvent.setup()
    setupFetch({
      status: {
        ...completedRun,
        status: 'failed',
        error_summary:
          'All configured sources failed; the accepted run remains consumed.',
        source_results: [
          {
            source_id: 'company_1',
            status: 'failed',
            records_found: 0,
            error_code: 'http_429',
            records_relevant: 0,
            records_filtered: 0,
            records_invalid: 0,
          },
        ],
      },
    })

    render(<SearchRunPanel />)

    await user.click(
      await screen.findByRole('button', { name: /start search/i }),
    )

    expect(await screen.findByText(/^failed$/i)).toBeInTheDocument()
    expect(screen.getByText(/http_429/)).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(/remains consumed/i)
  })

  it('stops polling once the run reaches a terminal state', async () => {
    const user = userEvent.setup()
    const fetchMock = setupFetch()

    render(<SearchRunPanel pollIntervalMs={5} />)

    await user.click(
      await screen.findByRole('button', { name: /start search/i }),
    )
    await screen.findByText(/^completed$/i)

    const callsAfterTerminal = statusCalls(fetchMock).length
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 40))
    })

    expect(statusCalls(fetchMock)).toHaveLength(callsAfterTerminal)
  })

  it('shows a visible error when eligibility cannot be loaded', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error('Backend offline')),
    )

    render(<SearchRunPanel />)

    expect(
      await screen.findByText(/could not load search eligibility/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/backend offline/i)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /retry eligibility/i }),
    ).toBeInTheDocument()
  })

  it('shows a run status network error and can retry polling', async () => {
    const user = userEvent.setup()
    let statusAttempt = 0
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/v1/search-policy') {
        return Promise.resolve(mockResponse(eligiblePolicy))
      }
      if (url === '/api/v1/search-runs') {
        return Promise.resolve(mockResponse(startResponse, 202))
      }
      if (url.startsWith('/api/v1/search-runs/')) {
        statusAttempt += 1
        if (statusAttempt === 1) {
          return Promise.reject(new Error('Status request failed'))
        }
        return Promise.resolve(mockResponse(completedRun))
      }
      return Promise.reject(new Error(`Unexpected fetch request: ${url}`))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<SearchRunPanel pollIntervalMs={5} />)

    await user.click(
      await screen.findByRole('button', { name: /start search/i }),
    )
    expect(
      await screen.findByText(/could not load run status/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/status request failed/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start search/i })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: /retry status/i }))

    expect(await screen.findByText(/^completed$/i)).toBeInTheDocument()
    expect(statusCalls(fetchMock)).toHaveLength(2)
  })
})
