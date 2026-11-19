import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation, useNavigate } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  TRACKER_FIELDS,
  type Opportunity,
  type OpportunityDetail,
  type TrackerField,
  type TrackerState,
} from './api/opportunities'
import type { CompanySource } from './api/companies'
import type { SyncStatus } from './api/sync'
import type { TargetRole } from './api/targetRoles'
import { AppRoutes } from './App'

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

const HEALTH = {
  status: 'ok',
  service: 'personal-job-finder-api',
  version: '0.1.0',
}

const SEARCH_POLICY = {
  timezone: 'Asia/Jakarta',
  operational_day: '2026-11-18',
  max_runs: 2,
  accepted_runs: 0,
  remaining_runs: 2,
  last_accepted_at: null,
  cooldown_until: null,
  cooldown_remaining_seconds: 0,
  next_eligible_at: null,
  next_reset_at: '2026-11-18T23:00:00Z',
  has_active_run: false,
  can_start: true,
  block_reason: null,
  blocking_reasons: [],
}

const DASHBOARD_SUMMARY = {
  as_of_date_jkt: '2026-11-18',
  total_opportunities: 30,
  excluded_opportunities: 0,
  new_opportunities: 30,
  new_opportunities_window: { start: '2026-11-12', end: '2026-11-18' },
  applications_window: { start: '2026-11-12', end: '2026-11-18' },
  applications_last_7_days: 1,
  interviews_window: { start: '2026-11-18', end: '2026-11-25' },
  upcoming_interviews: 0,
  opportunities_by_target_role: [
    { role_id: 'role_1', label: 'Data Analytics', count: 30 },
  ],
  opportunities_by_source: [{ source_type: 'greenhouse', count: 30 }],
  opportunities_by_location: [
    { province: 'DKI Jakarta', city: 'Jakarta Selatan', count: 30 },
  ],
  latest_read_model_refresh_at_utc: '2026-11-18T02:00:00Z',
  data_quality: {
    invalid_date_value_count: 0,
    invalid_date_values: [],
    invalid_date_values_truncated: false,
  },
}

const PENDING_SYNC = {
  counts: { opportunity: { pending: 1 }, job_source: {} },
  totals: { outstanding: 1 },
  due_now: 1,
  action_required: false,
  blocked: [],
  blocked_truncated: false,
  last_synced_at_utc: '2026-11-17T03:00:00Z',
  checked_at_utc: '2026-11-18T03:00:00Z',
}

const SYNCED_SYNC = {
  ...PENDING_SYNC,
  counts: { opportunity: { synced: 1 }, job_source: {} },
  totals: { outstanding: 0 },
  due_now: 0,
  last_synced_at_utc: '2026-11-18T04:00:00Z',
  checked_at_utc: '2026-11-18T04:00:01Z',
}

const SAFE_SOURCE = {
  source_type: 'greenhouse',
  external_source_id: 'board-1',
  source_url: 'https://boards.greenhouse.io/acme/jobs/1',
  source_url_is_safe: true,
  captured_at_utc: '2026-11-12T03:00:00Z',
  provenance_note: 'Captured from the public Greenhouse job board API.',
  run_ids: ['run_1'],
}

const UNSAFE_SOURCE = {
  source_type: 'linkedin_post',
  external_source_id: 'post-1',
  source_url: 'https://www.linkedin.com/posts/example',
  source_url_is_safe: false,
  captured_at_utc: '2026-11-13T03:00:00Z',
  provenance_note: null,
  run_ids: [],
}

type ApiOptions = {
  roles?: TargetRole[]
  patchMode?: 'success' | 'error' | 'hold'
}

/**
 * A stateful stand-in for the backend, so the tests can follow one record
 * through feed, detail, PATCH, and a later re-fetch. It is a transport double:
 * no assertion here proves Google Sheets integration.
 */
