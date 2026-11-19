import { useEffect, useRef, useState } from 'react'
import {
  fetchSearchPolicy,
  fetchSearchRun,
  startSearchRun,
  type SearchPolicy,
  type SearchRunStatus,
} from '../api/searchRuns'
import { formatJakartaInstant as formatInstant } from '../lib/datetime'
import { TERMINAL_STATUSES } from '../lib/searchRunStatus'
import { AllowanceStrip } from './AllowanceStrip'
import { RunStatusCard } from './RunStatusCard'

type PolicyState =
  | { status: 'loading' }
  | { status: 'loaded'; policy: SearchPolicy }
  | { status: 'error'; message: string }

type StartState =
  | { status: 'idle' }
  | { status: 'starting' }
  | { status: 'started'; runId: string }
  | { status: 'error'; message: string }

type RunState =
  | { status: 'idle' }
  | { status: 'loading'; runId: string }
  | { status: 'loaded'; run: SearchRunStatus; pollingStopped: boolean }
  | { status: 'error'; runId: string; message: string }

type SearchRunPanelProps = {
  pollIntervalMs?: number
  maxPollAttempts?: number
  /** Change this value to re-fetch search eligibility from the backend. */
  refreshToken?: number
}

const DEFAULT_POLL_INTERVAL_MS = 1_000
const DEFAULT_MAX_POLL_ATTEMPTS = 120

const BLOCKER_LABELS: Record<string, string> = {
  no_target_roles: 'Add at least one primary target role.',
  too_many_target_roles: 'Keep at most three primary target roles.',
  no_usable_source:
    'No approved source is available. Register a company source and approve it before starting a search.',
  active_run_exists: 'Another search run is already active.',
  quota_exhausted: 'The two-run daily quota is exhausted.',
  cooldown_active: 'The 12-hour cooldown is still active.',
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown search error'
}

function createIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  throw new Error('This browser cannot generate a secure idempotency key.')
}

