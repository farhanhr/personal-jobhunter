import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { TailoredCV } from '../api/tailoredCv'
import { TailoredCVModal } from './TailoredCVModal'

const optimizeCV: TailoredCV = {
  mode: 'optimize',
  target_role: 'Backend Engineer',
  target_company: 'Acme Example',
  full_name: 'Siti Example',
  contact_header: 'Jakarta, Indonesia | siti@example.test',
  summary: 'Backend engineer focused on payment APIs.',
  skills: {
    technical_skills: ['Python', 'Go'],
    tools_frameworks: ['Docker'],
    domain_knowledge: [],
  },
  experience: [
    {
      company: 'Example Fintech',
      title: 'Senior Backend Engineer',
      location: 'Jakarta',
      period: '2021 - Present',
      bullets: ['Architected the payments API rewrite.'],
    },
  ],
  education: [],
  projects: [],
  upskilling_checklist: [],
}

const ultraCV: TailoredCV = {
  ...optimizeCV,
  mode: 'ultra_optimize',
  upskilling_checklist: ['Study Kafka fundamentals', 'Practice system design interviews'],
}

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

function stubClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
  return writeText
}

describe('TailoredCVModal', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('explains both modes before generating', () => {
    render(<TailoredCVModal opportunityId="opp_1" onClose={() => {}} />)

    expect(
      screen.getByRole('dialog', { name: 'Tailor CV' }),
    ).toBeInTheDocument()
    expect(screen.getByText('Optimize (Recommended)')).toBeInTheDocument()
    expect(screen.getByText('Ultra Optimize')).toBeInTheDocument()
    expect(
      screen.getByText(/without adding unverified skills/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/study checklist of skills to learn/i),
    ).toBeInTheDocument()
  })

  it('generates an optimized CV and renders it in the print area', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn().mockResolvedValue(mockResponse(optimizeCV))
    vi.stubGlobal('fetch', fetchMock)

    render(<TailoredCVModal opportunityId="opp_1" onClose={() => {}} />)
    await user.click(screen.getByRole('button', { name: /optimize \(recommended\)/i }))

    expect(
      await screen.findByText('Siti Example'),
    ).toBeInTheDocument()
    expect(screen.getByText(/backend engineer focused on payment apis/i)).toBeInTheDocument()
    expect(screen.getByText(/architected the payments api rewrite/i)).toBeInTheDocument()

    const printArea = document.querySelector('#ats-cv-print-area')
    expect(printArea).not.toBeNull()
    expect(
      within(printArea as HTMLElement).getByText('Professional Experience'),
    ).toBeInTheDocument()

    expect(String((fetchMock.mock.calls[0] as [string])[0])).toBe(
      '/api/v1/opportunities/opp_1/tailored-cv?mode=optimize',
    )
  })

  it('shows the upskilling checklist outside the printable CV', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(ultraCV)))

    render(<TailoredCVModal opportunityId="opp_1" onClose={() => {}} />)
    await user.click(screen.getByRole('button', { name: /ultra optimize/i }))

    expect(
      await screen.findByRole('heading', {
        name: /upskilling checklist for this role/i,
      }),
    ).toBeInTheDocument()
    expect(screen.getByText('Study Kafka fundamentals')).toBeInTheDocument()

    const printArea = document.querySelector('#ats-cv-print-area') as HTMLElement
    expect(
      within(printArea).queryByText('Study Kafka fundamentals'),
    ).not.toBeInTheDocument()
  })

  it('prints only the CV through the browser dialog', async () => {
    const user = userEvent.setup()
    const printSpy = vi.spyOn(window, 'print').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(optimizeCV)))

    render(<TailoredCVModal opportunityId="opp_1" onClose={() => {}} />)
    await user.click(screen.getByRole('button', { name: /optimize \(recommended\)/i }))
    await screen.findByText('Siti Example')
    await user.click(screen.getByRole('button', { name: /download \/ print pdf/i }))

    expect(printSpy).toHaveBeenCalledTimes(1)
  })

  it('copies the CV as markdown and confirms it', async () => {
    const user = userEvent.setup()
    const writeText = stubClipboard()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(optimizeCV)))

    render(<TailoredCVModal opportunityId="opp_1" onClose={() => {}} />)
    await user.click(screen.getByRole('button', { name: /optimize \(recommended\)/i }))
    await screen.findByText('Siti Example')
    await user.click(screen.getByRole('button', { name: /copy markdown/i }))

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    const markdown = writeText.mock.calls[0][0] as string
    expect(markdown).toContain('# Siti Example')
    expect(markdown).toContain('## Professional Experience')
    expect(await screen.findByText(/copied to clipboard/i)).toBeInTheDocument()
  })

  it('shows the actionable backend error', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        mockResponse(
          {
            error: {
              code: 'candidate_profile_missing',
              message:
                'Please upload your Master CV in Settings before generating a tailored CV.',
            },
          },
          400,
        ),
      ),
    )

    render(<TailoredCVModal opportunityId="opp_1" onClose={() => {}} />)
    await user.click(screen.getByRole('button', { name: /optimize \(recommended\)/i }))

    expect(
      await screen.findByText(/upload your master cv in settings/i),
    ).toBeInTheDocument()
  })

  it('returns to mode selection when the user changes mode', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponse(optimizeCV)))

    render(<TailoredCVModal opportunityId="opp_1" onClose={() => {}} />)
    await user.click(screen.getByRole('button', { name: /optimize \(recommended\)/i }))
    await screen.findByText('Siti Example')
    await user.click(screen.getByRole('button', { name: /change mode/i }))

    expect(screen.getByText('Ultra Optimize')).toBeInTheDocument()
  })

  it('closes on Escape and on the close button', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()

    render(<TailoredCVModal opportunityId="opp_1" onClose={onClose} />)
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})
