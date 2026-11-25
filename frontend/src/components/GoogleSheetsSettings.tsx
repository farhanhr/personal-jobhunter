import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import {
  disconnectServiceAccount,
  fetchIntegrationStatus,
  GoogleSheetsSettingsError,
  runSaveAndSetup,
  type IntegrationStatus,
  type SetupOutcome,
  uploadServiceAccountKey,
} from '../api/googleSheetsSettings'
import { Input } from './ui/input'
import { Label } from './ui/label'

type LoadState = { status: 'loading' } | { status: 'loaded' } | { status: 'error'; message: string }

function errorMessage(error: unknown): string {
  return error instanceof GoogleSheetsSettingsError
    ? error.message
    : 'The Google Sheets request failed.'
}

function stateLabel(status: IntegrationStatus): string {
  if (status.configuration_error) {
    return 'Invalid configuration'
  }
  switch (status.state) {
    case 'not_configured':
      return 'Not configured'
    case 'awaiting_spreadsheet':
      return 'Awaiting spreadsheet'
    case 'configured':
      return 'Configured'
    case 'credentials_invalid':
      return 'Credentials invalid'
    case 'ready':
      return 'Ready'
  }
}

export function GoogleSheetsSettings() {
  const [status, setStatus] = useState<IntegrationStatus | null>(null)
  const [loadState, setLoadState] = useState<LoadState>({ status: 'loading' })
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [spreadsheetUrl, setSpreadsheetUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<SetupOutcome | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetchIntegrationStatus(controller.signal)
      .then((loaded) => {
        setStatus(loaded)
        setLoadState({ status: 'loaded' })
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setLoadState({ status: 'error', message: errorMessage(error) })
        }
      })
    return () => controller.abort()
  }, [])

  function resetFile() {
    setSelectedFile(null)
    if (fileInput.current) {
      fileInput.current.value = ''
    }
  }

  async function refreshStatus() {
    try {
      setStatus(await fetchIntegrationStatus())
    } catch {
      // Keep the last known status; the next action reports the real failure.
    }
  }

  async function handleConnect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedFile && !status?.key_installed) {
      setActionError('Choose the Service Account JSON file first.')
      return
    }
    setBusy(true)
    setActionError(null)
    setMessage(null)
    setOutcome(null)
    try {
      if (selectedFile) {
        setStatus(await uploadServiceAccountKey(selectedFile))
        setMessage('Service Account key saved.')
        resetFile()
      }
      const result = await runSaveAndSetup(spreadsheetUrl.trim() || undefined)
      setOutcome(result)
      if (result.ready) {
        setSpreadsheetUrl('')
      }
      await refreshStatus()
    } catch (error) {
      setActionError(errorMessage(error))
      await refreshStatus()
    } finally {
      setBusy(false)
    }
  }

  async function handleDisconnect() {
    setBusy(true)
    setActionError(null)
    try {
      setStatus(await disconnectServiceAccount())
      setOutcome(null)
      setMessage('Service Account key removed.')
      resetFile()
    } catch (error) {
      setActionError(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby="google-sheets-heading" className="space-y-4 rounded-lg border p-5">
      <header className="space-y-1">
        <h2 id="google-sheets-heading" className="text-lg font-semibold">
          Google Sheets
        </h2>
        <p className="text-sm text-muted-foreground">
          Connect your tracker spreadsheet with a Service Account key. No Google sign-in is needed.
        </p>
      </header>

      {loadState.status === 'loading' ? <p role="status">Loading integration status…</p> : null}
      {loadState.status === 'error' ? <p role="alert">{loadState.message}</p> : null}

      {status ? (
        <div className="space-y-3">
          <ul className="grid gap-2 sm:grid-cols-3">
            <li className="rounded-md border p-3 text-sm">
              <p className="font-medium">Service Account</p>
              {status.key_installed ? (
                <p className="break-all">
                  {status.service_account_email ? (
                    <span data-testid="service-account-email">{status.service_account_email}</span>
                  ) : (
                    'Key installed'
                  )}
                  {status.service_account_email ? (
                    <CopyEmailButton email={status.service_account_email} />
                  ) : null}
                </p>
              ) : (
                <p className="text-muted-foreground">Not uploaded</p>
              )}
            </li>
            <li className="rounded-md border p-3 text-sm">
              <p className="font-medium">Spreadsheet</p>
              <p className={status.spreadsheet_configured ? undefined : 'text-muted-foreground'}>
                {status.spreadsheet_configured ? 'Connected' : 'Not connected'}
              </p>
            </li>
            <li className="rounded-md border p-3 text-sm">
              <p className="font-medium">Workspace</p>
              <p className={status.workspace_ready ? undefined : 'text-muted-foreground'}>
                {status.workspace_ready ? 'Ready' : stateLabel(status)}
              </p>
            </li>
          </ul>

          <p className="text-sm">{status.message}</p>
          {status.service_account_email ? (
            <p className="text-sm text-muted-foreground">
              Share the spreadsheet with this email and give it Editor access.
            </p>
          ) : null}
          {status.configuration_error ? (
            <p role="alert" className="text-sm">
              {status.configuration_error === 'spreadsheet_url_invalid'
                ? 'The spreadsheet link in your .env file is not a valid Google Sheets URL. Fix it or run Save & Connect with a valid URL.'
                : `Configuration problem: ${status.configuration_error}`}
            </p>
          ) : null}
        </div>
      ) : null}

      <form onSubmit={handleConnect} className="space-y-4">
        <div
          className="space-y-2 rounded-md border border-dashed p-4"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault()
            const file = event.dataTransfer.files?.[0]
            if (file) {
              setSelectedFile(file)
              if (fileInput.current) {
                fileInput.current.value = ''
              }
            }
          }}
        >
          <Label htmlFor="service-account-file">Upload Service Account JSON</Label>
          <p className="text-sm text-muted-foreground">
            Drag the downloaded JSON key here, or choose it below. It is stored only on this
            computer, readable only by you.
          </p>
          <Input
            id="service-account-file"
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
          />
          {selectedFile ? (
            <p className="text-sm text-muted-foreground">Selected: {selectedFile.name}</p>
          ) : null}
        </div>

        <div className="space-y-2">
          <Label htmlFor="spreadsheet-url">Google Spreadsheet URL</Label>
          <p className="text-sm text-muted-foreground">
            Paste the link of an empty spreadsheet, or one this application used before. An
            incompatible spreadsheet is never modified.
          </p>
          <Input
            id="spreadsheet-url"
            type="url"
            placeholder="https://docs.google.com/spreadsheets/d/…"
            value={spreadsheetUrl}
            onChange={(event) => setSpreadsheetUrl(event.target.value)}
          />
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="submit"
            className="button-primary"
            disabled={busy || (!selectedFile && !status?.key_installed)}
          >
            {busy ? 'Connecting…' : 'Save & Connect Workspace'}
          </button>
          <button
            type="button"
            className="button-secondary"
            onClick={() => void handleDisconnect()}
            disabled={busy || !status?.key_installed}
          >
            Disconnect
          </button>
        </div>
      </form>

      {message ? <p role="status">{message}</p> : null}
      {actionError ? <p role="alert">{actionError}</p> : null}

      {outcome ? (
        <div role="status" className="space-y-1 text-sm">
          <p className="font-medium">
            {outcome.ready ? 'Ready' : `Setup stopped at ${outcome.stage ?? 'setup'}`}
          </p>
          <p>{outcome.message}</p>
          {outcome.ready && outcome.adopted_existing ? (
            <p>Adopted an existing workspace and kept its rows.</p>
          ) : null}
          {outcome.ready && outcome.counts ? (
            <p>
              Local data refreshed:{' '}
              {Object.entries(outcome.counts)
                .map(([name, value]) => `${name}=${value}`)
                .join(', ')}
            </p>
          ) : null}
          {outcome.ready ? null : <p>{outcome.preserved}</p>}
          {outcome.optional_warning ? <p>{outcome.optional_warning}</p> : null}
          {outcome.refresh_warning ? <p role="alert">{outcome.refresh_warning}</p> : null}
          {outcome.ready ? <Link to="/">Go to Dashboard</Link> : null}
        </div>
      ) : null}
    </section>
  )
}

function CopyEmailButton({ email }: { email: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      type="button"
      className="ml-2"
      onClick={() => {
        void navigator.clipboard.writeText(email).then(() => setCopied(true))
      }}
    >
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}
