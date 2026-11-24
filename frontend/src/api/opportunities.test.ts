import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  OpportunitiesApiError,
  fetchOpportunityDetail,
  fetchOpportunityFeed,
  updateTracker,
  type OpportunityFeedPage,
} from './opportunities'

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

const trackerSummary = {
  application_status: 'Applied',
  date_applied: '2026-11-10',
  follow_up_date: null,
  interview_date: null,
  sync_status: 'pending',
  known_fields: ['application_status', 'date_applied'],
}

const opportunity = {
  opportunity_id: 'opp_1',
  company_id: 'company_1',
  company_name: 'Nusantara Analytics',
  job_title: 'Data Analyst',
  target_role_id: 'role_1',
  role_match_type: 'canonical_title',
  role_match_reason: 'Title matches the target role.',
  location_raw: 'Jakarta Selatan, DKI Jakarta',
  country: 'Indonesia',
  province: 'DKI Jakarta',
  city: 'Jakarta Selatan',
  location_status: 'eligible',
  work_mode: 'onsite',
  remote_scope: 'not_applicable',
  posted_at_utc: null,
  first_seen_at_utc: '2026-11-12T01:00:00Z',
  last_seen_at_utc: '2026-11-18T01:00:00Z',
  availability_status: 'unknown',
  feed_bucket: 'main',
  description_excerpt: 'Build dashboards for the analytics team.',
  has_more_description: true,
  candidate_fit_score: null,
  candidate_fit_summary: null,
  is_best_fit: false,
  tracker_summary: { ...trackerSummary },
  sources: [
    {
      source_type: 'greenhouse',
      external_source_id: '1',
      source_url: 'https://boards.greenhouse.io/acme/jobs/1',
      source_url_is_safe: true,
      captured_at_utc: '2026-11-12T03:00:00Z',
      provenance_note: 'Captured from the public board.',
      run_ids: ['run_1'],
    },
    {
      source_type: 'linkedin_post',
      external_source_id: 'post_1',
      source_url: 'https://linkedin.com/posts/unsafe',
      source_url_is_safe: false,
      captured_at_utc: '2026-11-13T03:00:00Z',
      provenance_note: null,
      run_ids: [],
    },
  ],
}

const feedPage: OpportunityFeedPage = {
  items: [opportunity],
  page: 1,
  page_size: 25,
  total_items: 1,
  total_pages: 1,
}

describe('opportunities api', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads the main feed', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(feedPage))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchOpportunityFeed({ feed: 'main' })).resolves.toEqual(feedPage)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/jobs?feed=main&page=1&page_size=25',
      expect.objectContaining({
        headers: { Accept: 'application/json' },
      }),
    )
  })

  it('requests the review queue with the backend feed parameter', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      mockResponse({
        ...feedPage,
        items: [{ ...opportunity, feed_bucket: 'review' }],
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await fetchOpportunityFeed({ feed: 'review', page: 2, pageSize: 10 })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/jobs?feed=review&page=2&page_size=10',
      expect.anything(),
    )
  })

  it('normalises a page below one', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(feedPage))
    vi.stubGlobal('fetch', fetchMock)

    await fetchOpportunityFeed({ feed: 'main', page: 0 })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/jobs?feed=main&page=1&page_size=25',
      expect.anything(),
    )
  })

  it('forwards an abort signal', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(feedPage))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()

    await fetchOpportunityFeed({ feed: 'main', signal: controller.signal })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/jobs?feed=main&page=1&page_size=25',
      expect.objectContaining({ signal: controller.signal }),
    )
  })

  it('rejects a malformed item', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          mockResponse({ ...feedPage, items: [{ ...opportunity, job_title: 42 }] }),
        ),
    )

    await expect(fetchOpportunityFeed({ feed: 'main' })).rejects.toMatchObject({
      name: 'OpportunitiesApiError',
      code: 'unexpected_payload',
    })
  })

  it('rejects a feed page without pagination metadata', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(mockResponse({ ...feedPage, total_pages: undefined })),
    )

    await expect(fetchOpportunityFeed({ feed: 'main' })).rejects.toBeInstanceOf(
      OpportunitiesApiError,
    )
  })

  it('rejects a malformed source summary', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse({
          ...feedPage,
          items: [
            {
              ...opportunity,
              sources: [{ ...opportunity.sources[0], source_url_is_safe: 'yes' }],
            },
          ],
        }),
      ),
    )

    await expect(fetchOpportunityFeed({ feed: 'main' })).rejects.toMatchObject({
      code: 'unexpected_payload',
    })
  })

  it('rejects a malformed tracker summary', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse({
          ...feedPage,
          items: [
            {
              ...opportunity,
              tracker_summary: { ...trackerSummary, known_fields: 'all' },
            },
          ],
        }),
      ),
    )

    await expect(fetchOpportunityFeed({ feed: 'main' })).rejects.toMatchObject({
      code: 'unexpected_payload',
    })
  })

  it('surfaces the API error envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse(
          {
            error: {
              code: 'invalid_feed',
              message: 'The feed is not available.',
              details: { feed: 'archived' },
            },
          },
          422,
        ),
      ),
    )

    await expect(fetchOpportunityFeed({ feed: 'main' })).rejects.toMatchObject({
      code: 'invalid_feed',
      message: 'The feed is not available.',
      status: 422,
      details: { feed: 'archived' },
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

    await expect(fetchOpportunityFeed({ feed: 'main' })).rejects.toMatchObject({
      code: 'request_failed',
      status: 500,
    })
  })
})

