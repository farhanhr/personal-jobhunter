import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  DashboardApiError,
  fetchDashboardSummary,
  type DashboardSummary,
} from './dashboard'

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
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

describe('dashboard api', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads the dashboard summary', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(summary))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchDashboardSummary()).resolves.toEqual(summary)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/dashboard/summary',
      expect.objectContaining({
        headers: { Accept: 'application/json' },
      }),
    )
  })

  it('accepts a summary whose read model has never been refreshed', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          mockResponse({ ...summary, latest_read_model_refresh_at_utc: null }),
        ),
    )

    const result = await fetchDashboardSummary()

    expect(result.latest_read_model_refresh_at_utc).toBeNull()
  })

  it('forwards an abort signal', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(summary))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()

    await fetchDashboardSummary(controller.signal)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/dashboard/summary',
      expect.objectContaining({ signal: controller.signal }),
    )
  })

  it('rejects a malformed payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(mockResponse({ ...summary, total_opportunities: '42' })),
    )

    await expect(fetchDashboardSummary()).rejects.toMatchObject({
      name: 'DashboardApiError',
      code: 'unexpected_payload',
    })
  })

  it('rejects a summary with a malformed grouping', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse({
          ...summary,
          opportunities_by_source: [{ source_type: 'greenhouse' }],
        }),
      ),
    )

    await expect(fetchDashboardSummary()).rejects.toBeInstanceOf(DashboardApiError)
  })

  it('surfaces the API error envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse(
          {
            error: {
              code: 'read_model_unavailable',
              message: 'The local read model is not available.',
              details: { table: 'opportunities_read_model' },
            },
          },
          503,
        ),
      ),
    )

    await expect(fetchDashboardSummary()).rejects.toMatchObject({
      code: 'read_model_unavailable',
      message: 'The local read model is not available.',
      status: 503,
      details: { table: 'opportunities_read_model' },
    })
  })

  it('rejects a non-JSON error body without losing the status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => {
          throw new Error('not json')
        },
      } as unknown as Response),
    )

    await expect(fetchDashboardSummary()).rejects.toMatchObject({
      code: 'request_failed',
      status: 500,
    })
  })
})
