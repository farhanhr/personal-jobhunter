import { useEffect, useState, type FormEvent } from 'react'
import {
  createCompanySource,
  fetchCompanySources,
  updateCompanySource,
  type CompanySource,
} from '../api/companies'

type LoadState =
  | { status: 'loading' }
  | { status: 'loaded' }
  | { status: 'error'; message: string }

type SubmitState =
  | { status: 'idle' }
  | { status: 'submitting' }
  | { status: 'error'; message: string }

type RowState = { status: 'idle' } | { status: 'updating' }

type CompanySourcesProps = {
  /** Called after a policy or enablement change succeeds. */
  onSourcePolicyChanged?: () => void
}

const STATUS_LABELS: Record<string, string> = {
  unreviewed: 'Unreviewed',
  permitted: 'Approved for search',
  blocked: 'Blocked from search',
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown company source error'
}

export function CompanySources({ onSourcePolicyChanged }: CompanySourcesProps = {}) {
  const [companies, setCompanies] = useState<CompanySource[]>([])
  const [loadState, setLoadState] = useState<LoadState>({ status: 'loading' })
  const [submitState, setSubmitState] = useState<SubmitState>({ status: 'idle' })
  const [rowState, setRowState] = useState<Record<string, RowState>>({})
  const [companyName, setCompanyName] = useState('')
  const [boardToken, setBoardToken] = useState('')
  const [reloadToken, setReloadToken] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false

    fetchCompanySources(controller.signal)
      .then((loaded) => {
        if (!cancelled) {
          setCompanies(loaded)
          setLoadState({ status: 'loaded' })
        }
      })
      .catch((error: unknown) => {
        if (!cancelled && !controller.signal.aborted) {
          setLoadState({ status: 'error', message: errorMessage(error) })
        }
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [reloadToken])

  function replaceCompany(updated: CompanySource) {
    setCompanies((current) =>
      current.map((company) =>
        company.company_id === updated.company_id ? updated : company,
      ),
    )
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (!companyName.trim() || !boardToken.trim()) {
      setSubmitState({
        status: 'error',
        message: 'Company name and board token are required.',
      })
      return
    }

    setSubmitState({ status: 'submitting' })
    try {
      const created = await createCompanySource({
        company_name: companyName.trim(),
        board_token: boardToken.trim(),
      })
      setCompanies((current) => [...current, created])
      setCompanyName('')
      setBoardToken('')
      setSubmitState({ status: 'idle' })
      onSourcePolicyChanged?.()
    } catch (error: unknown) {
      setSubmitState({ status: 'error', message: errorMessage(error) })
    }
  }

  async function changePolicy(
    company: CompanySource,
    policyStatus: CompanySource['policy_status'],
  ) {
    setRowState((current) => ({
      ...current,
      [company.company_id]: { status: 'updating' },
    }))
    try {
      const updated = await updateCompanySource(company.company_id, {
        policy_status: policyStatus,
      })
      replaceCompany(updated)
      setRowState((current) => ({
        ...current,
        [company.company_id]: { status: 'idle' },
      }))
      onSourcePolicyChanged?.()
    } catch (error: unknown) {
      setRowState((current) => ({
        ...current,
        [company.company_id]: { status: 'idle' },
      }))
      setSubmitState({ status: 'error', message: errorMessage(error) })
    }
  }

  const isSubmitting = submitState.status === 'submitting'

  return (
    <section className="settings-panel" aria-labelledby="company-sources-heading">
      <div className="settings-heading">
        <h2 id="company-sources-heading">Company sources</h2>
        <p>
          Register a company&apos;s public Greenhouse board, then approve it
          explicitly. Unreviewed and blocked sources are never fetched.
        </p>
      </div>

      {loadState.status === 'loading' && (
        <p className="settings-note" aria-live="polite">
          Loading company sources…
        </p>
      )}

      {loadState.status === 'error' && (
        <div className="settings-alert settings-alert-error" role="alert">
          <h3>Could not load company sources</h3>
          <p>{loadState.message}</p>
          <button
            type="button"
            className="button-secondary"
            onClick={() => {
              setLoadState({ status: 'loading' })
              setReloadToken((token) => token + 1)
            }}
          >
            Retry
          </button>
        </div>
      )}

      {loadState.status === 'loaded' && (
        <>
          {companies.length === 0 ? (
            <p className="settings-empty">
              No company sources registered yet. A search cannot start until at
              least one source is approved.
            </p>
          ) : (
            <ul className="company-list">
              {companies.map((company) => (
                <CompanyRow
                  key={company.company_id}
                  company={company}
                  busy={rowState[company.company_id]?.status === 'updating'}
                  onApprove={() => changePolicy(company, 'permitted')}
                  onBlock={() => changePolicy(company, 'blocked')}
                />
              ))}
            </ul>
          )}

          <form className="settings-form" onSubmit={handleSubmit} noValidate>
            <h3>Register a Greenhouse board</h3>
            <div className="company-form-fields">
              <label htmlFor="company-name">Company name</label>
              <input
                id="company-name"
                value={companyName}
                onChange={(event) => setCompanyName(event.target.value)}
                autoComplete="off"
                maxLength={200}
                disabled={isSubmitting}
              />

              <label htmlFor="company-board-token">Board token</label>
              <input
                id="company-board-token"
                value={boardToken}
                onChange={(event) => setBoardToken(event.target.value)}
                autoComplete="off"
                maxLength={64}
                aria-describedby="company-board-token-hint"
                disabled={isSubmitting}
              />
              <p className="field-hint" id="company-board-token-hint">
                The path segment from boards.greenhouse.io/&lt;token&gt;. Letters,
                digits, underscores, and hyphens only.
              </p>
            </div>

            <div className="settings-actions">
              <button
                type="submit"
                className="button-primary"
                disabled={isSubmitting}
              >
                {isSubmitting ? 'Registering…' : 'Register Greenhouse board'}
              </button>
            </div>

            {submitState.status === 'error' && (
              <div className="settings-alert settings-alert-error" role="alert">
                <h3>Could not save company source</h3>
                <p>{submitState.message}</p>
              </div>
            )}
          </form>
        </>
      )}
    </section>
  )
}

function CompanyRow({
  company,
  busy,
  onApprove,
  onBlock,
}: {
  company: CompanySource
  busy: boolean
  onApprove: () => void
  onBlock: () => void
}) {
  const statusLabel = STATUS_LABELS[company.policy_status] ?? company.policy_status
  const willBeFetched = company.can_fetch

  return (
    <li className="company-card">
      <div>
        <h3>{company.company_name}</h3>
        <p className="field-hint">
          {company.ats_type} · board <code>{company.board_token}</code>
        </p>
        <p className={`company-status company-status-${company.policy_status}`}>
          {statusLabel}
          {!willBeFetched && ' — this source will not be fetched.'}
          {willBeFetched && ' — included in the next search.'}
        </p>
      </div>

      <div className="company-actions">
        {company.policy_status !== 'permitted' && (
          <button
            type="button"
            className="button-primary"
            onClick={onApprove}
            disabled={busy}
          >
            Approve source
          </button>
        )}
        {company.policy_status !== 'blocked' && (
          <button
            type="button"
            className="button-danger"
            onClick={onBlock}
            disabled={busy}
          >
            Block source
          </button>
        )}
      </div>
    </li>
  )
}
