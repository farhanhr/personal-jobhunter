import { useCallback, useEffect, useRef, useState } from 'react'

export type ResourceState<T> =
  | { status: 'loading' }
  | { status: 'loaded'; data: T }
  | { status: 'error'; message: string; code?: string }

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown request error'
}

/**
 * The API error code, when the thrown error carries one. Callers use it to tell
 * a documented outcome (a 404 for a missing record) from a failure.
 */
function codeFor(error: unknown): string | undefined {
  if (
    typeof error === 'object' &&
    error !== null &&
    'code' in error &&
    typeof (error as { code: unknown }).code === 'string'
  ) {
    return (error as { code: string }).code
  }
  return undefined
}

/**
 * Loads one API resource and exposes it as an explicit state machine.
 *
 * Each dashboard section owns its own request, so a failure in one section
 * cannot blank the others.
 *
 * `key` identifies the request: a new feed, page, or refresh token restarts it,
 * and the result is tagged with the key it belongs to so a stale response never
 * lands in a newer view. The loader is kept in a ref so callers may pass an
 * inline closure without restarting the request on every render, and a refresh
 * shows "loading" during render instead of writing state inside the effect.
 */
export function useResource<T>(
  load: (signal: AbortSignal) => Promise<T>,
  key: string | number = 0,
): { state: ResourceState<T>; reload: () => void } {
  const [localToken, setLocalToken] = useState(0)
  const [result, setResult] = useState<{
    key: string
    value: ResourceState<T>
  } | null>(null)
  const loadRef = useRef(load)

  const requestKey = `${key}:${localToken}`

  useEffect(() => {
    loadRef.current = load
  })

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false

    loadRef
      .current(controller.signal)
      .then((data) => {
        if (!cancelled) {
          setResult({ key: requestKey, value: { status: 'loaded', data } })
        }
      })
      .catch((error: unknown) => {
        if (!cancelled && !controller.signal.aborted) {
          setResult({
            key: requestKey,
            value: {
              status: 'error',
              message: messageFor(error),
              code: codeFor(error),
            },
          })
        }
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [requestKey])

  const state: ResourceState<T> =
    result !== null && result.key === requestKey
      ? result.value
      : { status: 'loading' }

  const reload = useCallback(() => {
    setLocalToken((token) => token + 1)
  }, [])

  return { state, reload }
}
