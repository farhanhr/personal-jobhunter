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

async function chooseKey(user: ReturnType<typeof userEvent.setup>) {
  await user.upload(
    await screen.findByLabelText('Upload Service Account JSON'),
    new File([SYNTHETIC_KEY], 'key.json', { type: 'application/json' }),
  )
}

function uploadedFile(init: RequestInit): File | null {
  const body = init.body
  return body instanceof FormData ? (body.get('file') as File | null) : null
}

describe('GoogleSheetsSettings', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    sessionStorage.clear()
    localStorage.clear()
  })

  it('shows direct setup controls without asking for a local setup token', async () => {
    vi.stubGlobal('fetch', routes())

    renderSettings()

    expect(await screen.findByText('Not configured')).toBeInTheDocument()
    expect(screen.getByLabelText('Upload Service Account JSON')).toBeInTheDocument()
    expect(screen.getByLabelText('Google Spreadsheet URL')).toBeInTheDocument()
    expect(screen.queryByLabelText('Local setup token')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Unlock setup' })).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Save & Connect Workspace' }),
    ).toBeDisabled()
  })

  it('uploads the key as multipart form data and connects the workspace', async () => {
    const fetchMock = routes({
      'PUT /api/v1/integrations/google-sheets/service-account': () => json(awaiting),
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
          adopted_existing: false,
          refresh_warning: null,
          counts: { opportunities: 1 },
        }),
      'GET /api/v1/integrations/google-sheets/status': () => json(ready),
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    renderSettings()
    await chooseKey(user)
    await user.type(screen.getByLabelText('Google Spreadsheet URL'), SYNTHETIC_URL)
    await user.click(screen.getByRole('button', { name: 'Save & Connect Workspace' }))

    expect((await screen.findAllByText('Ready')).length).toBeGreaterThan(0)
    expect(screen.getByTestId('service-account-email')).toHaveTextContent(SYNTHETIC_EMAIL)

    const upload = fetchMock.mock.calls.find(
      ([input]) => String(input).endsWith('/service-account'),
    )
    const init = upload?.[1] as RequestInit
    expect(init.method).toBe('PUT')
    expect(uploadedFile(init)?.name).toBe('key.json')
    expect((init.headers as Record<string, string>)['X-PJF-Setup-Token']).toBeUndefined()

    const setup = fetchMock.mock.calls.find(
      ([input, config]) =>
        String(input).endsWith('/setup') && (config as RequestInit | undefined)?.method === 'POST',
    )
    const setupInit = setup?.[1] as RequestInit | undefined
    expect(JSON.parse(String(setupInit?.body))).toEqual({
      spreadsheet_url: SYNTHETIC_URL,
    })
  })

  it('uses an already installed key when only a spreadsheet URL is saved', async () => {
    let statusCalls = 0
    const fetchMock = routes(
      {
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
            adopted_existing: false,
            refresh_warning: null,
            counts: null,
          }),
        'GET /api/v1/integrations/google-sheets/status': () =>
          json(statusCalls++ === 0 ? awaiting : ready),
      },
      awaiting,
    )
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    renderSettings()
    await screen.findByText('Awaiting spreadsheet')
    await user.type(screen.getByLabelText('Google Spreadsheet URL'), SYNTHETIC_URL)
    await user.click(screen.getByRole('button', { name: 'Save & Connect Workspace' }))

    expect((await screen.findAllByText('Ready')).length).toBeGreaterThan(0)
    expect(
      fetchMock.mock.calls.some(([input]) => String(input).endsWith('/service-account')),
    ).toBe(false)
  })

  it('never stores or renders the private key in the DOM or browser storage', async () => {
    vi.stubGlobal(
      'fetch',
      routes({
        'PUT /api/v1/integrations/google-sheets/service-account': () => json(awaiting),
        'POST /api/v1/integrations/google-sheets/setup': () =>
          json({
            ready: false,
            stage: 'access',
            code: 'permission_denied',
            message: 'Share the spreadsheet with the Service Account.',
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
        },
        awaiting,
      ),
    )
    const user = userEvent.setup()

    renderSettings()
    await chooseKey(user)
    await user.click(screen.getByRole('button', { name: 'Save & Connect Workspace' }))
    await screen.findByTestId('service-account-email')

    expect(document.body.innerHTML).not.toContain('BEGIN PRIVATE KEY')
    expect(JSON.stringify({ ...localStorage })).not.toContain('PRIVATE KEY')
    expect(JSON.stringify({ ...sessionStorage })).not.toContain('PRIVATE KEY')
    expect(screen.getByLabelText('Upload Service Account JSON')).toHaveValue('')
  })

  it('refreshes the status after a rejected upload so a stale ready state is not shown', async () => {
    const notReadyAfterReplacement = {
      ...awaiting,
      state: 'configured',
      message: 'A spreadsheet is configured. Run Save & Setup to validate it.',
      spreadsheet_configured: true,
      spreadsheet_source: 'ui',
      workspace_ready: false,
      onboarding_required: true,
    }
    let statusCalls = 0
    const fetchMock = routes({
      'GET /api/v1/integrations/google-sheets/status': () =>
        json(statusCalls++ === 0 ? ready : notReadyAfterReplacement),
      'PUT /api/v1/integrations/google-sheets/service-account': () =>
        json(
          {
            error: {
              code: 'permission_denied',
              message:
                'The key was saved, but Google denied access to the spreadsheet. Share it with pjf-writer@pjf-test-project.iam.gserviceaccount.com as Editor, then run Save & Setup again.',
            },
          },
          422,
        ),
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    renderSettings()
    await screen.findByText('Ready')
    await chooseKey(user)
    await user.click(screen.getByRole('button', { name: 'Save & Connect Workspace' }))

    expect(
      await screen.findByText(/Share it with pjf-writer@pjf-test-project\.iam\.gserviceaccount\.com/),
    ).toBeInTheDocument()
    // The stale "Ready" verdict of the replaced credential must not survive.
    await waitFor(() => expect(screen.getByText('Configured')).toBeInTheDocument())
    expect(screen.queryByText('Ready')).not.toBeInTheDocument()
  })

  it('sends the pasted spreadsheet URL in the setup body', async () => {
    const fetchMock = routes(
      {
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
      },
      awaiting,
    )
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    renderSettings()
    await screen.findByText('Awaiting spreadsheet')
    await user.type(screen.getByLabelText('Google Spreadsheet URL'), SYNTHETIC_URL)
    await user.click(screen.getByRole('button', { name: 'Save & Connect Workspace' }))

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
      routes(
        {
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
        },
        awaiting,
      ),
    )
    const user = userEvent.setup()

    renderSettings()
    await screen.findByText('Awaiting spreadsheet')
    await user.click(screen.getByRole('button', { name: 'Save & Connect Workspace' }))

    expect(await screen.findByText('Setup stopped at access')).toBeInTheDocument()
    expect(screen.getAllByText(/Share the spreadsheet/).length).toBeGreaterThan(0)
  })

  it('reports a ready state, adopted counts, and a dashboard link', async () => {
    vi.stubGlobal(
      'fetch',
      routes(
        {
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
        },
        ready,
      ),
    )
    const user = userEvent.setup()

    renderSettings()
    await screen.findByText('Ready')
    await user.click(screen.getByRole('button', { name: 'Save & Connect Workspace' }))

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
      routes(
        {
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
        },
        ready,
      ),
    )
    const user = userEvent.setup()

    renderSettings()
    await screen.findByText('Ready')
    await user.click(screen.getByRole('button', { name: 'Save & Connect Workspace' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Nothing was changed')
  })

  it('asks the user to reconnect when the key is gone after setup', async () => {
    vi.stubGlobal(
      'fetch',
      routes(
        {
          'GET /api/v1/integrations/google-sheets/status': () =>
            json({
              ...ready,
              key_installed: false,
              message:
                'The Service Account key is missing. Restore it in Settings to resume synchronization; cached data remains readable.',
            }),
        },
        {
          ...ready,
          key_installed: false,
          message:
            'The Service Account key is missing. Restore it in Settings to resume synchronization; cached data remains readable.',
        },
      ),
    )

    renderSettings()

    expect((await screen.findAllByText('Ready')).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Restore it in Settings/).length).toBeGreaterThan(0)
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
      routes(
        {
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
          'GET /api/v1/integrations/google-sheets/status': () => json(ready),
        },
        ready,
      ),
    )
    const user = userEvent.setup()

    renderSettings()
    await screen.findByText('Ready')
    await user.click(screen.getByRole('button', { name: 'Save & Connect Workspace' }))

    const alerts = await screen.findAllByRole('alert')
    expect(alerts.some((alert) => alert.textContent?.includes('refresh failed'))).toBe(true)
  })

  it('disconnect removes the key and refreshes the status from the backend', async () => {
    const fetchMock = routes(
      {
        'DELETE /api/v1/integrations/google-sheets/service-account': () => json(notConfigured),
      },
      ready,
    )
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    renderSettings()
    await screen.findByText('Ready')
    await user.click(screen.getByRole('button', { name: 'Disconnect' }))

    await waitFor(() => expect(screen.getByText('Not configured')).toBeInTheDocument())
    const deletes = fetchMock.mock.calls.filter(
      ([input, init]) =>
        String(input).endsWith('/service-account') && (init?.method ?? 'GET') === 'DELETE',
    )
    expect(deletes).toHaveLength(1)
  })
})