function createApi(options: ApiOptions = {}) {
  const tracker = {
    values: {
      job_type: 'Full time',
      date_applied: '2026-11-14',
      application_status: 'Applied',
      follow_up_date: null,
      interview_date: null,
      vacancy_url: null,
      notes: 'Referred by a former colleague.',
    } as Record<TrackerField, string | null>,
    known: new Set<TrackerField>([
      'job_type',
      'date_applied',
      'application_status',
      'follow_up_date',
      'notes',
    ]),
    syncStatus: 'pending',
  }

  const state = {
    tracker,
    sync: { ...PENDING_SYNC } as SyncStatus,
    roles: options.roles ?? [
      {
        role_id: 'role_1',
        canonical_name: 'Data Analytics',
        aliases: ['Data Analyst'],
      },
    ],
    companies: [] as CompanySource[],
    patchBodies: [] as Array<Record<string, unknown>>,
    feedRequests: [] as string[],
    patchMode: options.patchMode ?? ('success' as 'success' | 'error' | 'hold'),
  }

  function trackerResponse(opportunityId: string): TrackerState {
    return {
      opportunity_id: opportunityId,
      ...tracker.values,
      sync_status: tracker.syncStatus,
      known_fields: [...tracker.known].sort(),
    }
  }

  function trackerSummary() {
    return {
      application_status: tracker.values.application_status,
      date_applied: tracker.values.date_applied,
      follow_up_date: tracker.values.follow_up_date,
      interview_date: tracker.values.interview_date,
      sync_status: tracker.syncStatus,
      known_fields: [...tracker.known].sort(),
    }
  }

  function opportunityFor(id: string): Opportunity {
    const isReview = id === 'opp_review_1'
    const index = Number(id.replace('opp_', ''))
    const title = isReview ? 'Backend Engineer' : `Data Analyst ${index}`

    return {
      opportunity_id: id,
      company_id: `company_${id}`,
      company_name: 'Nusantara Analytics',
      job_title: title,
      target_role_id: 'role_1',
      role_match_type: 'canonical_title',
      role_match_reason: 'Title matches the configured target role.',
      location_raw: 'Jakarta Selatan, DKI Jakarta',
      country: 'Indonesia',
      province: 'DKI Jakarta',
      city: 'Jakarta Selatan',
      location_status: 'eligible',
      work_mode: 'hybrid',
      remote_scope: 'not_applicable',
      posted_at_utc: '2026-11-13T02:30:00Z',
      first_seen_at_utc: '2026-11-12T01:00:00Z',
      last_seen_at_utc: '2026-11-18T01:00:00Z',
      availability_status: 'unknown',
      feed_bucket: isReview ? 'review' : 'main',
      description_excerpt: `Short excerpt for ${title}.`,
      has_more_description: true,
      tracker_summary: trackerSummary(),
      sources: [SAFE_SOURCE, UNSAFE_SOURCE],
    }
  }

  function detailFor(id: string): OpportunityDetail {
    const item = opportunityFor(id)

    return {
      opportunity_id: item.opportunity_id,
      company_id: item.company_id,
      company_name: item.company_name,
      job_title: item.job_title,
      target_role_id: item.target_role_id,
      role_match_type: item.role_match_type,
      role_match_reason: item.role_match_reason,
      location_raw: item.location_raw,
      country: item.country,
      province: item.province,
      city: item.city,
      location_status: item.location_status,
      location_reason: null,
      work_mode: item.work_mode,
      remote_scope: item.remote_scope,
      posted_at_utc: item.posted_at_utc,
      source_updated_at_utc: null,
      first_seen_at_utc: item.first_seen_at_utc,
      last_seen_at_utc: item.last_seen_at_utc,
      availability_status: item.availability_status,
      feed_bucket: item.feed_bucket,
      description: `<p>Full description for ${item.job_title}.</p><ul><li>Own reporting.</li><li>Build dashboards.</li></ul>`,
      job_type: tracker.values.job_type,
      date_applied: tracker.values.date_applied,
      application_status: tracker.values.application_status,
      follow_up_date: tracker.values.follow_up_date,
      interview_date: tracker.values.interview_date,
      vacancy_url: tracker.values.vacancy_url,
      notes: tracker.values.notes,
      sync_status: tracker.syncStatus,
      known_fields: [...tracker.known].sort(),
      sources: item.sources,
    }
  }

  const fetchMock = vi.fn(
    (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), 'http://127.0.0.1')
      const path = url.pathname
      const method = init?.method ?? 'GET'

      if (path === '/api/v1/health' && method === 'GET') {
        return Promise.resolve(mockResponse(HEALTH))
      }

      if (path === '/api/v1/settings/search' && method === 'GET') {
        return Promise.resolve(
          mockResponse({ target_roles: state.roles, max_target_roles: 3 }),
        )
      }

      if (path === '/api/v1/settings/search' && method === 'PUT') {
        const body = JSON.parse(String(init?.body)) as {
          target_roles: Array<{ canonical_name: string; aliases: string[] }>
        }
        state.roles = body.target_roles.map((role, index) => ({
          role_id: `role_saved_${index}`,
          canonical_name: role.canonical_name,
          aliases: role.aliases,
        }))
        return Promise.resolve(
          mockResponse({ target_roles: state.roles, max_target_roles: 3 }),
        )
      }

      if (path === '/api/v1/search-policy' && method === 'GET') {
        return Promise.resolve(mockResponse(SEARCH_POLICY))
      }

      if (path === '/api/v1/dashboard/summary' && method === 'GET') {
        return Promise.resolve(mockResponse(DASHBOARD_SUMMARY))
      }

      if (path === '/api/v1/search-runs/latest' && method === 'GET') {
        return Promise.resolve(mockResponse(null))
      }

      if (path === '/api/v1/sync/status' && method === 'GET') {
        return Promise.resolve(mockResponse(state.sync))
      }

      if (path === '/api/v1/companies' && method === 'GET') {
        return Promise.resolve(mockResponse({ companies: state.companies }))
      }

      if (path === '/api/v1/companies' && method === 'POST') {
        const body = JSON.parse(String(init?.body)) as {
          company_name: string
          board_token: string
        }
        const company: CompanySource = {
          company_id: 'company_new',
          company_name: body.company_name,
          ats_type: 'greenhouse',
          board_token: body.board_token,
          enabled: true,
          policy_status: 'unreviewed',
          can_fetch: false,
        }
        state.companies = [...state.companies, company]
        return Promise.resolve(mockResponse(company, 201))
      }

      if (path.startsWith('/api/v1/companies/') && method === 'PATCH') {
        const companyId = decodeURIComponent(path.split('/').pop() ?? '')
        const body = JSON.parse(String(init?.body)) as {
          policy_status?: 'permitted' | 'blocked'
        }
        const existing = state.companies.find(
          (company) => company.company_id === companyId,
        )
        if (existing === undefined) {
          return Promise.resolve(mockResponse({}, 404))
        }
        const updated: CompanySource = {
          ...existing,
          policy_status: body.policy_status ?? existing.policy_status,
          can_fetch: body.policy_status === 'permitted',
        }
        state.companies = state.companies.map((company) =>
          company.company_id === companyId ? updated : company,
        )
        return Promise.resolve(mockResponse(updated))
      }

      if (path === '/api/v1/jobs' && method === 'GET') {
        state.feedRequests.push(`${path}${url.search}`)
        const feed = url.searchParams.get('feed')
        if (feed === 'review') {
          return Promise.resolve(
            mockResponse({
              items: [opportunityFor('opp_review_1')],
              page: 1,
              page_size: 25,
              total_items: 1,
              total_pages: 1,
            }),
          )
        }

        const page = Number(url.searchParams.get('page') ?? '1')
        const all = Array.from({ length: 30 }, (_, index) =>
          opportunityFor(`opp_${index + 1}`),
        )
        const start = (page - 1) * 25
        return Promise.resolve(
          mockResponse({
            items: all.slice(start, start + 25),
            page,
            page_size: 25,
            total_items: 30,
            total_pages: 2,
          }),
        )
      }

      if (path.startsWith('/api/v1/jobs/') && method === 'GET') {
        const opportunityId = decodeURIComponent(path.split('/').pop() ?? '')
        if (opportunityId === 'missing') {
          return Promise.resolve(
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
          )
        }
        return Promise.resolve(mockResponse(detailFor(opportunityId)))
      }

      if (
        path.startsWith('/api/v1/opportunities/') &&
        path.endsWith('/tracker') &&
        method === 'PATCH'
      ) {
        const opportunityId = decodeURIComponent(path.split('/')[4] ?? '')
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>
        state.patchBodies.push(body)

        if (state.patchMode === 'error') {
          return Promise.resolve(
            mockResponse(
              {
                error: {
                  code: 'tracker_state_inconsistent',
                  message:
                    'This opportunity has no canonical row yet. Synchronize it before editing tracker fields.',
                  details: {},
                },
              },
              409,
            ),
          )
        }
        if (state.patchMode === 'hold') {
          return new Promise<Response>(() => undefined)
        }

        for (const field of TRACKER_FIELDS) {
          if (!(field in body)) {
            continue
          }
          const value = body[field]
          tracker.values[field] =
            value === null ? null : String(value)
          tracker.known.add(field)
        }
        tracker.syncStatus = 'pending'
        state.sync = { ...PENDING_SYNC }
        return Promise.resolve(mockResponse(trackerResponse(opportunityId)))
      }

      return Promise.reject(
        new Error(`Unexpected fetch request: ${path} ${method}`),
      )
    },
  )

  return { fetch: fetchMock, state }
}

