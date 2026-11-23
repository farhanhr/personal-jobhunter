import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import {
  disconnectServiceAccount,
  exchangeSetupToken,
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
  const [csrfToken, setCsrfToken] = useState<string | null>(null)
  const [setupToken, setSetupToken] = useState('')
  const [sessionError, setSessionError] = useState<string | null>(null)
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

  async function handleSession(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSessionError(null)
    setBusy(true)
    try {
      const session = await exchangeSetupToken(setupToken.trim())
      setCsrfToken(session.csrf_token)
      setSetupToken('')
    } catch (error) {
      setSessionError(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  async function run<T>(
    action: (token: string) => Promise<T>,
    after: (value: T) => void,
    onError?: () => void,
  ) {
    if (!csrfToken) {
      setActionError('Unlock this section with the local setup token first.')
      return
    }
    setBusy(true)
    setActionError(null)
    try {
      after(await action(csrfToken))
    } catch (error) {
      if (error instanceof GoogleSheetsSettingsError && error.code === 'setup_session_required') {
        setCsrfToken(null)
      }
      setActionError(errorMessage(error))
      onError?.()
    } finally {
      setBusy(false)
    }
  }

  function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const file = fileInput.current?.files?.[0]
    if (!file) {
      setActionError('Choose the Service Account JSON file first.')
      return
    }
    void run(
      (token) => uploadServiceAccountKey(file, token),
      (next) => {
        setStatus(next)
        setMessage('Service Account key saved.')
        setOutcome(null)
        if (fileInput.current) {
          fileInput.current.value = ''
        }
      },
      () => {
        // A rejected replacement can invalidate the workspace the previous
        // credential validated, so the displayed verdict must be refreshed
        // instead of keeping a stale "Ready".
        void fetchIntegrationStatus().then(setStatus).catch(() => undefined)
      },
    )
  }

  function handleSetup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void run(
      (token) => runSaveAndSetup(spreadsheetUrl.trim() || undefined, token),
      (result) => {
        setOutcome(result)
        setMessage(null)
        if (result.ready) {
          setSpreadsheetUrl('')
        }
        void fetchIntegrationStatus().then(setStatus).catch(() => undefined)
      },
    )
  }

  function handleDisconnect() {
    void run(
      (token) => disconnectServiceAccount(token),
      (next) => {
        setStatus(next)
        setOutcome(null)
        setMessage('Service Account key removed.')
      },
    )
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
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="font-medium">Status</dt>
          <dd>{stateLabel(status)}</dd>
          <dt className="font-medium">Message</dt>
          <dd>{status.message}</dd>
          {status.configuration_error ? (
            <>
              <dt className="font-medium">Configuration error</dt>
              <dd role="alert">
                {status.configuration_error === 'spreadsheet_url_invalid'
                  ? 'The spreadsheet link in your .env file is not a valid Google Sheets URL. Fix it or run Save & Setup with a valid URL.'
                  : `Configuration problem: ${status.configuration_error}`}
              </dd>
            </>
          ) : null}
          {status.service_account_email ? (
            <>
              <dt className="font-medium">Service Account email</dt>
              <dd className="break-all">
                <span data-testid="service-account-email">{status.service_account_email}</span>
                <CopyEmailButton email={status.service_account_email} />
              </dd>
              <dt className="font-medium">Sharing</dt>
              <dd>
                Share the spreadsheet with this email and give it Editor access.
              </dd>
            </>
          ) : null}
        </dl>
      ) : null}

      {!csrfToken ? (
        <form onSubmit={handleSession} className="space-y-2">
          <Label htmlFor="setup-token">Local setup token</Label>
          <p className="text-sm text-muted-foreground">
            Paste the one-time token from the file the server wrote to your local data folder.
            It works once and expires after 30 minutes.
          </p>
          <Input
            id="setup-token"
            name="setup-token"
            type="password"
            autoComplete="off"
            value={setupToken}
            onChange={(event) => setSetupToken(event.target.value)}
          />
          <button type="submit" disabled={busy || setupToken.trim().length === 0}>
            Unlock setup
          </button>
          {sessionError ? <p role="alert">{sessionError}</p> : null}
        </form>
      ) : (
        <div className="space-y-6">
          <form onSubmit={handleUpload} className="space-y-2">
            <Label htmlFor="service-account-file">Service Account JSON key</Label>
            <Input
              id="service-account-file"
              ref={fileInput}
              type="file"
              accept="application/json,.json"
            />
            <button type="submit" disabled={busy}>
              {status?.key_installed ? 'Replace key' : 'Upload key'}
            </button>
          </form>

          <form onSubmit={handleSetup} className="space-y-2">
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
            <div className="flex flex-wrap gap-2">
              <button type="submit" disabled={busy}>
                Save &amp; Setup
              </button>
              <button type="button" onClick={handleDisconnect} disabled={busy}>
                Disconnect
              </button>
            </div>
          </form>
        </div>
      )}

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
