import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { WorkspaceGate } from './WorkspaceGate'

function json(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

const ready = {
  state: 'ready',
  message: 'Google Sheets is connected and ready.',
  service_account_email: 'pjf-reader@pjf-test-project.iam.gserviceaccount.com',
  key_installed: true,
  spreadsheet_configured: true,
  spreadsheet_source: 'ui',
  last_setup_stage: null,
  workspace_ready: true,
  onboarding_required: false,
  configured_via: 'ui',
  setup_completed_at: '2026-11-22T05:00:00.000000Z',
}

const pending = {
  ...ready,
  state: 'awaiting_spreadsheet',
  message: 'Share the spreadsheet with this email as Editor, then click Save & Setup.',
  key_installed: true,
  spreadsheet_configured: false,
  spreadsheet_source: 'none',
  workspace_ready: false,
  onboarding_required: true,
  configured_via: null,
  setup_completed_at: null,
}

function renderGate() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route element={<WorkspaceGate />}>
          <Route index element={<p>Dashboard content</p>} />
        </Route>
        <Route path="/onboarding" element={<p>Onboarding content</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('WorkspaceGate', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('redirects to onboarding when setup has not completed', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(pending)))

    renderGate()

    expect(await screen.findByText('Onboarding content')).toBeInTheDocument()
    expect(screen.queryByText('Dashboard content')).not.toBeInTheDocument()
  })

  it('renders the guarded route when the workspace is ready', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(ready)))

    renderGate()

    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()
  })

  it('shows an error with a retry instead of redirecting when status fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json({ error: { code: 'unavailable', message: 'Backend restarting.' } }, 503)),
    )

    renderGate()

    expect(await screen.findByRole('alert')).toHaveTextContent('Backend restarting.')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.queryByText('Onboarding content')).not.toBeInTheDocument()
  })
})
