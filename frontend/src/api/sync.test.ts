import { afterEach, describe, expect, it, vi } from 'vitest'
import { SyncApiError, fetchSyncStatus, type SyncStatus } from './sync'

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

const status: SyncStatus = {
  counts: {
    opportunity: { pending: 2, synced: 10 },
    job_source: { pending: 1, blocked: 1 },
  },
  totals: {
    pending: 3,
    in_progress: 0,
    retry_wait: 0,
    blocked: 1,
    synced: 10,
    outstanding: 4,
  },
  due_now: 2,
  action_required: true,
  blocked: [
    {
      outbox_id: 'outbox_1',
      entity_type: 'job_source',
      entity_id: 'source_1',
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

describe('sync api', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads synchronization status', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(status))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchSyncStatus()).resolves.toEqual(status)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/sync/status',
      expect.objectContaining({
        headers: { Accept: 'application/json' },
      }),
    )
  })

  it('accepts an empty outbox', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse({
          ...status,
          counts: { opportunity: {}, job_source: {} },
          totals: { outstanding: 0 },
          due_now: 0,
          action_required: false,
          blocked: [],
          last_synced_at_utc: null,
        }),
      ),
    )

    const result = await fetchSyncStatus()

    expect(result.totals.outstanding).toBe(0)
    expect(result.last_synced_at_utc).toBeNull()
    expect(result.blocked).toEqual([])
  })

  it('forwards an abort signal', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(status))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()

    await fetchSyncStatus(controller.signal)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/sync/status',
      expect.objectContaining({ signal: controller.signal }),
    )
  })

  it('rejects a malformed payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(mockResponse({ ...status, due_now: 'two' })),
    )

    await expect(fetchSyncStatus()).rejects.toMatchObject({
      name: 'SyncApiError',
      code: 'unexpected_payload',
    })
  })

  it('rejects a blocked item with a missing field', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse({
          ...status,
          blocked: [{ outbox_id: 'outbox_1', entity_type: 'job_source' }],
        }),
      ),
    )

    await expect(fetchSyncStatus()).rejects.toBeInstanceOf(SyncApiError)
  })

  it('surfaces the API error envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse(
          {
            error: {
              code: 'sync_unavailable',
              message: 'The synchronization store is not ready.',
              details: {},
            },
          },
          503,
        ),
      ),
    )

    await expect(fetchSyncStatus()).rejects.toMatchObject({
      code: 'sync_unavailable',
      status: 503,
    })
  })
})
