import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CompanySources } from './CompanySources'

const unreviewed = {
  company_id: 'company_1',
  company_name: 'Example Company',
  ats_type: 'greenhouse',
  board_token: 'example-board',
  enabled: true,
  policy_status: 'unreviewed',
  can_fetch: false,
}

const permitted = {
  ...unreviewed,
  policy_status: 'permitted',
  can_fetch: true,
}

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

function setupFetch({
  companies = [unreviewed],
  create,
  patch,
}: {
  companies?: unknown[]
  create?: () => Promise<Response>
  patch?: () => Promise<Response>
} = {}) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'

    if (url === '/api/v1/companies' && method === 'GET') {
      return Promise.resolve(mockResponse({ companies }))
    }
    if (url === '/api/v1/companies' && method === 'POST') {
      return create ? create() : Promise.resolve(mockResponse(unreviewed, 201))
    }
    if (url.startsWith('/api/v1/companies/') && method === 'PATCH') {
      return patch ? patch() : Promise.resolve(mockResponse(permitted))
    }

    return Promise.reject(new Error(`Unexpected fetch request: ${url} ${method}`))
  })

  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function createCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(
    ([input, init]) =>
      String(input) === '/api/v1/companies' &&
      (init as RequestInit | undefined)?.method === 'POST',
  )
}

describe('CompanySources', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows registered sources with their review status', async () => {
    setupFetch()

    render(<CompanySources />)

    expect(await screen.findByText('Example Company')).toBeInTheDocument()
    expect(
      screen.getByText(/^Unreviewed/, { selector: '.company-status' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/will not be fetched/i)).toBeInTheDocument()
  })

  it('registers a company with a name and board token', async () => {
    const user = userEvent.setup()
    const fetchMock = setupFetch()

    render(<CompanySources />)

    await screen.findByText('Example Company')
    await user.type(screen.getByLabelText(/company name/i), 'Acme Example')
    await user.type(screen.getByLabelText(/board token/i), 'acme-example')
    await user.click(
      screen.getByRole('button', { name: /register greenhouse board/i }),
    )

    await waitFor(() => expect(createCalls(fetchMock)).toHaveLength(1))
    const body = JSON.parse(
      String((createCalls(fetchMock)[0][1] as RequestInit).body),
    )
    expect(body).toEqual({
      company_name: 'Acme Example',
      board_token: 'acme-example',
    })
  })

  it('requires both fields before submitting', async () => {
    const user = userEvent.setup()
    const fetchMock = setupFetch()

    render(<CompanySources />)

    await screen.findByText('Example Company')
    await user.click(
      screen.getByRole('button', { name: /register greenhouse board/i }),
    )

    expect(createCalls(fetchMock)).toHaveLength(0)
    expect(
      screen.getByText(/company name and board token are required/i),
    ).toBeInTheDocument()
  })

  it('explains a board token validation error from the backend', async () => {
    const user = userEvent.setup()
    setupFetch({
      create: () =>
        Promise.resolve(
          mockResponse(
            {
              error: {
                code: 'invalid_board_token',
                message:
                  'The Greenhouse board token must be a single path identifier.',
                details: {},
                request_id: 'req_1',
              },
            },
            422,
          ),
        ),
    })

    render(<CompanySources />)

    await screen.findByText('Example Company')
    await user.type(screen.getByLabelText(/company name/i), 'Acme Example')
    await user.type(screen.getByLabelText(/board token/i), '../etc/passwd')
    await user.click(
      screen.getByRole('button', { name: /register greenhouse board/i }),
    )

    expect(
      await screen.findByText(/must be a single path identifier/i),
    ).toBeInTheDocument()
  })

  it('approves a source explicitly and reflects the new status', async () => {
    const user = userEvent.setup()
    const fetchMock = setupFetch()

    render(<CompanySources />)

    await screen.findByText('Example Company')
    await user.click(screen.getByRole('button', { name: /approve source/i }))

    expect(await screen.findByText(/approved for search/i)).toBeInTheDocument()
    const patchCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).startsWith('/api/v1/companies/') &&
        (init as RequestInit | undefined)?.method === 'PATCH',
    )
    if (!patchCall) {
      throw new Error('Expected a PATCH request')
    }
    expect(JSON.parse(String((patchCall[1] as RequestInit).body))).toEqual({
      policy_status: 'permitted',
    })
  })

  it('blocks a source and explains it will not be fetched', async () => {
    const user = userEvent.setup()
    setupFetch({
      companies: [permitted],
      patch: () =>
        Promise.resolve(
          mockResponse({ ...unreviewed, policy_status: 'blocked' }),
        ),
    })

    render(<CompanySources />)

    await screen.findByText('Example Company')
    await user.click(screen.getByRole('button', { name: /block source/i }))

    expect(await screen.findByText(/blocked from search/i)).toBeInTheDocument()
  })

  it('shows a visible error when the list cannot be loaded', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error('Backend offline')),
    )

    render(<CompanySources />)

    expect(
      await screen.findByText(/could not load company sources/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/backend offline/i)).toBeInTheDocument()
  })

  it('does not offer approval for an already permitted source', async () => {
    setupFetch({ companies: [permitted] })

    render(<CompanySources />)

    await screen.findByText('Example Company')
    expect(
      screen.queryByRole('button', { name: /approve source/i }),
    ).not.toBeInTheDocument()
    expect(screen.getByText(/approved for search/i)).toBeInTheDocument()
    expect(screen.getByText(/included in the next search/i)).toBeInTheDocument()
  })

  it('warns when a permitted source is disabled and will not be fetched', async () => {
    setupFetch({
      companies: [{ ...permitted, enabled: false, can_fetch: false }],
    })

    render(<CompanySources />)

    await screen.findByText('Example Company')
    expect(screen.getByText(/will not be fetched/i)).toBeInTheDocument()
  })

  it('trims surrounding whitespace before submitting', async () => {
    const user = userEvent.setup()
    const fetchMock = setupFetch()

    render(<CompanySources />)

    await screen.findByText('Example Company')
    await user.type(screen.getByLabelText(/company name/i), '  Acme Example  ')
    await user.type(screen.getByLabelText(/board token/i), ' acme-example ')
    await user.click(
      screen.getByRole('button', { name: /register greenhouse board/i }),
    )

    await waitFor(() => expect(createCalls(fetchMock)).toHaveLength(1))
    const body = JSON.parse(
      String((createCalls(fetchMock)[0][1] as RequestInit).body),
    )
    expect(body).toEqual({
      company_name: 'Acme Example',
      board_token: 'acme-example',
    })
  })
})
