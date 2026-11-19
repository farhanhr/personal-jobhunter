import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OpportunityDetailPage } from './OpportunityDetailPage'

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

const detail = {
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
  description: '<p>Build dashboards.</p><p>Work with the analytics team.</p>',
  job_type: 'Full time',
  date_applied: '2026-11-14',
  application_status: 'Applied',
  follow_up_date: null,
  interview_date: null,
  vacancy_url: null,
  notes: 'Referred by a former colleague.',
  sync_status: 'pending',
  known_fields: [
    'job_type',
    'date_applied',
    'application_status',
    'follow_up_date',
  ],
  sources: [
    {
      source_type: 'greenhouse',
      external_source_id: '1',
      source_url: 'https://boards.greenhouse.io/acme/jobs/1',
      source_url_is_safe: true,
      captured_at_utc: '2026-11-12T03:00:00Z',
      provenance_note: 'Captured from the public Greenhouse board.',
      run_ids: ['run_1'],
    },
    {
      source_type: 'linkedin_post',
      external_source_id: 'post_1',
      source_url: 'https://www.linkedin.com/posts/example',
      source_url_is_safe: false,
      captured_at_utc: '2026-11-13T03:00:00Z',
      provenance_note: null,
      run_ids: [],
    },
  ],
}

const trackerAfterSave = {
  opportunity_id: 'opp_1',
  job_type: 'Full time',
  date_applied: '2026-11-14',
  application_status: 'Interview',
  follow_up_date: null,
  interview_date: null,
  vacancy_url: null,
  notes: 'Referred by a former colleague.',
  sync_status: 'pending',
  known_fields: [
    'job_type',
    'date_applied',
    'application_status',
    'follow_up_date',
  ],
}

const rolesPayload = {
  target_roles: [
    {
      role_id: 'role_1',
      canonical_name: 'Data Analytics',
      aliases: ['Data Analyst'],
    },
  ],
  max_target_roles: 3,
}

type Handlers = {
  detail?: () => Promise<Response>
  patch?: (body: Record<string, unknown>) => Promise<Response>
  roles?: () => Promise<Response>
}

function mockApi(handlers: Handlers = {}) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'

    if (url === '/api/v1/settings/search' && method === 'GET') {
      return handlers.roles?.() ?? Promise.resolve(mockResponse(rolesPayload))
    }
    if (url.startsWith('/api/v1/jobs/') && method === 'GET') {
      return handlers.detail?.() ?? Promise.resolve(mockResponse(detail))
    }
    if (url.endsWith('/tracker') && method === 'PATCH') {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>
      return handlers.patch?.(body) ?? Promise.resolve(mockResponse(trackerAfterSave))
    }

    return Promise.reject(new Error(`Unexpected fetch request: ${url} ${method}`))
  })
}

function renderDetail(options: { from?: string } = {}) {
  return render(
    <MemoryRouter
      initialEntries={[
        {
          pathname: '/opportunities/opp_1',
          state: options.from === undefined ? null : { from: options.from },
        },
      ]}
    >
      <Routes>
        <Route
          path="/opportunities/:opportunityId"
          element={<OpportunityDetailPage />}
        />
      </Routes>
    </MemoryRouter>,
  )
}

