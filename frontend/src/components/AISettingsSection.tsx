import { useEffect, useState, type FormEvent } from 'react'
import {
  AI_PROVIDER_PRESETS,
  fetchAISettings,
  presetFor,
  saveAISettings,
  testAIConnection,
  type AITestConnectionResult,
} from '../api/aiSettings'
import { Input } from './ui/input'
import { Label } from './ui/label'

type LoadState =
  | { status: 'loading' }
  | { status: 'loaded' }
  | { status: 'error'; message: string }

type SaveState =
  | { status: 'idle' }
  | { status: 'saving' }
  | { status: 'saved' }
  | { status: 'error'; message: string }

type TestState =
  | { status: 'idle' }
  | { status: 'testing' }
  | { status: 'done'; result: AITestConnectionResult }

type FormState = {
  provider: string
  baseUrl: string
  model: string
}

const EMPTY_FORM: FormState = { provider: 'custom', baseUrl: '', model: '' }

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown AI settings error'
}

export function AISettingsSection() {
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [apiKey, setApiKey] = useState('')
  const [apiKeyMasked, setApiKeyMasked] = useState<string | null>(null)
  const [isConfigured, setIsConfigured] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)
  const [loadState, setLoadState] = useState<LoadState>({ status: 'loading' })
  const [saveState, setSaveState] = useState<SaveState>({ status: 'idle' })
  const [testState, setTestState] = useState<TestState>({ status: 'idle' })
  const [reloadToken, setReloadToken] = useState(0)

  useEffect(() => {
    const controller = new AbortController()

    fetchAISettings(controller.signal)
      .then((settings) => {
        setForm({
          provider: settings.provider,
          baseUrl: settings.base_url,
          model: settings.model,
        })
        setApiKeyMasked(settings.api_key_masked)
        setIsConfigured(settings.is_configured)
        setApiKey('')
        setLoadState({ status: 'loaded' })
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return
        }

        setLoadState({ status: 'error', message: errorMessage(error) })
      })

    return () => controller.abort()
  }, [reloadToken])

  const preset = presetFor(form.provider)
  const requiresApiKey = preset?.requiresApiKey ?? true
  const isBusy = saveState.status === 'saving' || testState.status === 'testing'

  function editForm(patch: Partial<FormState>) {
    setForm((current) => ({ ...current, ...patch }))
    setSaveState({ status: 'idle' })
    setTestState({ status: 'idle' })
  }

  function changeProvider(provider: string) {
    const nextPreset = presetFor(provider)
    setForm((current) => {
      if (!nextPreset || nextPreset.id === 'custom') {
        return { ...current, provider }
      }

      return {
        provider,
        baseUrl: nextPreset.baseUrl,
        model: nextPreset.defaultModel,
      }
    })
    setSaveState({ status: 'idle' })
    setTestState({ status: 'idle' })
  }

  function submittedKey(): string | null {
    const trimmed = apiKey.trim()
    return trimmed.length === 0 ? null : trimmed
  }

  function validateForm(): string | null {
    if (form.baseUrl.trim().length === 0) {
      return 'Enter the Base URL of the provider.'
    }
    if (form.model.trim().length === 0) {
      return 'Enter the model name to use.'
    }
    return null
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    const validationError = validateForm()
    if (validationError !== null) {
      setSaveState({ status: 'error', message: validationError })
      return
    }

    setSaveState({ status: 'saving' })
    try {
      const saved = await saveAISettings({
        provider: form.provider,
        base_url: form.baseUrl.trim(),
        api_key: submittedKey(),
        model: form.model.trim(),
      })
      setForm({
        provider: saved.provider,
        baseUrl: saved.base_url,
        model: saved.model,
      })
      setApiKeyMasked(saved.api_key_masked)
      setIsConfigured(saved.is_configured)
      setApiKey('')
      setSaveState({ status: 'saved' })
    } catch (error: unknown) {
      setSaveState({ status: 'error', message: errorMessage(error) })
    }
  }

  async function handleTest() {
    const validationError = validateForm()
    if (validationError !== null) {
      setTestState({
        status: 'done',
        result: { success: false, latency_ms: null, model: null, error: validationError },
      })
      return
    }

    setTestState({ status: 'testing' })
    try {
      const result = await testAIConnection({
        provider: form.provider,
        base_url: form.baseUrl.trim(),
        api_key: submittedKey(),
        model: form.model.trim(),
      })
      setTestState({ status: 'done', result })
    } catch (error: unknown) {
      setTestState({
        status: 'done',
        result: {
          success: false,
          latency_ms: null,
          model: null,
          error: errorMessage(error),
        },
      })
    }
  }

  const apiKeyHint = (() => {
    if (!requiresApiKey) {
      return apiKeyMasked !== null
        ? `This provider does not need an API key. A key is still saved (${apiKeyMasked}) for the other providers.`
        : 'This provider does not need an API key.'
    }
    if (apiKeyMasked !== null) {
      return `A key is saved (${apiKeyMasked}). Leave this blank to keep it.`
    }
    return 'Stored locally in this app. It is only sent to the provider you choose.'
  })()

  return (
    <section className="settings-panel" aria-labelledby="ai-settings-heading">
      <div className="settings-heading">
        <h2 id="ai-settings-heading">AI provider</h2>
        <p>
          Bring your own key for any [OpenAI]-compatible endpoint, including a local
          Ollama server. The key stays in this app&apos;s local database.
        </p>
      </div>

      {loadState.status === 'loading' && (
        <p className="settings-note" aria-live="polite">
          Loading AI settings…
        </p>
      )}

      {loadState.status === 'error' && (
        <div className="settings-alert settings-alert-error" role="alert">
          <h3>Could not load AI settings</h3>
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
        <form className="settings-form" onSubmit={handleSubmit} noValidate>
          <Label htmlFor="ai-provider">Provider</Label>
          <select
            id="ai-provider"
            className="settings-select"
            value={form.provider}
            onChange={(event) => changeProvider(event.target.value)}
            disabled={isBusy}
          >
            {AI_PROVIDER_PRESETS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
          {preset && preset.note.length > 0 && (
            <p className="field-hint">{preset.note}</p>
          )}

          <Label htmlFor="ai-base-url">Base URL</Label>
          <Input
            id="ai-base-url"
            value={form.baseUrl}
            onChange={(event) => editForm({ baseUrl: event.target.value })}
            placeholder="https://api.example.com/v1"
            autoComplete="off"
            spellCheck={false}
            maxLength={2048}
            disabled={isBusy}
          />

          <Label htmlFor="ai-api-key">API key</Label>
          <div className="secret-field">
            <Input
              id="ai-api-key"
              type={showApiKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(event) => {
                setApiKey(event.target.value)
                setSaveState({ status: 'idle' })
                setTestState({ status: 'idle' })
              }}
              placeholder={apiKeyMasked ?? (requiresApiKey ? 'Paste your API key' : 'Not required')}
              autoComplete="off"
              spellCheck={false}
              disabled={isBusy}
              aria-describedby="ai-api-key-hint"
            />
            <button
              type="button"
              className="button-secondary"
              aria-pressed={showApiKey}
              onClick={() => setShowApiKey((visible) => !visible)}
              disabled={isBusy}
            >
              {showApiKey ? 'Hide' : 'Show'}
            </button>
          </div>
          <p className="field-hint" id="ai-api-key-hint">
            {apiKeyHint}
          </p>

          <Label htmlFor="ai-model">Model</Label>
          <Input
            id="ai-model"
            value={form.model}
            onChange={(event) => editForm({ model: event.target.value })}
            placeholder="e.g. llama-3.3-70b-versatile"
            autoComplete="off"
            spellCheck={false}
            maxLength={200}
            disabled={isBusy}
          />

          <div className="settings-actions">
            <button
              type="button"
              className="button-secondary"
              onClick={handleTest}
              disabled={isBusy}
            >
              {testState.status === 'testing' ? 'Testing…' : 'Test Connection'}
            </button>
            <button type="submit" className="button-primary" disabled={isBusy}>
              {saveState.status === 'saving' ? 'Saving…' : 'Save Settings'}
            </button>
          </div>

          {saveState.status === 'saved' && (
            <p className="settings-success" role="status">
              {isConfigured
                ? 'AI settings saved.'
                : 'AI settings saved. Add an API key to make this provider usable.'}
            </p>
          )}

          {saveState.status === 'error' && (
            <div className="settings-alert settings-alert-error" role="alert">
              <h3>Could not save AI settings</h3>
              <p>{saveState.message}</p>
            </div>
          )}

          {testState.status === 'done' && testState.result.success && (
            <div className="settings-success" role="status">
              <p>
                Connection succeeded in {testState.result.latency_ms ?? 0} ms
                {testState.result.model ? ` using ${testState.result.model}` : ''}.
              </p>
            </div>
          )}

          {testState.status === 'done' && !testState.result.success && (
            <div className="settings-alert settings-alert-error" role="alert">
              <h3>Connection test failed</h3>
              <p>{testState.result.error ?? 'The provider did not answer.'}</p>
            </div>
          )}
        </form>
      )}
    </section>
  )
}
