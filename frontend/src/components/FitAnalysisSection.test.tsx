import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

describe('FitAnalysisSection', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows an analyze action when nothing is cached', () => {
    render(<FitAnalysisSection detail={detail()} />)

    expect(screen.getByText(/not analyzed yet/i)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Analyze Fit' }),
    ).toBeInTheDocument()
  })

  it('renders a cached analysis without calling the API', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    render(
      <FitAnalysisSection
        detail={detail({
          candidate_fit_score: 92,
          candidate_fit_summary: fitResult.summary,
          candidate_fit_evaluated_at: fitResult.evaluated_at_utc,
          is_best_fit: true,
        })}
      />,
    )

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

  it('analyzes on request and shows the result', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(fitResult))
    vi.stubGlobal('fetch', fetchMock)

    render(<FitAnalysisSection detail={detail()} />)
    await user.click(screen.getByRole('button', { name: 'Analyze Fit' }))

    expect(await screen.findByText('⭐ Best Fit (92%)')).toBeInTheDocument()
    expect(screen.getByText(/streaming is a gap/i)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Re-analyze Fit' }),
    ).toBeInTheDocument()

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/opportunities/opp_1/analyze-fit')
    expect(init.method).toBe('POST')
  })

  it('shows the actionable error when the master CV is missing', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
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
      ),
    )

    render(<FitAnalysisSection detail={detail()} />)
    await user.click(screen.getByRole('button', { name: 'Analyze Fit' }))

    expect(
      await screen.findByText(/upload your master cv in settings/i),
    ).toBeInTheDocument()
  })
})
