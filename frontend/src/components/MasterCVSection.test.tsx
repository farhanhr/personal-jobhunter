import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MasterCVSection } from './MasterCVSection'

const profile = {
  contact_info: {
    full_name: 'Siti Example',
    email: 'siti@example.test',
    phone: null,
    location: 'Jakarta',
    linkedin_url: null,
    portfolio_url: null,
  },
  summary: 'Backend engineer.',
  skills: {
    hard_skills: ['Python', 'Go'],
    tools: ['Docker'],
    soft_skills: [],
  },
  experience: [
    {
      company: 'Example Fintech',
      title: 'Senior Backend Engineer',
      location: 'Jakarta',
      start_date: '2021',
      end_date: null,
      is_current: true,
      achievements: ['Led the payments rewrite.'],
    },
  ],
  education: [],
  projects: [],
}

const emptyProfile = {
  has_profile: false,
  profile: null,
  file_name: null,
  updated_at_utc: null,
}

const savedProfile = {
  has_profile: true,
  profile,
  file_name: 'cv.pdf',
  updated_at_utc: '2026-11-24T10:15:00.000000Z',
}

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

function stubFetch(handler: (call: { url: string; init?: RequestInit }) => Response | Promise<Response>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler({ url: String(input), init }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function methodCalls(fetchMock: ReturnType<typeof vi.fn>, method: string) {
  return fetchMock.mock.calls.filter(
    ([, init]) => ((init as RequestInit | undefined)?.method ?? 'GET') === method,
  )
}

describe('MasterCVSection', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows the empty state when no CV has been parsed', async () => {
    stubFetch(() => mockResponse(emptyProfile))

    render(<MasterCVSection />)

    expect(screen.getByText(/loading master cv/i)).toBeInTheDocument()
    expect(
      await screen.findByText(/no master cv parsed yet/i),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /choose pdf/i })).toBeInTheDocument()
  })

  it('renders the parsed profile sections for review', async () => {
    stubFetch(() => mockResponse(savedProfile))

    render(<MasterCVSection />)

    expect(await screen.findByLabelText(/full name/i)).toHaveValue('Siti Example')
    expect(screen.getByLabelText(/hard skills/i)).toHaveValue('Python, Go')
    expect(screen.getByLabelText(/^company$/i)).toHaveValue('Example Fintech')
    expect(screen.getByLabelText(/achievements/i)).toHaveValue(
      'Led the payments rewrite.',
    )
    expect(screen.getByText(/current file: cv\.pdf/i)).toBeInTheDocument()
  })

  it('uploads a PDF and shows the extraction and parsing steps', async () => {
    const user = userEvent.setup()
    let resolveUpload: (response: Response) => void = () => {}
    const uploadResponse = new Promise<Response>((resolve) => {
      resolveUpload = resolve
    })
    const fetchMock = stubFetch(({ init }) => {
      if (init?.method === 'POST') {
        return uploadResponse
      }
      return mockResponse(emptyProfile)
    })

    const { container } = render(<MasterCVSection />)
    await screen.findByText(/no master cv parsed yet/i)

    const fileInput = container.querySelector('#cv-file') as HTMLInputElement
    const file = new File(['%PDF-1.4'], 'cv.pdf', { type: 'application/pdf' })
    await user.upload(fileInput, file)

    expect(await screen.findByText(/text extracted/i)).toBeInTheDocument()
    expect(screen.getByText(/parsing profile with ai/i)).toBeInTheDocument()

    await act(async () => {
      resolveUpload(mockResponse(savedProfile))
    })

    expect(await screen.findByLabelText(/full name/i)).toHaveValue('Siti Example')

    const postCall = methodCalls(fetchMock, 'POST')[0]
    const body = (postCall[1] as RequestInit).body as FormData
    expect(body.get('file')).toBeInstanceOf(File)
  })

  it('rejects a non-PDF file before contacting the server', async () => {
    const fetchMock = stubFetch(() => mockResponse(emptyProfile))

    const { container } = render(<MasterCVSection />)
    await screen.findByText(/no master cv parsed yet/i)

    const fileInput = container.querySelector('#cv-file') as HTMLInputElement
    const file = new File(['plain'], 'cv.txt', { type: 'text/plain' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    expect(await screen.findByText(/upload the cv as a pdf/i)).toBeInTheDocument()
    expect(methodCalls(fetchMock, 'POST')).toHaveLength(0)
  })

  it('shows the actionable error when the AI provider is not configured', async () => {
    const user = userEvent.setup()
    stubFetch(({ init }) => {
      if (init?.method === 'POST') {
        return mockResponse(
          {
            error: {
              code: 'ai_not_configured',
              message:
                'AI provider is not configured. Please configure it in Settings first.',
            },
          },
          400,
        )
      }
      return mockResponse(emptyProfile)
    })

    const { container } = render(<MasterCVSection />)
    await screen.findByText(/no master cv parsed yet/i)

    const fileInput = container.querySelector('#cv-file') as HTMLInputElement
    await user.upload(
      fileInput,
      new File(['%PDF-1.4'], 'cv.pdf', { type: 'application/pdf' }),
    )

    expect(
      await screen.findByText(/AI provider is not configured/i),
    ).toBeInTheDocument()
  })

  it('parses pasted text through the same upload endpoint', async () => {
    const user = userEvent.setup()
    const fetchMock = stubFetch(({ init }) => {
      if (init?.method === 'POST') {
        return mockResponse({ ...savedProfile, file_name: null })
      }
      return mockResponse(emptyProfile)
    })

    render(<MasterCVSection />)
    await screen.findByText(/no master cv parsed yet/i)

    await user.click(screen.getByRole('button', { name: /paste text instead/i }))
    await user.type(screen.getByLabelText(/cv text/i), 'Siti Example CV text')
    await user.click(screen.getByRole('button', { name: /parse pasted text/i }))

    expect(await screen.findByLabelText(/full name/i)).toHaveValue('Siti Example')

    const body = (methodCalls(fetchMock, 'POST')[0][1] as RequestInit).body as FormData
    expect(body.get('text')).toBe('Siti Example CV text')
  })

  it('saves manual edits with PUT', async () => {
    const user = userEvent.setup()
    const edited = {
      ...savedProfile,
      profile: { ...profile, summary: 'Edited summary.' },
    }
    const fetchMock = stubFetch(({ init }) => {
      if (init?.method === 'PUT') {
        return mockResponse(edited)
      }
      return mockResponse(savedProfile)
    })

    render(<MasterCVSection />)
    const summary = await screen.findByLabelText(/professional summary/i)
    await user.clear(summary)
    await user.type(summary, 'Edited summary.')
    await user.click(screen.getByRole('button', { name: /save changes/i }))

    expect(await screen.findByText('Master CV saved.')).toBeInTheDocument()

    const putBody = JSON.parse(
      String((methodCalls(fetchMock, 'PUT')[0][1] as RequestInit).body),
    ) as { summary: string }
    expect(putBody.summary).toBe('Edited summary.')
  })

  it('keeps the raw text out of the request and sends only the profile on save', async () => {
    const user = userEvent.setup()
    const fetchMock = stubFetch(() => mockResponse(savedProfile))

    render(<MasterCVSection />)
    await screen.findByLabelText(/full name/i)
    await user.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => expect(methodCalls(fetchMock, 'PUT')).toHaveLength(1))
    const payload = JSON.parse(
      String((methodCalls(fetchMock, 'PUT')[0][1] as RequestInit).body),
    ) as Record<string, unknown>
    expect(payload).not.toHaveProperty('raw_text')
    expect(payload).not.toHaveProperty('file_name')
  })
})