const detailPayload = {
  opportunity_id: 'opp_1',
  company_id: 'company_1',
  company_name: 'Nusantara Analytics',
  job_title: 'Data Analyst',
  target_role_id: 'role_1',
  role_match_type: 'canonical_title',
  role_match_reason: 'Title matches the target role.',
  location_raw: 'Jakarta Selatan, DKI Jakarta',
  country: 'Indonesia',
  province: 'DKI Jakarta',
  city: 'Jakarta Selatan',
  location_status: 'eligible',
  location_reason: null,
  work_mode: 'onsite',
  remote_scope: 'not_applicable',
  posted_at_utc: null,
  source_updated_at_utc: null,
  first_seen_at_utc: '2026-11-12T01:00:00Z',
  last_seen_at_utc: '2026-11-18T01:00:00Z',
  availability_status: 'unknown',
  feed_bucket: 'main',
  description: '<p>Build dashboards.</p>',
  job_type: 'Full time',
  date_applied: null,
  application_status: 'Applied',
  follow_up_date: null,
  interview_date: null,
  vacancy_url: null,
  notes: 'Referred by a former colleague.',
  sync_status: 'pending',
  known_fields: ['application_status', 'date_applied', 'job_type'],
  candidate_fit_score: null,
  candidate_fit_summary: null,
  candidate_fit_evaluated_at: null,
  is_best_fit: false,
  sources: [
    {
      source_type: 'greenhouse',
      external_source_id: '1',
      source_url: 'https://boards.greenhouse.io/acme/jobs/1',
      source_url_is_safe: true,
      captured_at_utc: '2026-11-12T03:00:00Z',
      provenance_note: 'Captured from the public board.',
      run_ids: ['run_1'],
    },
  ],
}

const trackerPayload = {
  opportunity_id: 'opp_1',
  job_type: 'Full time',
  date_applied: null,
  application_status: 'Interview',
  follow_up_date: null,
  interview_date: null,
  vacancy_url: null,
  notes: null,
  sync_status: 'pending',
  known_fields: ['application_status', 'date_applied', 'job_type'],
}