describe('OpportunityDetailPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('renders the full description as readable text', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDetail()

    const description = await screen.findByText(/build dashboards/i)
    expect(description).toBeInTheDocument()
    expect(description).toHaveTextContent(/work with the analytics team/i)
    // Markup is never rendered as HTML.
    expect(description.innerHTML).not.toContain('<p>')
  })

  it('shows the discovery metadata and match reason', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDetail()

    expect(await screen.findByText('Jakarta Selatan, DKI Jakarta')).toBeInTheDocument()
    expect(screen.getByText('Title matches the target role.')).toBeInTheDocument()
    expect(screen.getByText('Onsite')).toBeInTheDocument()
    expect(screen.getByText('Not supplied by the source')).toBeInTheDocument()
  })

  it('resolves the configured target role name instead of its id', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDetail()

    const term = await screen.findByText('Target role')
    const row = term.closest('div')!
    expect(await within(row).findByText('Data Analytics')).toBeInTheDocument()
    expect(within(row).queryByText(/role_1/)).not.toBeInTheDocument()
  })

  it('keeps a human-readable fallback when the role was deleted', async () => {
    vi.stubGlobal(
      'fetch',
      mockApi({
        roles: () =>
          Promise.resolve(
            mockResponse({ target_roles: [], max_target_roles: 3 }),
          ),
      }),
    )

    renderDetail()

    expect(
      await screen.findByText('Matched to a configured target role'),
    ).toBeInTheDocument()
    expect(screen.queryByText(/role_1/)).not.toBeInTheDocument()
  })

  it('shows source provenance and links only safe URLs', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDetail()

    expect(await screen.findByText('greenhouse')).toBeInTheDocument()
    expect(screen.getByText('linkedin_post')).toBeInTheDocument()

    const safe = screen.getByRole('link', { name: /open the original posting/i })
    expect(safe).toHaveAttribute('href', 'https://boards.greenhouse.io/acme/jobs/1')

    // The unsafe source is named but is not an anchor.
    const unsafeRow = screen.getByText('linkedin_post').closest('li')!
    expect(within(unsafeRow).queryByRole('link')).not.toBeInTheDocument()
    expect(
      within(unsafeRow).getByText(/not stored as safe to open/i),
    ).toBeInTheDocument()
  })

  it('links back to the exact feed the user came from', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDetail({ from: '/opportunities?feed=review&page=2' })

    const back = await screen.findByRole('link', { name: /back to opportunities/i })
    expect(back).toHaveAttribute('href', '/opportunities?feed=review&page=2')
  })

  it('falls back to the feed root without a recorded origin', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDetail()

    const back = await screen.findByRole('link', { name: /back to opportunities/i })
    expect(back).toHaveAttribute('href', '/opportunities')
  })

  it('shows a not-found state for an unknown opportunity', async () => {
    vi.stubGlobal(
      'fetch',
      mockApi({
        detail: () =>
          Promise.resolve(
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
      }),
    )

    renderDetail()

    expect(
      await screen.findByRole('heading', { name: /opportunity not found/i }),
    ).toBeInTheDocument()
    // A missing record is not a retryable failure.
    expect(
      screen.queryByRole('button', { name: /^retry$/i }),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /back to opportunities/i })).toBeInTheDocument()
  })

  it('shows a retryable error when the detail request fails', async () => {
    let attempt = 0
    const fetchMock = mockApi({
      detail: () => {
        attempt += 1
        return attempt === 1
          ? Promise.resolve(
              mockResponse(
                {
                  error: {
                    code: 'read_model_error',
                    message: 'Read model failed.',
                  },
                },
                500,
              ),
            )
          : Promise.resolve(mockResponse(detail))
      },
    })
    vi.stubGlobal('fetch', fetchMock)

    renderDetail()

    expect(
      await screen.findByRole('heading', { name: /could not load this opportunity/i }),
    ).toBeInTheDocument()
    expect(screen.getByText('Read model failed.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /^retry$/i }))

    expect(
      await screen.findByRole('heading', { name: /data analyst/i }),
    ).toBeInTheDocument()
  })

  it('distinguishes a known blank from an unknown tracker field', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDetail()

    await screen.findByRole('heading', { name: /application tracker/i })

    // follow_up_date is known and blank.
    const followUp = screen.getByLabelText('Follow-up date')
    expect(followUp).toHaveValue('')
    expect(
      within(screen.getByText('Follow-up date').closest('.tracker-input')!).getByText(
        /not recorded in the spreadsheet/i,
      ),
    ).toBeInTheDocument()

    // vacancy_url is absent from known_fields.
    const vacancy = screen.getByLabelText('Vacancy URL')
    expect(vacancy).toHaveValue('')
    expect(
      within(screen.getByText('Vacancy URL').closest('.tracker-input')!).getByText(
        /unknown: no reliable local value/i,
      ),
    ).toBeInTheDocument()
  })

  it('populates existing values into the form', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDetail()

    expect(await screen.findByLabelText('Job type')).toHaveValue('Full time')
    expect(screen.getByLabelText('Application status')).toHaveValue('Applied')
    expect(screen.getByLabelText('Date applied')).toHaveValue('2026-11-14')
    expect(screen.getByLabelText('Notes')).toHaveValue(
      'Referred by a former colleague.',
    )
  })

  it('submits only the field the user changed', async () => {
    const patchBodies: Array<Record<string, unknown>> = []
    vi.stubGlobal(
      'fetch',
      mockApi({
        patch: (body) => {
          patchBodies.push(body)
          return Promise.resolve(mockResponse(trackerAfterSave))
        },
      }),
    )

    renderDetail()

    const status = await screen.findByLabelText('Application status')
    await userEvent.clear(status)
    await userEvent.type(status, 'Interview')
    await userEvent.click(screen.getByRole('button', { name: /save tracker/i }))

    expect(patchBodies).toHaveLength(1)
    expect(patchBodies[0]).toEqual({ application_status: 'Interview' })
  })

  it('submits an explicit clear for a known value the user empties', async () => {
    const patchBodies: Array<Record<string, unknown>> = []
    vi.stubGlobal(
      'fetch',
      mockApi({
        patch: (body) => {
          patchBodies.push(body)
          return Promise.resolve(
            mockResponse({ ...trackerAfterSave, job_type: null }),
          )
        },
      }),
    )

    renderDetail()

    await userEvent.clear(await screen.findByLabelText('Job type'))
    await userEvent.click(screen.getByRole('button', { name: /save tracker/i }))

    expect(patchBodies[0]).toEqual({ job_type: null })
  })

  it('never submits an untouched unknown field', async () => {
    const patchBodies: Array<Record<string, unknown>> = []
    vi.stubGlobal(
      'fetch',
      mockApi({
        patch: (body) => {
          patchBodies.push(body)
          return Promise.resolve(mockResponse(trackerAfterSave))
        },
      }),
    )

    renderDetail()

    const status = await screen.findByLabelText('Application status')
    await userEvent.clear(status)
    await userEvent.type(status, 'Interview')
    await userEvent.click(screen.getByRole('button', { name: /save tracker/i }))

    const sent = Object.keys(patchBodies[0])
    for (const field of ['vacancy_url', 'interview_date', 'notes']) {
      expect(sent).not.toContain(field)
    }
    expect(sent).toEqual(['application_status'])
  })

  it('disables Save when nothing has changed', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDetail()

    expect(
      await screen.findByRole('button', { name: /save tracker/i }),
    ).toBeDisabled()
    expect(screen.getByText(/no changes to save/i)).toBeInTheDocument()
  })

  it('merges the saved values and known fields after a successful save', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDetail()

    const status = await screen.findByLabelText('Application status')
    await userEvent.clear(status)
    await userEvent.type(status, 'Interview')
    await userEvent.click(screen.getByRole('button', { name: /save tracker/i }))

    // The editor remounts on the merged values.
    expect(await screen.findByLabelText('Application status')).toHaveValue('Interview')
    expect(
      screen.getByText(/changes saved locally/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/the spreadsheet has not been updated yet/i),
    ).toBeInTheDocument()
    // Focus is returned to the section, not dropped to the document.
    expect(
      screen.getByRole('heading', { name: /application tracker/i }),
    ).toHaveFocus()
  })

  it('reports pending sync without claiming the spreadsheet was updated', async () => {
    vi.stubGlobal('fetch', mockApi())

    renderDetail()

    await screen.findByRole('heading', { name: /application tracker/i })
    expect(screen.getByText('Saved locally · Waiting to sync')).toBeInTheDocument()
    expect(screen.queryByText(/^synchronized$/i)).not.toBeInTheDocument()
  })

  it.each([
    ['pending', 'Saved locally · Waiting to sync'],
    ['in_progress', 'Synchronization in progress'],
    ['retry_wait', 'Saved locally · Retry scheduled'],
    ['blocked', 'Saved locally · Sync needs attention'],
    ['synced', 'Synchronized'],
    ['not_queued', 'No pending synchronization'],
  ])('renders the %s sync state as "%s"', async (status, label) => {
    vi.stubGlobal(
      'fetch',
      mockApi({
        detail: () =>
          Promise.resolve(mockResponse({ ...detail, sync_status: status })),
      }),
    )

    renderDetail()

    const line = await screen.findByText(label)
    expect(line).toHaveAttribute('data-sync', status)
  })

  it('shows a blocked sync as queued, not lost', async () => {
    vi.stubGlobal(
      'fetch',
      mockApi({
        detail: () =>
          Promise.resolve(mockResponse({ ...detail, sync_status: 'blocked' })),
      }),
    )

    renderDetail()

    await screen.findByRole('heading', { name: /application tracker/i })
    expect(screen.getByText('Saved locally · Sync needs attention')).toBeInTheDocument()
    expect(
      screen.getByText(/your edit is still queued locally/i),
    ).toBeInTheDocument()
    // The state explains that the edit survives and what to do about it.
    expect(screen.getByText(/nothing has been discarded/i)).toBeInTheDocument()
    expect(screen.queryByText(/the edit was lost/i)).not.toBeInTheDocument()
  })

  it('keeps the draft when the save fails', async () => {
    vi.stubGlobal(
      'fetch',
      mockApi({
        patch: () =>
          Promise.resolve(
            mockResponse(
              {
                error: {
                  code: 'tracker_state_inconsistent',
                  message: 'This opportunity has no canonical row yet.',
                },
              },
              409,
            ),
          ),
      }),
    )

    renderDetail()

    const status = await screen.findByLabelText('Application status')
    await userEvent.clear(status)
    await userEvent.type(status, 'Interview')
    await userEvent.click(screen.getByRole('button', { name: /save tracker/i }))

    expect(
      await screen.findByText(/could not save the tracker/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/this opportunity has no canonical row yet/i),
    ).toBeInTheDocument()
    // The typed value is still there, ready to retry.
    expect(screen.getByLabelText('Application status')).toHaveValue('Interview')
  })

  it('prevents a duplicate submission while saving', async () => {
    let patchCalls = 0
    vi.stubGlobal(
      'fetch',
      mockApi({
        patch: () => {
          patchCalls += 1
          return new Promise<Response>(() => undefined)
        },
      }),
    )

    renderDetail()

    const status = await screen.findByLabelText('Application status')
    await userEvent.clear(status)
    await userEvent.type(status, 'Interview')

    const save = screen.getByRole('button', { name: /save tracker/i })
    await userEvent.click(save)
    await userEvent.click(save)

    expect(patchCalls).toBe(1)
    expect(
      screen.getByRole('button', { name: /saving/i }),
    ).toBeDisabled()
  })
})