export function SearchRunPanel({
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  maxPollAttempts = DEFAULT_MAX_POLL_ATTEMPTS,
  refreshToken = 0,
}: SearchRunPanelProps = {}) {
  const [policyState, setPolicyState] = useState<PolicyState>({
    status: 'loading',
  })
  const [startState, setStartState] = useState<StartState>({ status: 'idle' })
  const [runState, setRunState] = useState<RunState>({ status: 'idle' })
  const [policyVersion, setPolicyVersion] = useState(0)
  const [pollRetryToken, setPollRetryToken] = useState(0)
  const pendingKeyRef = useRef<string | null>(null)
  const startInFlightRef = useRef(false)
  const startControllerRef = useRef<AbortController | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false

    fetchSearchPolicy(controller.signal)
      .then((policy) => {
        if (!cancelled) {
          setPolicyState({ status: 'loaded', policy })
        }
      })
      .catch((error: unknown) => {
        if (!cancelled && !controller.signal.aborted) {
          setPolicyState({ status: 'error', message: errorMessage(error) })
        }
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [policyVersion, refreshToken])

  useEffect(() => {
    return () => {
      startControllerRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    if (startState.status !== 'started') {
      return
    }

    const runId = startState.runId
    const controller = new AbortController()
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let attempts = 0

    async function poll() {
      try {
        const run = await fetchSearchRun(runId, controller.signal)
        if (cancelled) {
          return
        }

        if (TERMINAL_STATUSES.has(run.status)) {
          setRunState({ status: 'loaded', run, pollingStopped: false })
          setPolicyVersion((version) => version + 1)
          return
        }

        attempts += 1
        if (attempts >= maxPollAttempts) {
          setRunState({ status: 'loaded', run, pollingStopped: true })
          return
        }

        setRunState({ status: 'loaded', run, pollingStopped: false })
        timer = setTimeout(poll, pollIntervalMs)
      } catch (error: unknown) {
        if (cancelled || controller.signal.aborted) {
          return
        }
        setRunState({
          status: 'error',
          runId,
          message: errorMessage(error),
        })
      }
    }

    poll()

    return () => {
      cancelled = true
      controller.abort()
      if (timer !== undefined) {
        clearTimeout(timer)
      }
    }
  }, [startState, pollRetryToken, pollIntervalMs, maxPollAttempts])

  // A run started in this session stays "active" until we have observed a
  // terminal status. An unknown/error status must not re-enable Start Search,
  // otherwise the panel invites a duplicate request the backend will reject.
  const runIsActive =
    startState.status === 'started' &&
    !(runState.status === 'loaded' && TERMINAL_STATUSES.has(runState.run.status))
  const policy = policyState.status === 'loaded' ? policyState.policy : null
  const startDisabled =
    !policy ||
    !policy.can_start ||
    runIsActive ||
    startState.status === 'starting'

  async function handleStart() {
    if (startInFlightRef.current || !policy || !policy.can_start || runIsActive) {
      return
    }

    let key = pendingKeyRef.current
    if (key === null) {
      try {
        key = createIdempotencyKey()
      } catch (error: unknown) {
        setStartState({ status: 'error', message: errorMessage(error) })
        return
      }
      pendingKeyRef.current = key
    }

    startInFlightRef.current = true
    const controller = new AbortController()
    startControllerRef.current = controller
    setStartState({ status: 'starting' })
    setRunState({ status: 'idle' })

    try {
      const started = await startSearchRun(key, controller.signal)
      if (controller.signal.aborted) {
        return
      }
      pendingKeyRef.current = null
      setStartState({ status: 'started', runId: started.run_id })
      setRunState({ status: 'loading', runId: started.run_id })
    } catch (error: unknown) {
      if (!controller.signal.aborted) {
        setStartState({ status: 'error', message: errorMessage(error) })
      }
    } finally {
      startInFlightRef.current = false
      if (startControllerRef.current === controller) {
        startControllerRef.current = null
      }
    }
  }

  return (
    <section className="search-panel" aria-labelledby="search-runs-heading">
      <div className="settings-heading">
        <h2 id="search-runs-heading">Search allowance</h2>
        <p>
          Two searches a day, at least twelve hours apart. The backend decides
          whether a search can start; this page only reports that decision.
        </p>
      </div>

      {policyState.status === 'loading' && (
        <p className="settings-note" aria-live="polite">
          Checking search eligibility…
        </p>
      )}

      {policyState.status === 'error' && (
        <div className="settings-alert settings-alert-error" role="alert">
          <h3>Could not load search eligibility</h3>
          <p>{policyState.message}</p>
          <button
            type="button"
            className="button-secondary"
            onClick={() => {
              setPolicyState({ status: 'loading' })
              setPolicyVersion((version) => version + 1)
            }}
          >
            Retry eligibility
          </button>
        </div>
      )}

      {policy && <AllowanceStrip policy={policy} />}

      {policy && (
        <>
          {!policy.can_start && (
            <div className="settings-alert settings-alert-warning" role="status">
              <h3>Search cannot start yet</h3>
              <ul className="blocker-list">
                {policy.blocking_reasons.map((reason) => (
                  <li key={reason}>{BLOCKER_LABELS[reason] ?? reason}</li>
                ))}
              </ul>
              {policy.next_eligible_at && (
                <p>
                  Next eligible at{' '}
                  <time dateTime={policy.next_eligible_at}>
                    {formatInstant(policy.next_eligible_at)}
                  </time>
                  .
                </p>
              )}
            </div>
          )}

          {policy.can_start && (
            <p className="settings-note">
              Configuration is eligible. Starting a search consumes one run from
              today&apos;s quota even if a source later fails.
            </p>
          )}

          <div className="settings-actions">
            <button
              type="button"
              className="button-primary"
              onClick={handleStart}
              disabled={startDisabled}
            >
              {startState.status === 'starting'
                ? 'Starting search…'
                : 'Start search'}
            </button>
          </div>

          {startState.status === 'error' && (
            <div className="settings-alert settings-alert-error" role="alert">
              <h3>Could not start search</h3>
              <p>{startState.message}</p>
              <p className="field-hint">
                Retry reuses the same idempotency key, so the backend will not
                create a duplicate run.
              </p>
            </div>
          )}
        </>
      )}

      {runState.status === 'loading' && (
        <p className="settings-note" aria-live="polite">
          Loading run status…
        </p>
      )}

      {runState.status === 'error' && (
        <div className="settings-alert settings-alert-error" role="alert">
          <h3>Could not load run status</h3>
          <p>{runState.message}</p>
          <button
            type="button"
            className="button-secondary"
            onClick={() => setPollRetryToken((token) => token + 1)}
          >
            Retry status
          </button>
        </div>
      )}

      {runState.status === 'loaded' && (
        <RunStatusCard
          run={runState.run}
          pollingStopped={runState.pollingStopped}
        />
      )}
    </section>
  )
}