describe('opportunity detail api', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads one opportunity with its full description', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(detailPayload))
    vi.stubGlobal('fetch', fetchMock)

    const detail = await fetchOpportunityDetail('opp_1')

    expect(detail.description).toBe('<p>Build dashboards.</p>')
    expect(detail.known_fields).toEqual([
      'application_status',
      'date_applied',
      'job_type',
    ])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/jobs/opp_1',
      expect.objectContaining({ headers: { Accept: 'application/json' } }),
    )
  })

  it('encodes the opportunity id in the path', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(detailPayload))
    vi.stubGlobal('fetch', fetchMock)

    await fetchOpportunityDetail('opp/a b')

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/jobs/opp%2Fa%20b',
      expect.anything(),
    )
  })

  it('forwards an abort signal', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(detailPayload))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()

    await fetchOpportunityDetail('opp_1', controller.signal)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/jobs/opp_1',
      expect.objectContaining({ signal: controller.signal }),
    )
  })

  it('reports a missing opportunity as a typed 404', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse(
          {
            error: {
              code: 'opportunity_not_found',
              message: 'The opportunity does not exist in local data.',
              details: {},
            },
          },
          404,
        ),
      ),
    )

    await expect(fetchOpportunityDetail('opp_missing')).rejects.toMatchObject({
      name: 'OpportunitiesApiError',
      code: 'opportunity_not_found',
      status: 404,
    })
  })

  it('rejects a malformed detail payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse({ ...detailPayload, known_fields: 'all' }),
      ),
    )

    await expect(fetchOpportunityDetail('opp_1')).rejects.toMatchObject({
      code: 'unexpected_payload',
    })
  })

  it('rejects a detail payload without the tracker fields', async () => {
    const { application_status: _omitted, ...withoutTracker } = detailPayload
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(mockResponse(withoutTracker)),
    )

    await expect(fetchOpportunityDetail('opp_1')).rejects.toBeInstanceOf(
      OpportunitiesApiError,
    )
  })
})

describe('tracker update api', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('sends only the supplied fields', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(trackerPayload))
    vi.stubGlobal('fetch', fetchMock)

    await updateTracker('opp_1', { application_status: 'Interview' })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/opportunities/opp_1/tracker',
      expect.objectContaining({
        method: 'PATCH',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ application_status: 'Interview' }),
      }),
    )
  })

  it('sends an explicit null for a cleared field', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(trackerPayload))
    vi.stubGlobal('fetch', fetchMock)

    await updateTracker('opp_1', { notes: null })

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string)
    expect(body).toEqual({ notes: null })
    expect(Object.keys(body)).toHaveLength(1)
  })

  it('returns the merged tracker state', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(trackerPayload)))

    const state = await updateTracker('opp_1', { application_status: 'Interview' })

    expect(state.application_status).toBe('Interview')
    expect(state.sync_status).toBe('pending')
    expect(state.known_fields).toContain('application_status')
  })

  it('surfaces a tracker state conflict', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse(
          {
            error: {
              code: 'tracker_state_inconsistent',
              message: 'This opportunity has no canonical row yet.',
              details: {},
            },
          },
          409,
        ),
      ),
    )

    await expect(
      updateTracker('opp_1', { job_type: 'Internship' }),
    ).rejects.toMatchObject({
      code: 'tracker_state_inconsistent',
      status: 409,
    })
  })

  it('surfaces a validation error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse(
          {
            error: {
              code: 'validation_error',
              message: 'Notes must be 10000 characters or fewer.',
              details: { field: 'notes' },
            },
          },
          422,
        ),
      ),
    )

    await expect(
      updateTracker('opp_1', { notes: 'x' }),
    ).rejects.toMatchObject({
      code: 'validation_error',
      status: 422,
      details: { field: 'notes' },
    })
  })

  it('propagates a network failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new TypeError('Failed to fetch')),
    )

    await expect(
      updateTracker('opp_1', { job_type: 'Internship' }),
    ).rejects.toThrow(/failed to fetch/i)
  })

  it('rejects a malformed tracker response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse({ ...trackerPayload, sync_status: 42 }),
      ),
    )

    await expect(
      updateTracker('opp_1', { job_type: 'Internship' }),
    ).rejects.toMatchObject({ code: 'unexpected_payload' })
  })
})
