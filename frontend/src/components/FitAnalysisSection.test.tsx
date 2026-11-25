import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { OpportunityDetail } from '../api/opportunities'
import { FitAnalysisSection } from './FitAnalysisSection'

function detail(overrides: Partial<OpportunityDetail> = {}): OpportunityDetail {
  return {
    opportunity_id: 'opp_1',
    company_id: 'company_1',
    company_name: 'Nusantara Analytics',
    job_title: 'Backend Engineer',
    target_role_id: null,
    role_match_type: null,
    role_match_reason: null,
    location_raw: null,
    country: null,
    province: null,
    city: null,
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
    description: 'Build payment APIs.',
    job_type: null,
    date_applied: null,
    application_status: null,
    follow_up_date: null,
    interview_date: null,
    vacancy_url: null,
    notes: null,
    sync_status: 'pending',
    known_fields: [],
    candidate_fit_score: null,
    candidate_fit_summary: null,
    candidate_fit_evaluated_at: null,
    is_best_fit: false,
    sources: [],
    ...overrides,
  }
}

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

const aiConfigured = {
  provider: 'groq',
  base_url: 'https://api.groq.com/openai/v1',
  api_key_masked: '••••••••',
  model: 'llama-3.3-70b-versatile',
  is_configured: true,
  updated_at_utc: '2026-11-24T10:00:00.000000Z',
}

const aiUnconfigured = {
  provider: 'groq',
  base_url: '',
  api_key_masked: null,
  model: 'llama-3.3-70b-versatile',
  is_configured: false,
  updated_at_utc: null,
}

const cvUploaded = {
  has_profile: true,
  profile: {
    contact_info: {
      full_name: 'Synthetic Candidate',
      email: null,
      phone: null,
      location: null,
      linkedin_url: null,
      portfolio_url: null,
    },
    summary: '',
    skills: { hard_skills: [], tools: [], soft_skills: [] },
    experience: [],
    education: [],
    projects: [],
  },
  file_name: 'synthetic-cv.pdf',
  updated_at_utc: '2026-11-24T10:00:00.000000Z',
}

const cvMissing = {
  has_profile: false,
  profile: null,
  file_name: null,
  updated_at_utc: null,
}

const fitResult = {
  opportunity_id: 'opp_1',
  candidate_fit_score: 92,
  is_best_fit: true,
  summary: {
    candidate_fit_score: 92,
    fit_verdict: 'Strong Match',
    matching_skills: ['Python', 'PostgreSQL'],
    missing_skills: ['Kafka'],
    rationale: 'Core backend requirements are covered; streaming is a gap.',
  },
  evaluated_at_utc: '2026-11-24T11:00:00.000000Z',
}

type Handlers = {
  settings?: () => Response
  cv?: () => Response
  analyze?: () => Response
}

function mockApi(handlers: Handlers = {}) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/v1/ai/settings') {
      return Promise.resolve(handlers.settings?.() ?? mockResponse(aiConfigured))
    }
    if (url === '/api/v1/cv/profile') {
      return Promise.resolve(handlers.cv?.() ?? mockResponse(cvUploaded))
    }
    if (url.endsWith('/analyze-fit')) {
      return Promise.resolve(handlers.analyze?.() ?? mockResponse(fitResult))
    }
    return Promise.reject(new Error(`Unexpected request: ${url}`))
  })
}

function renderSection(overrides: Partial<OpportunityDetail> = {}) {
  return render(
    <MemoryRouter>
      <FitAnalysisSection detail={detail(overrides)} />
    </MemoryRouter>,
  )
}

describe('FitAnalysisSection', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows an analyze action when nothing is cached', () => {
    renderSection()

    expect(screen.getByText(/not analyzed yet/i)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Analyze Fit' }),
    ).toBeInTheDocument()
  })

  it('renders a cached analysis without calling the API', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    renderSection({
      candidate_fit_score: 92,
      candidate_fit_summary: fitResult.summary,
      candidate_fit_evaluated_at: fitResult.evaluated_at_utc,
      is_best_fit: true,
    })

    expect(screen.getByText('⭐ Best Fit (92%)')).toBeInTheDocument()
    expect(screen.getByText('Strong Match')).toBeInTheDocument()
    expect(screen.getByText('Python')).toBeInTheDocument()
    expect(screen.getByText('Kafka')).toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: /candidate fit 92 out of 100/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Re-analyze Fit' }),
    ).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('analyzes on request when AI and the Master CV are configured', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi()
    vi.stubGlobal('fetch', fetchMock)

    renderSection()
    await user.click(screen.getByRole('button', { name: 'Analyze Fit' }))

    expect(await screen.findByText('⭐ Best Fit (92%)')).toBeInTheDocument()
    expect(screen.getByText(/streaming is a gap/i)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Re-analyze Fit' }),
    ).toBeInTheDocument()

    const analyze = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith('/analyze-fit'),
    ) as [RequestInfo | URL, RequestInit] | undefined
    expect(String(analyze?.[0])).toBe('/api/v1/opportunities/opp_1/analyze-fit')
    expect(analyze?.[1].method).toBe('POST')
  })

  it('opens the unlock modal instead of calling the AI endpoint when unconfigured', async () => {
    const user = userEvent.setup()
    const fetchMock = mockApi({
      settings: () => mockResponse(aiUnconfigured),
      cv: () => mockResponse(cvMissing),
    })
    vi.stubGlobal('fetch', fetchMock)

    renderSection()
    await user.click(screen.getByRole('button', { name: 'Analyze Fit' }))

    const dialog = await screen.findByRole('dialog', {
      name: /unlock candidate fit & tailored ats cv/i,
    })
    expect(dialog).toBeInTheDocument()
    expect(screen.getByText('Not Configured')).toBeInTheDocument()
    expect(screen.getByText('Not Uploaded')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to Settings' })).toHaveAttribute(
      'href',
      '/settings',
    )
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).endsWith('/analyze-fit')),
    ).toBe(false)
  })

  it('opens the unlock modal from Tailor CV when the Master CV is missing', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      mockApi({
        settings: () => mockResponse(aiConfigured),
        cv: () => mockResponse(cvMissing),
      }),
    )

    renderSection()
    await user.click(screen.getByRole('button', { name: 'Tailor CV' }))

    expect(
      await screen.findByRole('dialog', {
        name: /unlock candidate fit & tailored ats cv/i,
      }),
    ).toBeInTheDocument()
    expect(screen.getByText('Configured')).toBeInTheDocument()
    expect(screen.getByText('Not Uploaded')).toBeInTheDocument()
  })

  it('shows the actionable error when the master CV is missing', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      mockApi({
        analyze: () =>
          mockResponse(
            {
              error: {
                code: 'candidate_profile_missing',
                message:
                  'Please upload your Master CV in Settings before analyzing fit.',
              },
            },
            400,
          ),
      }),
    )

    renderSection()
    await user.click(screen.getByRole('button', { name: 'Analyze Fit' }))

    expect(
      await screen.findByText(/upload your master cv in settings/i),
    ).toBeInTheDocument()
  })
})
