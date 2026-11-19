import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  fetchLatestSearchRun,
  fetchSearchPolicy,
  fetchSearchRun,
  startSearchRun,
  type SearchPolicy,
  type SearchRunStatus,
} from './searchRuns'

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

const policy: SearchPolicy = {
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

const run: SearchRunStatus = {
  run_id: 'run_abc',
  status: 'running',
  accepted_at_utc: '2026-11-12T00:00:00Z',
  started_at_utc: '2026-11-12T00:00:01Z',
  finished_at_utc: null,
  operational_day: '2026-11-12',
  progress: {
    stage: 'running',
    completed_sources: 0,
    total_sources: 1,
  },
  source_results: [],
  error_summary: null,
}

describe('searchRuns api', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads and validates the search policy', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(policy))
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchSearchPolicy()

    expect(result).toEqual(policy)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/search-policy',
      expect.objectContaining({
        headers: { Accept: 'application/json' },
      }),
    )
  })

  it('starts a run with the idempotency key and an empty payload', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      mockResponse(
        {
          run_id: 'run_abc',
          status: 'accepted',
          accepted_at_utc: '2026-11-12T00:00:00Z',
          operational_day: '2026-11-12',
          remaining_runs: 1,
          status_url: '/api/v1/search-runs/run_abc',
        },
        202,
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await startSearchRun('idem-key')

    expect(result.run_id).toBe('run_abc')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/search-runs',
      expect.objectContaining({
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'Idempotency-Key': 'idem-key',
        },
        body: '{}',
      }),
    )
  })

  it('loads one run status', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(run))
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchSearchRun('run_abc')

    expect(result).toEqual(run)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/search-runs/run_abc',
      expect.objectContaining({
        headers: { Accept: 'application/json' },
      }),
    )
  })

  it('surfaces the backend error envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse(
          {
            error: {
              code: 'search_cooldown_active',
              message: 'A search run is not currently eligible.',
              details: { next_eligible_at: '2026-11-12T11:00:00Z' },
              request_id: 'req_test',
            },
          },
          429,
        ),
      ),
    )

    await expect(startSearchRun('idem-key')).rejects.toMatchObject({
      name: 'SearchRunsApiError',
      code: 'search_cooldown_active',
      message: 'A search run is not currently eligible.',
      details: { next_eligible_at: '2026-11-12T11:00:00Z' },
      status: 429,
    })
  })

  it('rejects a malformed policy payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(mockResponse({ can_start: 'yes' })),
    )

    await expect(fetchSearchPolicy()).rejects.toThrow(/unexpected payload/i)
  })

  it('rejects a malformed run payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(mockResponse({ run_id: 'run_abc' })),
    )

    await expect(fetchSearchRun('run_abc')).rejects.toThrow(/unexpected payload/i)
  })
})

describe('latest search run', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('returns null when no run has been accepted yet', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(null))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchLatestSearchRun()).resolves.toBeNull()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/search-runs/latest',
      expect.objectContaining({
        headers: { Accept: 'application/json' },
      }),
    )
  })

  it('returns the latest run when one exists', async () => {
    const run: SearchRunStatus = {
      run_id: 'run_latest',
      status: 'completed',
      accepted_at_utc: '2026-11-18T01:00:00Z',
      started_at_utc: '2026-11-18T01:00:05Z',
      finished_at_utc: '2026-11-18T01:04:00Z',
      operational_day: '2026-11-18',
      progress: { stage: 'completed', completed_sources: 2, total_sources: 2 },
      source_results: [],
      error_summary: null,
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(run)))

    await expect(fetchLatestSearchRun()).resolves.toEqual(run)
  })

  it('forwards an abort signal', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(null))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()

    await fetchLatestSearchRun(controller.signal)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/search-runs/latest',
      expect.objectContaining({ signal: controller.signal }),
    )
  })

  it('rejects a malformed latest run payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(mockResponse({ run_id: 'run_latest' })),
    )

    await expect(fetchLatestSearchRun()).rejects.toThrow(/unexpected payload/i)
  })

  it('surfaces a failed latest run request', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse(
          { error: { code: 'run_not_found', message: 'No run.', details: {} } },
          404,
        ),
      ),
    )

    await expect(fetchLatestSearchRun()).rejects.toMatchObject({
      code: 'run_not_found',
      status: 404,
    })
  })
})
