import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createCompanySource,
  fetchCompanySources,
  updateCompanySource,
  type CompanySource,
} from './companies'

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

const unreviewed: CompanySource = {
  company_id: 'company_1',
  company_name: 'Example Company',
  ats_type: 'greenhouse',
  board_token: 'example-board',
  enabled: true,
  policy_status: 'unreviewed',
  can_fetch: false,
}

describe('companies api', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads registered company sources', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      mockResponse({ companies: [unreviewed] }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchCompanySources()

    expect(result).toEqual([unreviewed])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/companies',
      expect.objectContaining({
        headers: { Accept: 'application/json' },
      }),
    )
  })

  it('registers a company with only a name and board token', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(unreviewed, 201))
    vi.stubGlobal('fetch', fetchMock)

    const result = await createCompanySource({
      company_name: 'Example Company',
      board_token: 'example-board',
    })

    expect(result.policy_status).toBe('unreviewed')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/companies',
      expect.objectContaining({
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          company_name: 'Example Company',
          board_token: 'example-board',
        }),
      }),
    )
  })

  it('updates the review state of a company source', async () => {
    const permitted = {
      ...unreviewed,
      policy_status: 'permitted',
      can_fetch: true,
    }
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(permitted))
    vi.stubGlobal('fetch', fetchMock)

    const result = await updateCompanySource('company_1', {
      policy_status: 'permitted',
    })

    expect(result.can_fetch).toBe(true)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/companies/company_1',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ policy_status: 'permitted' }),
      }),
    )
  })

  it('surfaces the backend validation error envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse(
          {
            error: {
              code: 'invalid_board_token',
              message: 'The Greenhouse board token must be a single path identifier.',
              details: { board_token: '../etc/passwd' },
              request_id: 'req_test',
            },
          },
          422,
        ),
      ),
    )

    await expect(
      createCompanySource({
        company_name: 'Example Company',
        board_token: '../etc/passwd',
      }),
    ).rejects.toMatchObject({
      name: 'CompaniesApiError',
      code: 'invalid_board_token',
      status: 422,
    })
  })

  it('rejects a malformed company payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(mockResponse({ companies: 'nope' })),
    )

    await expect(fetchCompanySources()).rejects.toThrow(/unexpected payload/i)
  })
})
