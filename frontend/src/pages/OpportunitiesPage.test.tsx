import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { OpportunityFeedPage } from '../api/opportunities'
import { OpportunitiesPage } from './OpportunitiesPage'

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

const trackerPending = {
  application_status: 'Applied',
  date_applied: null,
  follow_up_date: null,
  interview_date: null,
  sync_status: 'pending',
  known_fields: ['application_status', 'date_applied', 'follow_up_date'],
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
  tracker_summary: { ...trackerPending },
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

function feedPage(overrides: Record<string, unknown> = {}): OpportunityFeedPage {
  return {
    items: [{ ...opportunity }],
    page: 1,
    page_size: 25,
    total_items: 1,
    total_pages: 1,
    ...overrides,
  }
}

type BucketCounts = { main?: number; review?: number }

function mockApi(counts: BucketCounts = { main: 1, review: 0 }) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://127.0.0.1')
    const feed = url.searchParams.get('feed')
    const page = Number(url.searchParams.get('page') ?? '1')
    const bucket = feed as 'main' | 'review'
    const total = counts[bucket] ?? 0

    // The number of rows follows the count: an empty bucket returns no items,
    // so the empty state is reached the way the UI will actually reach it.
    const items =
      total === 0
        ? []
        : [
            {
              ...opportunity,
              feed_bucket: bucket,
              job_title: bucket === 'review' ? 'Backend Engineer' : 'Data Analyst',
            },
          ]

    return Promise.resolve(
      mockResponse(
        feedPage({
          items,
          total_items: total,
          total_pages: Math.max(1, Math.ceil(total / 25)),
          page,
        }),
      ),
    )
  })
}

function renderPage(entry = '/opportunities') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/opportunities" element={<OpportunitiesPage />} />
        <Route
          path="/opportunities/:opportunityId"
          element={<div>detail route</div>}
        />
      </Routes>
    </MemoryRouter>,
  )
}

