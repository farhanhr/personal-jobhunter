import { describe, expect, it } from 'vitest'
import type { OpportunityDetail } from '../api/opportunities'
import { draftFromDetail, trackerUpdate } from './trackerDraft'

function detail(overrides: Partial<OpportunityDetail> = {}): OpportunityDetail {
  return {
    opportunity_id: 'opp_1',
    company_id: 'company_1',
    company_name: 'Example',
    job_title: 'Data Analyst',
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
    description: null,
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

describe('trackerUpdate', () => {
  it('is empty when nothing was edited', () => {
    const record = detail({ application_status: 'Applied' })

    expect(trackerUpdate(draftFromDetail(record), record)).toEqual({})
  })

  it('never sends an untouched unknown field', () => {
    const record = detail({ known_fields: ['application_status'] })

    const update = trackerUpdate(draftFromDetail(record), record)

    expect(update).toEqual({})
    expect(Object.keys(update)).toHaveLength(0)
  })

  it('sends only the edited field', () => {
    const record = detail({
      application_status: 'Applied',
      job_type: 'Full time',
    })
    const draft = { ...draftFromDetail(record), application_status: 'Interview' }

    expect(trackerUpdate(draft, record)).toEqual({
      application_status: 'Interview',
    })
  })

  it('sends an explicit null when a known value is cleared', () => {
    const record = detail({ notes: 'Called the recruiter.' })
    const draft = { ...draftFromDetail(record), notes: '' }

    expect(trackerUpdate(draft, record)).toEqual({ notes: null })
  })

  it('sends a value typed into a previously unknown field', () => {
    const record = detail({ known_fields: [] })
    const draft = { ...draftFromDetail(record), interview_date: '2026-12-01' }

    expect(trackerUpdate(draft, record)).toEqual({
      interview_date: '2026-12-01',
    })
  })

  it('treats typing then deleting as no change', () => {
    const record = detail({ known_fields: [] })
    const draft = { ...draftFromDetail(record), vacancy_url: 'x' }
    const cleared = { ...draft, vacancy_url: '' }

    expect(trackerUpdate(draft, record)).toEqual({ vacancy_url: 'x' })
    expect(trackerUpdate(cleared, record)).toEqual({})
  })

  it('preserves the raw text exactly, including spacing', () => {
    const record = detail({ date_applied: null })
    const draft = { ...draftFromDetail(record), date_applied: ' 1/11/2026 (pagi)' }

    expect(trackerUpdate(draft, record)).toEqual({
      date_applied: ' 1/11/2026 (pagi)',
    })
  })
})
