import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GoogleSheetsSettings } from './GoogleSheetsSettings'

const SYNTHETIC_EMAIL = 'pjf-reader@pjf-test-project.iam.gserviceaccount.com'
const SYNTHETIC_KEY = '-----BEGIN PRIVATE KEY-----synthetic-----END PRIVATE KEY-----'
const SYNTHETIC_URL = 'https://docs.google.com/spreadsheets/d/1SyntheticSheetId0123456789abcdefXYZ/edit'

function json(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

const notConfigured = {
  state: 'not_configured',
  message: 'Upload the Service Account JSON key to begin.',
  service_account_email: null,
  key_installed: false,
  spreadsheet_configured: false,
  spreadsheet_source: 'none',
  last_setup_stage: null,
  workspace_ready: false,
  onboarding_required: true,
  configured_via: null,
  setup_completed_at: null,
}

const awaiting = {
  ...notConfigured,
  state: 'awaiting_spreadsheet',
  message: 'Share the spreadsheet with this email as Editor, then click Save & Setup.',
  service_account_email: SYNTHETIC_EMAIL,
  key_installed: true,
}

const ready = {
  ...awaiting,
  state: 'ready',
  message: 'Google Sheets is connected and ready.',
  spreadsheet_configured: true,
  spreadsheet_source: 'ui',
  workspace_ready: true,
  onboarding_required: false,
  configured_via: 'ui',
  setup_completed_at: '2026-11-22T05:00:00.000000Z',
}

const invalidEnvironmentUrl = {
  ...notConfigured,
  state: 'not_configured' as const,
  message: 'Upload the Service Account JSON key to begin.',
  key_installed: true,
  service_account_email: SYNTHETIC_EMAIL,
  configuration_error: 'spreadsheet_url_invalid',
}

function routes(
  overrides: Record<string, () => Response> = {},
  statusBody: unknown = notConfigured,
) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    const key = `${method} ${url}`
    if (overrides[key]) {
      return overrides[key]()
    }
    if (key === 'GET /api/v1/integrations/google-sheets/status') {
      return json(statusBody)
    }
    if (key === 'POST /api/v1/integrations/google-sheets/session') {
      return json({ csrf_token: 'csrf-synthetic', expires_at: '2026-11-21T20:00:00+00:00' })
    }
    throw new Error(`Unexpected request: ${key}`)
  })
}

function renderSettings() {
  return render(
    <MemoryRouter>
      <GoogleSheetsSettings />
    </MemoryRouter>,
  )
}

async function unlock(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText('Local setup token'), 'token-synthetic-value')
  await user.click(screen.getByRole('button', { name: 'Unlock setup' }))
  await screen.findByLabelText('Service Account JSON key')
}

