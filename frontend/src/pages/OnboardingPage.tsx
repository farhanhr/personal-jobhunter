import { Link, Navigate } from 'react-router'
import { fetchIntegrationStatus } from '../api/googleSheetsSettings'
import { PageHeader } from '../components/PageHeader'
import { useResource } from '../hooks/useResource'

const STEPS = [
  {
    title: 'Upload your Service Account JSON',
    detail:
      'Open Settings → Google Sheets and choose the JSON key you downloaded from Google Cloud. It is stored only on this computer.',
  },
  {
    title: 'Paste your Google Sheet link',
    detail:
      'Share the spreadsheet with the Service Account email as Editor, paste the link, then click Save & Connect Workspace.',
  },
  {
    title: 'Start discovering jobs',
    detail:
      'Add one to three target roles and enable the company sources you trust. Then run your first search from the dashboard.',
  },
]

export function OnboardingPage() {
  const { state, reload } = useResource(fetchIntegrationStatus, 'onboarding')

  if (state.status === 'loaded' && state.data.workspace_ready) {
    return <Navigate to="/" replace />
  }

  return (
    <>
      <PageHeader
        title="Set up your job workspace"
        description="Three short steps: connect a Google Sheet, then let the app collect jobs for you."
      />

      <section className="settings-panel" aria-labelledby="onboarding-steps">
        <h2 id="onboarding-steps" className="text-lg font-semibold">
          Three steps
        </h2>
        <ol className="space-y-3">
          {STEPS.map((step, index) => (
            <li key={step.title} className="space-y-1">
              <p className="font-medium">
                {index + 1}. {step.title}
              </p>
              <p className="text-sm text-muted-foreground">{step.detail}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="settings-panel" aria-labelledby="onboarding-status">
        <h2 id="onboarding-status" className="text-lg font-semibold">
          Current status
        </h2>
        {state.status === 'loading' ? <p role="status">Checking status…</p> : null}
        {state.status === 'error' ? <p role="alert">{state.message}</p> : null}
        {state.status === 'loaded' ? (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="font-medium">Status</dt>
            <dd>{state.data.message}</dd>
            {state.data.service_account_email ? (
              <>
                <dt className="font-medium">Service Account email</dt>
                <dd className="break-all">{state.data.service_account_email}</dd>
              </>
            ) : null}
          </dl>
        ) : null}
        <div className="mt-3 flex flex-wrap gap-2">
          <Link to="/settings">Open Settings</Link>
          <button type="button" onClick={reload}>
            Retry status
          </button>
        </div>
      </section>

      <section className="settings-panel" aria-labelledby="onboarding-links">
        <h2 id="onboarding-links" className="text-lg font-semibold">
          Google Cloud quick links
        </h2>
        <ul className="space-y-1 text-sm">
          <li>
            <a
              href="https://console.cloud.google.com/apis/library/sheets.googleapis.com"
              target="_blank"
              rel="noreferrer"
            >
              Enable the Google Sheets API
            </a>
          </li>
          <li>
            <a
              href="https://console.cloud.google.com/iam-admin/serviceaccounts"
              target="_blank"
              rel="noreferrer"
            >
              Manage Service Accounts
            </a>
          </li>
          <li>
            <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">
              Create a Service Account key
            </a>
          </li>
        </ul>
      </section>
    </>
  )
}