function LocationProbe() {
  const location = useLocation()
  return (
    <output data-testid="location">{`${location.pathname}${location.search}`}</output>
  )
}

function HistoryControls() {
  const navigate = useNavigate()
  return (
    <div>
      <button type="button" onClick={() => navigate(-1)}>
        history back
      </button>
      <button type="button" onClick={() => navigate(1)}>
        history forward
      </button>
    </div>
  )
}

function renderApp(entry: string | { pathname: string; state?: unknown }) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <AppRoutes />
      <LocationProbe />
      <HistoryControls />
    </MemoryRouter>,
  )
}

function locationText(): string {
  return screen.getByTestId('location').textContent ?? ''
}

describe('frontend integration', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('Journey A — keeps the bucket and page across list, detail, and back', async () => {
    const api = createApi()
    vi.stubGlobal('fetch', api.fetch)

    renderApp('/')

    expect(
      await screen.findByRole('heading', { name: 'Dashboard' }),
    ).toBeInTheDocument()

    await userEvent.click(screen.getByRole('link', { name: 'Opportunities' }))
    expect(
      await screen.findByRole('heading', { name: 'Opportunities' }),
    ).toBeInTheDocument()
    expect(await screen.findByText('Data Analyst 1')).toBeInTheDocument()
    expect(locationText()).toBe('/opportunities')

    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(locationText()).toBe('/opportunities?feed=main&page=2')
    expect(await screen.findByText('Data Analyst 26')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('link', { name: 'Data Analyst 26' }))
    expect(
      await screen.findByRole('heading', { name: 'Data Analyst 26' }),
    ).toBeInTheDocument()
    expect(locationText()).toBe('/opportunities/opp_26')

    await userEvent.click(
      screen.getByRole('link', { name: /back to opportunities/i }),
    )
    expect(await screen.findByText('Data Analyst 26')).toBeInTheDocument()
    expect(locationText()).toBe('/opportunities?feed=main&page=2')

    await userEvent.click(screen.getByRole('tab', { name: 'Review queue' }))
    expect(await screen.findByText('Backend Engineer')).toBeInTheDocument()
    // A new bucket starts at page 1, so the old page is gone.
    expect(locationText()).toBe('/opportunities?feed=review')

    await userEvent.click(screen.getByRole('link', { name: 'Backend Engineer' }))
    expect(
      await screen.findByRole('heading', { name: 'Backend Engineer' }),
    ).toBeInTheDocument()
    await userEvent.click(
      screen.getByRole('link', { name: /back to opportunities/i }),
    )
    expect(await screen.findByText('Backend Engineer')).toBeInTheDocument()
    expect(locationText()).toBe('/opportunities?feed=review')
  })

  it('Journey A — browser back and forward restore the same context', async () => {
    const api = createApi()
    vi.stubGlobal('fetch', api.fetch)

    renderApp('/opportunities?feed=review')

    await userEvent.click(
      await screen.findByRole('link', { name: 'Backend Engineer' }),
    )
    expect(
      await screen.findByRole('heading', { name: 'Backend Engineer' }),
    ).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'history back' }))
    expect(
      await screen.findByRole('link', { name: 'Backend Engineer' }),
    ).toBeInTheDocument()
    expect(locationText()).toBe('/opportunities?feed=review')

    await userEvent.click(
      screen.getByRole('button', { name: 'history forward' }),
    )
    expect(
      await screen.findByRole('heading', { name: 'Backend Engineer' }),
    ).toBeInTheDocument()
    // The restored entry still knows where Back should go.
    expect(
      screen.getByRole('link', { name: /back to opportunities/i }),
    ).toHaveAttribute('href', '/opportunities?feed=review')
  })

  it('Journey B — one edited field is queued and survives a re-fetch', async () => {
    const api = createApi()
    vi.stubGlobal('fetch', api.fetch)

    renderApp({
      pathname: '/opportunities/opp_1',
      state: { from: '/opportunities?feed=main' },
    })

    const status = await screen.findByLabelText('Application status')
    expect(status).toHaveValue('Applied')

    await userEvent.clear(status)
    await userEvent.type(status, 'Interview')
    await userEvent.click(
      screen.getByRole('button', { name: /save tracker/i }),
    )

    expect(await screen.findByText(/changes saved locally/i)).toBeInTheDocument()
    expect(api.state.patchBodies).toEqual([
      { application_status: 'Interview' },
    ])
    expect(
      screen.getByText('Saved locally · Waiting to sync'),
    ).toBeInTheDocument()

    await userEvent.click(
      screen.getByRole('link', { name: /back to opportunities/i }),
    )
    const row = (await screen.findByRole('link', { name: 'Data Analyst 1' }))
      .closest('li')!
    expect(within(row).getByText('Interview')).toBeInTheDocument()

    await userEvent.click(
      within(row).getByRole('link', { name: 'Data Analyst 1' }),
    )
    expect(await screen.findByLabelText('Application status')).toHaveValue(
      'Interview',
    )
    expect(
      screen.getByText('Saved locally · Waiting to sync'),
    ).toBeInTheDocument()
  })

  it('Journey B — a previously unknown field becomes known only after it is filled', async () => {
    const api = createApi()
    vi.stubGlobal('fetch', api.fetch)

    renderApp({
      pathname: '/opportunities/opp_1',
      state: { from: '/opportunities?feed=main' },
    })

    const vacancy = await screen.findByLabelText('Vacancy URL')
    expect(vacancy).toHaveValue('')
    expect(
      within(vacancy.closest('.tracker-input')!).getByText(
        /unknown: no reliable local value/i,
      ),
    ).toBeInTheDocument()

    await userEvent.type(vacancy, 'https://example.test/job/42')
    await userEvent.click(
      screen.getByRole('button', { name: /save tracker/i }),
    )

    expect(await screen.findByText(/changes saved locally/i)).toBeInTheDocument()
    expect(api.state.patchBodies).toEqual([
      { vacancy_url: 'https://example.test/job/42' },
    ])

    const saved = screen.getByLabelText('Vacancy URL')
    expect(saved).toHaveValue('https://example.test/job/42')
    expect(
      within(saved.closest('.tracker-input')!).queryByText(
        /unknown: no reliable local value/i,
      ),
    ).not.toBeInTheDocument()
  })

  it('Journey B — clearing a known field sends null and shows a known blank', async () => {
    const api = createApi()
    vi.stubGlobal('fetch', api.fetch)

    renderApp({
      pathname: '/opportunities/opp_1',
      state: { from: '/opportunities?feed=main' },
    })

    await userEvent.clear(await screen.findByLabelText('Notes'))
    await userEvent.click(
      screen.getByRole('button', { name: /save tracker/i }),
    )

    expect(await screen.findByText(/changes saved locally/i)).toBeInTheDocument()
    expect(api.state.patchBodies).toEqual([{ notes: null }])

    const notes = screen.getByLabelText('Notes')
    expect(notes).toHaveValue('')
    expect(
      within(notes.closest('.tracker-input')!).getByText(
        /not recorded in the spreadsheet/i,
      ),
    ).toBeInTheDocument()
  })

  it('Journey B — a failed save keeps the draft and a retry sends it', async () => {
    const api = createApi({ patchMode: 'error' })
    vi.stubGlobal('fetch', api.fetch)

    renderApp({
      pathname: '/opportunities/opp_1',
      state: { from: '/opportunities?feed=main' },
    })

    const status = await screen.findByLabelText('Application status')
    await userEvent.clear(status)
    await userEvent.type(status, 'Interview')
    await userEvent.click(
      screen.getByRole('button', { name: /save tracker/i }),
    )

    expect(
      await screen.findByText(/could not save the tracker/i),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Application status')).toHaveValue('Interview')
    expect(api.state.patchBodies).toHaveLength(1)

    api.state.patchMode = 'success'
    await userEvent.click(
      screen.getByRole('button', { name: /save tracker/i }),
    )

    expect(await screen.findByText(/changes saved locally/i)).toBeInTheDocument()
    expect(api.state.patchBodies).toHaveLength(2)
    expect(api.state.patchBodies[1]).toEqual({
      application_status: 'Interview',
    })
  })

  it('Journey C — pending work is consistent, and only a confirmed sync clears it', async () => {
    const api = createApi()
    vi.stubGlobal('fetch', api.fetch)

    renderApp({
      pathname: '/opportunities/opp_1',
      state: { from: '/opportunities?feed=main' },
    })

    expect(
      await screen.findByText('Saved locally · Waiting to sync'),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/the edit is queued\. run a sync from the dashboard/i),
    ).toBeInTheDocument()

    await userEvent.click(screen.getByRole('link', { name: 'Dashboard' }))
    await screen.findByRole('heading', { name: 'Dashboard' })

    const outstanding = screen.getByText('Outstanding').closest('div')!
    expect(await within(outstanding).findByText('1')).toBeInTheDocument()
    expect(
      screen.getByText(/everything queued is waiting for a manual sync/i),
    ).toBeInTheDocument()

    // Simulate the backend confirming a later successful sync.
    api.state.sync = { ...SYNCED_SYNC }
    api.state.tracker.syncStatus = 'synced'
    api.state.tracker.known = new Set(TRACKER_FIELDS)

    await userEvent.click(screen.getByRole('link', { name: 'Opportunities' }))
    const row = (await screen.findByRole('link', { name: 'Data Analyst 1' }))
      .closest('li')!
    expect(within(row).getByText('Synced')).toBeInTheDocument()

    await userEvent.click(
      within(row).getByRole('link', { name: 'Data Analyst 1' }),
    )
    expect(await screen.findByText('Synchronized')).toBeInTheDocument()
    expect(
      screen.getByText(/the spreadsheet holds this record as shown/i),
    ).toBeInTheDocument()

    await userEvent.click(screen.getByRole('link', { name: 'Dashboard' }))
    await screen.findByRole('heading', { name: 'Dashboard' })
    expect(
      await screen.findByText(/nothing is waiting to sync/i),
    ).toBeInTheDocument()
  })

  it('Journey D — settings still create a role and an approved source', async () => {
    const api = createApi({ roles: [] })
    vi.stubGlobal('fetch', api.fetch)

    renderApp('/')

    expect(
      await screen.findByRole('heading', { name: 'Dashboard' }),
    ).toBeInTheDocument()
    expect(
      await screen.findByRole('heading', { name: /search allowance/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/searches left/i)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('link', { name: 'Settings' }))
    expect(
      await screen.findByRole('heading', { name: 'Settings' }),
    ).toBeInTheDocument()

    await userEvent.click(
      await screen.findByRole('button', { name: /add primary role/i }),
    )
    await userEvent.type(
      screen.getByLabelText(/primary role name/i),
      'Data Analyst',
    )
    await userEvent.click(
      screen.getByRole('button', { name: /save target roles/i }),
    )
    expect(await screen.findByText(/target roles saved/i)).toBeInTheDocument()

    await userEvent.type(
      screen.getByLabelText(/company name/i),
      'Acme Example',
    )
    await userEvent.type(screen.getByLabelText(/board token/i), 'acme-example')
    await userEvent.click(
      screen.getByRole('button', { name: /register greenhouse board/i }),
    )

    expect(
      await screen.findByRole('heading', { name: 'Acme Example' }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/^Unreviewed/, { selector: '.company-status' }),
    ).toBeInTheDocument()

    await userEvent.click(
      screen.getByRole('button', { name: /approve source/i }),
    )
    expect(
      await screen.findByText(/approved for search — included in the next search/i),
    ).toBeInTheDocument()

    await userEvent.click(screen.getByRole('link', { name: 'Dashboard' }))
    expect(
      await screen.findByRole('heading', { name: 'Dashboard' }),
    ).toBeInTheDocument()
  })

  it('shows a not-found detail state without leaking an internal id', async () => {
    const api = createApi()
    vi.stubGlobal('fetch', api.fetch)

    renderApp({
      pathname: '/opportunities/missing',
      state: { from: '/opportunities?feed=review' },
    })

    expect(
      await screen.findByRole('heading', { name: /opportunity not found/i }),
    ).toBeInTheDocument()
    expect(
      within(screen.getByRole('main')).queryByText(/missing/),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /back to opportunities/i }),
    ).toHaveAttribute('href', '/opportunities?feed=review')
  })
})