describe('GoogleSheetsSettings', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    sessionStorage.clear()
    localStorage.clear()
  })

  it('shows not-configured status and requires a setup token before any mutation', async () => {
    vi.stubGlobal('fetch', routes())

    renderSettings()

    expect(await screen.findByText('Not configured')).toBeInTheDocument()
    expect(screen.queryByLabelText('Service Account JSON key')).not.toBeInTheDocument()
  })

  it('unlocks with the one-time token and sends the CSRF header on upload', async () => {
    const fetchMock = routes({
      'PUT /api/v1/integrations/google-sheets/service-account': () => json(awaiting),
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    renderSettings()
    await unlock(user)

    const file = new File([SYNTHETIC_KEY], 'key.json', { type: 'application/json' })
    await user.upload(screen.getByLabelText('Service Account JSON key'), file)
    await user.click(screen.getByRole('button', { name: 'Upload key' }))

    await waitFor(() =>
      expect(screen.getByTestId('service-account-email')).toHaveTextContent(SYNTHETIC_EMAIL),
    )
    const upload = fetchMock.mock.calls.find(
      ([input]) => String(input).endsWith('/service-account'),
    )
    const init = upload?.[1] as RequestInit
    expect(init.method).toBe('PUT')
    expect((init.headers as Record<string, string>)['X-PJF-Setup-Token']).toBe('csrf-synthetic')
  })

  it('uploads a key downloaded with the original Google Cloud filename', async () => {
    const fetchMock = routes({
      'PUT /api/v1/integrations/google-sheets/service-account': () => json(awaiting),
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    renderSettings()
    await unlock(user)

    const file = new File([SYNTHETIC_KEY], 'example-project-000000-abcdef0123.json', {
      type: 'application/json',
    })
    await user.upload(screen.getByLabelText('Service Account JSON key'), file)
    await user.click(screen.getByRole('button', { name: 'Upload key' }))

    await waitFor(() =>
      expect(screen.getByTestId('service-account-email')).toHaveTextContent(SYNTHETIC_EMAIL),
    )
    const upload = fetchMock.mock.calls.find(
      ([input]) => String(input).endsWith('/service-account'),
    )
    const init = upload?.[1] as RequestInit
    // The downloaded name never leaves the browser: the key itself is the body.
    expect(init.body).toBe(file)
    expect((init.headers as Record<string, string>)['Content-Disposition']).toBeUndefined()
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json')
  })

  it('never stores or renders the private key in the DOM or browser storage', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'PUT /api/v1/integrations/google-sheets/service-account': () => json(awaiting),
      }),
    )
    const user = userEvent.setup()

    renderSettings()
    await unlock(user)
    await user.upload(
      screen.getByLabelText('Service Account JSON key'),
      new File([SYNTHETIC_KEY], 'key.json', { type: 'application/json' }),
    )
    await user.click(screen.getByRole('button', { name: 'Upload key' }))
    await screen.findByTestId('service-account-email')

    expect(document.body.innerHTML).not.toContain('BEGIN PRIVATE KEY')
    expect(JSON.stringify({ ...localStorage })).not.toContain('PRIVATE KEY')
    expect(JSON.stringify({ ...sessionStorage })).not.toContain('PRIVATE KEY')
    expect(screen.getByLabelText('Service Account JSON key')).toHaveValue('')
  })

  it('sends the pasted spreadsheet URL in the setup body', async () => {
    const fetchMock = routes({
      'POST /api/v1/integrations/google-sheets/setup': () =>
        json({
          ready: false,
          stage: 'access',
          code: 'permission_denied',
          message: `Share the spreadsheet with ${SYNTHETIC_EMAIL} as Editor, then retry.`,
          service_account_email: SYNTHETIC_EMAIL,
          source: 'ui',
          retry_safe: true,
          preserved: 'Nothing was changed.',
          optional_warning: null,
          workspace_ready: false,
          adopted_existing: false,
          refresh_warning: null,
          counts: null,
        }),
      'GET /api/v1/integrations/google-sheets/status': () => json(awaiting),
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    renderSettings()
    await unlock(user)
    await user.type(screen.getByLabelText('Google Spreadsheet URL'), SYNTHETIC_URL)
    await user.click(screen.getByRole('button', { name: 'Save & Setup' }))

    await screen.findByText('Setup stopped at access')
    const setup = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith('/setup') && (init as RequestInit | undefined)?.method === 'POST',
    )
    const init = setup?.[1] as RequestInit
    expect(JSON.parse(String(init.body))).toEqual({ spreadsheet_url: SYNTHETIC_URL })
  })

  it('shows permission guidance with the Service Account email after a failed setup', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'POST /api/v1/integrations/google-sheets/setup': () =>
          json({
            ready: false,
            stage: 'access',
            code: 'permission_denied',
            message: `Share the spreadsheet with ${SYNTHETIC_EMAIL} as Editor, then retry.`,
            service_account_email: SYNTHETIC_EMAIL,
            source: 'ui',
            retry_safe: true,
            preserved: 'Nothing was changed.',
            optional_warning: null,
            workspace_ready: false,
            adopted_existing: false,
            refresh_warning: null,
            counts: null,
          }),
        'GET /api/v1/integrations/google-sheets/status': () => json(awaiting),
      }),
    )
    const user = userEvent.setup()

    renderSettings()
    await unlock(user)
    await user.click(screen.getByRole('button', { name: 'Save & Setup' }))

    expect(await screen.findByText('Setup stopped at access')).toBeInTheDocument()
    expect(screen.getAllByText(/Share the spreadsheet/).length).toBeGreaterThan(0)
  })

  it('reports a ready state, adopted counts, and a dashboard link', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'POST /api/v1/integrations/google-sheets/setup': () =>
          json({
            ready: true,
            stage: null,
            code: 'ready',
            message: 'Google Sheets is connected and ready.',
            service_account_email: SYNTHETIC_EMAIL,
            source: 'ui',
            retry_safe: true,
            preserved: 'Nothing was changed.',
            optional_warning: null,
            workspace_ready: true,
            adopted_existing: true,
            refresh_warning: null,
            counts: { opportunities: 12, job_sources: 3 },
          }),
        'GET /api/v1/integrations/google-sheets/status': () => json(ready),
      }),
    )
    const user = userEvent.setup()

    renderSettings()
    await unlock(user)
    await user.click(screen.getByRole('button', { name: 'Save & Setup' }))

    const adopted = await screen.findByText('Adopted an existing workspace and kept its rows.')
    const region = adopted.closest('[role="status"]') as HTMLElement
    expect(within(region).getByText('Google Sheets is connected and ready.')).toBeInTheDocument()
    expect(within(region).getByText(/opportunities=12, job_sources=3/)).toBeInTheDocument()
    expect(within(region).getByRole('link', { name: 'Go to Dashboard' })).toHaveAttribute(
      'href',
      '/',
    )
  })

  it('explains that an incompatible spreadsheet was not changed', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'POST /api/v1/integrations/google-sheets/setup': () =>
          json(
            {
              error: {
                code: 'workspace_incompatible',
                message:
                  'This spreadsheet is not a Personal JobHunter workspace. Nothing was changed.',
                details: {
                  preserved:
                    'Nothing was changed in the spreadsheet or the active configuration.',
                },
              },
            },
            409,
          ),
      }),
    )
    const user = userEvent.setup()

    renderSettings()
    await unlock(user)
    await user.click(screen.getByRole('button', { name: 'Save & Setup' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Nothing was changed')
  })

  it('asks the user to reconnect when the key is gone after setup', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'GET /api/v1/integrations/google-sheets/status': () =>
          json({
            ...ready,
            key_installed: false,
            message:
              'The Service Account key is missing. Restore it in Settings to resume synchronization; cached data remains readable.',
          }),
      }),
    )

    renderSettings()

    expect(await screen.findByText('Ready')).toBeInTheDocument()
    expect(screen.getAllByText(/Restore it in Settings/).length).toBeGreaterThan(0)
  })

  it('an expired session returns the user to the token prompt', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'POST /api/v1/integrations/google-sheets/setup': () =>
          json(
            { error: { code: 'setup_session_required', message: 'Unlock again.', details: {} } },
            401,
          ),
      }),
    )
    const user = userEvent.setup()

    renderSettings()
    await unlock(user)
    await user.click(screen.getByRole('button', { name: 'Save & Setup' }))

    expect(await screen.findByLabelText('Local setup token')).toBeInTheDocument()
  })

  it('surfaces an invalid .env spreadsheet URL as a configuration error', async () => {
    vi.stubGlobal('fetch', routes({}, invalidEnvironmentUrl))

    renderSettings()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'not a valid Google Sheets URL',
    )
    expect(screen.getByText('Invalid configuration')).toBeInTheDocument()
  })

  it('renders a refresh warning as an alert after a successful setup', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'POST /api/v1/integrations/google-sheets/setup': () =>
          json({
            ready: true,
            stage: null,
            code: 'ready',
            message: 'Google Sheets is connected and ready.',
            service_account_email: SYNTHETIC_EMAIL,
            source: 'ui',
            retry_safe: true,
            preserved: 'Nothing was changed.',
            optional_warning: null,
            workspace_ready: true,
            adopted_existing: true,
            refresh_warning: 'Local data refresh failed; retry later.',
            counts: { opportunities: 12, job_sources: 3 },
          }),
      }),
    )
    const user = userEvent.setup()

    renderSettings()
    await unlock(user)
    await user.click(screen.getByRole('button', { name: 'Save & Setup' }))

    const alerts = await screen.findAllByRole('alert')
    expect(alerts.some((alert) => alert.textContent?.includes('refresh failed'))).toBe(true)
  })

  it('disconnect removes the key and refreshes the status from the backend', async () => {
    const fetchMock = routes({
      'DELETE /api/v1/integrations/google-sheets/service-account': () => json(notConfigured),
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    renderSettings()
    await unlock(user)
    await user.click(screen.getByRole('button', { name: 'Disconnect' }))

    await waitFor(() =>
      expect(screen.getByText('Not configured')).toBeInTheDocument(),
    )
    const deletes = fetchMock.mock.calls.filter(
      ([input, init]) =>
        String(input).endsWith('/service-account') && (init?.method ?? 'GET') === 'DELETE',
    )
    expect(deletes).toHaveLength(1)
  })
})