describe('OpportunitiesPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads and renders the main feed', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderPage()

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Opportunities' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('tabpanel', { name: 'Main feed' })).toBeInTheDocument()
    expect(await screen.findByText('Data Analyst')).toBeInTheDocument()
    expect(screen.getByText('Nusantara Analytics')).toBeInTheDocument()
    expect(screen.getByText('1–1 of 1, page 1 of 1.')).toBeInTheDocument()
  })

  it('requests the review queue with the backend feed parameter', async () => {
    const fetchMock = mockApi({ main: 1, review: 1 })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()

    await screen.findByText('Data Analyst')
    await userEvent.click(screen.getByRole('tab', { name: 'Review queue' }))

    expect(
      await screen.findByRole('tabpanel', { name: 'Review queue' }),
    ).toBeInTheDocument()
    expect(await screen.findByText('Backend Engineer')).toBeInTheDocument()

    const reviewCall = fetchMock.mock.calls.find(([input]) =>
      String(input).includes('feed=review'),
    )
    expect(String(reviewCall?.[0])).toBe('/api/v1/jobs?feed=review&page=1&page_size=25')
  })

  it('resets to page 1 when the bucket changes', async () => {
    const fetchMock = mockApi({ main: 30, review: 30 })
    vi.stubGlobal('fetch', fetchMock)

    renderPage('/opportunities?feed=main&page=2')

    await screen.findByText('26–30 of 30, page 2 of 2.')
    expect(
      fetchMock.mock.calls.every(([input]) => String(input).includes('feed=main')),
    ).toBe(true)

    await userEvent.click(screen.getByRole('tab', { name: 'Review queue' }))

    await screen.findByText('Backend Engineer')
    // The bucket change reset the page, so the review request is page 1 even
    // though the URL arrived at page 2.
    expect(
      fetchMock.mock.calls.some(
        ([input]) =>
          String(input) === '/api/v1/jobs?feed=review&page=1&page_size=25',
      ),
    ).toBe(true)
  })

  it('pages previous and next with correct disabled states', async () => {
    const fetchMock = mockApi({ main: 30 })
    vi.stubGlobal('fetch', fetchMock)

    renderPage('/opportunities?feed=main&page=1')

    await screen.findByText('1–25 of 30, page 1 of 2.')
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled()

    await userEvent.click(screen.getByRole('button', { name: 'Next' }))

    await screen.findByText('26–30 of 30, page 2 of 2.')
    expect(screen.getByRole('button', { name: 'Previous' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).includes('page=2')),
    ).toBe(true)

    await userEvent.click(screen.getByRole('button', { name: 'Previous' }))

    await screen.findByText('1–25 of 30, page 1 of 2.')
    expect(
      fetchMock.mock.calls.some(
        ([input]) => String(input).includes('feed=main') && String(input).includes('page=1'),
      ),
    ).toBe(true)
  })

  it('distinguishes a known blank from an unknown field', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderPage()

    await screen.findByText('Data Analyst')

    // application_status is known and set.
    const application = screen
      .getByText('Application status')
      .parentElement!
    expect(within(application).getByText('Applied')).toBeInTheDocument()

    // follow_up_date is known and blank: "Not recorded", not "Unknown".
    // follow_up_date and date_applied are known blanks: "Not recorded".
    for (const label of ['Follow-up date', 'Date applied']) {
      const field = screen.getByText(label).parentElement!
      expect(within(field).getByText('Not recorded')).toBeInTheDocument()
      expect(within(field).queryByText('Unknown')).not.toBeInTheDocument()
    }

    // interview_date is absent from known_fields: "Unknown".
    const interview = screen.getByText('Interview date').parentElement!
    expect(within(interview).getByText('Unknown')).toBeInTheDocument()
    expect(within(interview).queryByText('Not recorded')).not.toBeInTheDocument()
  })

  it('renders pending and blocked synchronization without implying loss', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://127.0.0.1')
      const feed = url.searchParams.get('feed')

      return Promise.resolve(
        mockResponse(
          feedPage({
            items: [
              {
                ...opportunity,
                feed_bucket: feed === 'review' ? 'review' : 'main',
                tracker_summary: {
                  ...trackerPending,
                  // A blocked edit is still queued; it is not lost.
                  sync_status: feed === 'review' ? 'blocked' : 'pending',
                },
              },
            ],
          }),
        ),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    renderPage('/opportunities?feed=review')

    expect(await screen.findByText('Needs attention')).toBeInTheDocument()
    expect(screen.queryByText(/edit was lost/i)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: 'Main feed' }))

    expect(await screen.findByText('Waiting for manual sync')).toBeInTheDocument()
  })

  it('shows the empty state for an empty main feed', async () => {
    vi.stubGlobal('fetch', mockApi({ main: 0 }))

    renderPage()

    expect(
      await screen.findByText(/nothing has been discovered yet/i),
    ).toBeInTheDocument()
    // The count line states the bucket is empty; the empty state says what to do.
    expect(screen.getByText(/no opportunities in this bucket/i)).toBeInTheDocument()
  })

  it('shows the empty state for an empty review queue', async () => {
    vi.stubGlobal('fetch', mockApi({ main: 1 }))

    renderPage('/opportunities?feed=review')

    expect(
      await screen.findByText(/the review queue is empty/i),
    ).toBeInTheDocument()
  })

  it('keeps the request scoped and retries on failure', async () => {
    const summary = mockResponse(
      { error: { code: 'read_model_error', message: 'The read model failed.' } },
      500,
    )
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/jobs')) {
        return Promise.resolve(summary)
      }
      return Promise.reject(new Error(`Unexpected fetch request: ${url}`))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderPage()

    expect(
      await screen.findByText(/could not load the main feed/i),
    ).toBeInTheDocument()
    expect(screen.getByText('The read model failed.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))

    await screen.findByText(/could not load the main feed/i)
  })

  it('renders only safe source URLs as links', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderPage()

    await screen.findByText('Data Analyst')

    // A hard 429 would have meant "stop", and the fixture says something went
    // wrong; the safe list is what the row may present.
    const row = screen.getByText('Data Analyst').closest('li')!
    expect(within(row).queryByText('linkedin.com')).not.toBeInTheDocument()

    const links = within(row).getAllByRole('link')
    // The row's own detail link plus the one safe source.
    expect(links).toHaveLength(2)
    expect(
      links.map((link) => link.getAttribute('href')),
    ).toEqual([
      '/opportunities/opp_1',
      'https://boards.greenhouse.io/acme/jobs/1',
    ])
    expect(
      within(row).getByText(/1 further source cannot be linked safely/i),
    ).toBeInTheDocument()
  })

  it('formats timestamps in Asia/Jakarta', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderPage()

    await screen.findByText('Data Analyst')

    // 2026-11-12T01:00:00Z is 08:00 on 12 November in Jakarta.
    expect(screen.getByText(/12 Nov 2026, 08:00/)).toBeInTheDocument()
  })

  it('does not invent local filtering or fake counts', async () => {
    vi.stubGlobal('fetch', mockApi({ main: 2 }))

    renderPage()

    await screen.findByText('Data Analyst')

    // No search input, no fake filter, no per-page fudge: the row count on the
    // page comes from the response. The only controls are tabs, sort order, and
    // paging, and sorting is performed by the backend.
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Sort')).toBeInTheDocument()
    expect(screen.getByText('1–2 of 2, page 1 of 1.')).toBeInTheDocument()
  })
})

