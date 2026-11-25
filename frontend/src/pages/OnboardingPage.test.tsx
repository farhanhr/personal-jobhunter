import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OnboardingPage } from './OnboardingPage'

function json(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

const pending = {
  state: 'awaiting_spreadsheet',
  message: 'Share the spreadsheet with this email as Editor, then click Save & Setup.',
  service_account_email: 'pjf-reader@pjf-test-project.iam.gserviceaccount.com',
  key_installed: true,
  spreadsheet_configured: false,
  spreadsheet_source: 'none',
  last_setup_stage: null,
  workspace_ready: false,
  onboarding_required: true,
  configured_via: null,
  setup_completed_at: null,
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/onboarding']}>
      <Routes>
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="/" element={<p>Dashboard content</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('OnboardingPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('shows the three streamlined steps, current status, and the Settings call to action', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(pending)))

    renderPage()

    expect(
      screen.getByRole('heading', { name: /set up your job workspace/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/1\. Upload your Service Account JSON/)).toBeInTheDocument()
    expect(screen.getByText(/2\. Paste your Google Sheet link/)).toBeInTheDocument()
    expect(screen.getByText(/3\. Start discovering jobs/)).toBeInTheDocument()
    expect(await screen.findByText(pending.message)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open Settings' })).toHaveAttribute(
      'href',
      '/settings',
    )
  })

  it('redirects to the dashboard when the workspace is already ready', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        json({ ...pending, state: 'ready', workspace_ready: true, onboarding_required: false }),
      ),
    )

    renderPage()

    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()
  })
})