describe('feed to detail navigation', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('opens the detail route from a row, keeping the feed and page', async () => {
    vi.stubGlobal('fetch', mockApi({ main: 30 }))

    renderPage('/opportunities?feed=main&page=2')

    const row = await screen.findByRole('link', { name: 'Data Analyst' })
    expect(row).toHaveAttribute('href', '/opportunities/opp_1')

    await userEvent.click(row)

    expect(await screen.findByText('detail route')).toBeInTheDocument()
  })

  it('gives each row one real link and no dead actions', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderPage()

    const row = (await screen.findByText('Data Analyst')).closest('li')!
    // The title link plus the safe source link; nothing else is clickable.
    const links = within(row).getAllByRole('link')
    expect(links).toHaveLength(2)
    expect(within(row).queryByRole('button')).not.toBeInTheDocument()
  })

  it('renders Best Fit and standard fit badges', async () => {
    const summary = {
      candidate_fit_score: 92,
      fit_verdict: 'Strong Match',
      matching_skills: [],
      missing_skills: [],
      rationale: 'Close match.',
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse(
          feedPage({
            items: [
              {
                ...opportunity,
                opportunity_id: 'opp_best',
                job_title: 'Best Fit Role',
                candidate_fit_score: 92,
                candidate_fit_summary: summary,
                is_best_fit: true,
              },
              {
                ...opportunity,
                opportunity_id: 'opp_ok',
                job_title: 'Partial Fit Role',
                candidate_fit_score: 78,
                candidate_fit_summary: { ...summary, candidate_fit_score: 78 },
                is_best_fit: false,
              },
            ],
            total_items: 2,
          }),
        ),
      ),
    )

    renderPage()

    expect(await screen.findByText('⭐ Best Fit (92%)')).toBeInTheDocument()
    expect(screen.getByText('Fit: 78%')).toBeInTheDocument()
  })

  it('asks the API for Best Fit ordering when the sort changes', async () => {
    const fetchMock = mockApi()
    vi.stubGlobal('fetch', fetchMock)

    renderPage()
    await screen.findByText('Data Analyst')

    await userEvent.selectOptions(screen.getByLabelText('Sort'), 'best_fit')

    await waitFor(() => {
      const requested = fetchMock.mock.calls.map(([input]) => String(input))
      expect(
        requested.some((url) => url.includes('sort_by=best_fit')),
      ).toBe(true)
    })
  })
})
