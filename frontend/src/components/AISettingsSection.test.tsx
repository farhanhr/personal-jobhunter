import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AISettingsSection } from './AISettingsSection'

const savedSettings = {
  provider: 'groq',
  base_url: 'https://api.groq.com/openai/v1',
  api_key_masked: 'gsk...1234',
  model: 'llama-3.3-70b-versatile',
  is_configured: true,
  updated_at_utc: '2026-11-24T09:30:00.000000Z',
}

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

type Call = { url: string; init?: RequestInit }

function stubFetch(handler: (call: Call) => Response) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
    handler({ url: String(input), init }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function bodyOf(fetchMock: ReturnType<typeof vi.fn>, method: string) {
  const call = fetchMock.mock.calls.find(
    ([, init]) => (init as RequestInit | undefined)?.method === method,
  )
  if (!call) {
    throw new Error(`Expected a ${method} request`)
  }

  return JSON.parse(String((call[1] as RequestInit).body)) as Record<string, unknown>
}

describe('AISettingsSection', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads the saved provider and shows the masked key hint', async () => {
    stubFetch(() => mockResponse(savedSettings))

    render(<AISettingsSection />)

    expect(screen.getByText(/loading ai settings/i)).toBeInTheDocument()
    expect(await screen.findByRole('combobox', { name: 'Provider' })).toHaveValue('groq')
    expect(screen.getByLabelText(/base url/i)).toHaveValue(
      'https://api.groq.com/openai/v1',
    )
    expect(screen.getByLabelText(/model/i)).toHaveValue(
      'llama-3.3-70b-versatile',
    )
    expect(screen.getByLabelText(/api key/i)).toHaveValue('')
    expect(screen.getByText(/gsk\.\.\.1234/)).toBeInTheDocument()
  })

  it('offers every preset and auto-fills the base URL and model', async () => {
    const user = userEvent.setup()
    stubFetch(() => mockResponse(savedSettings))

    render(<AISettingsSection />)
    const select = await screen.findByRole('combobox', { name: 'Provider' })

    expect(
      Array.from((select as HTMLSelectElement).options).map(
        (option) => option.value,
      ),
    ).toEqual(['groq', 'openrouter', 'deepseek', 'gemini', 'ollama', 'custom'])

    await user.selectOptions(select, 'ollama')

    expect(screen.getByLabelText(/base url/i)).toHaveValue(
      'http://localhost:11434/v1',
    )
    expect(screen.getByLabelText(/model/i)).toHaveValue('llama3.2')
    expect(screen.getByText(/does not need an api key/i)).toBeInTheDocument()
  })

  it('saves the edited endpoint and sends the typed key', async () => {
    const user = userEvent.setup()
    const fetchMock = stubFetch(({ init }) => {
      if (init?.method === 'PUT') {
        return mockResponse({ ...savedSettings, api_key_masked: 'sk-...wxyz' })
      }
      return mockResponse(savedSettings)
    })

    render(<AISettingsSection />)
    await screen.findByLabelText(/model/i)

    await user.clear(screen.getByLabelText(/model/i))
    await user.type(screen.getByLabelText(/model/i), 'deepseek-chat')
    await user.type(screen.getByLabelText(/api key/i), 'sk-test-key-wxyz')
    await user.click(screen.getByRole('button', { name: /save settings/i }))

    expect(await screen.findByText('AI settings saved.')).toBeInTheDocument()
    expect(bodyOf(fetchMock, 'PUT')).toEqual({
      provider: 'groq',
      base_url: 'https://api.groq.com/openai/v1',
      api_key: 'sk-test-key-wxyz',
      model: 'deepseek-chat',
    })
    expect(screen.getByLabelText(/api key/i)).toHaveValue('')
  })

  it('sends a null key when the saved key is left untouched', async () => {
    const user = userEvent.setup()
    const fetchMock = stubFetch(() => mockResponse(savedSettings))

    render(<AISettingsSection />)
    await screen.findByLabelText(/model/i)

    await user.click(screen.getByRole('button', { name: /save settings/i }))

    await screen.findByText('AI settings saved.')
    expect(bodyOf(fetchMock, 'PUT').api_key).toBeNull()
  })

  it('reveals and hides the API key input', async () => {
    const user = userEvent.setup()
    stubFetch(() => mockResponse(savedSettings))

    render(<AISettingsSection />)
    const keyInput = await screen.findByLabelText(/api key/i)

    expect(keyInput).toHaveAttribute('type', 'password')
    await user.click(screen.getByRole('button', { name: /show/i }))
    expect(keyInput).toHaveAttribute('type', 'text')
    await user.click(screen.getByRole('button', { name: /hide/i }))
    expect(keyInput).toHaveAttribute('type', 'password')
  })

  it('reports a successful connection test with its latency', async () => {
    const user = userEvent.setup()
    const fetchMock = stubFetch(({ init }) => {
      if (init?.method === 'POST') {
        return mockResponse({
          success: true,
          latency_ms: 412,
          model: 'llama-3.3-70b-versatile',
          error: null,
        })
      }
      return mockResponse(savedSettings)
    })

    render(<AISettingsSection />)
    await screen.findByLabelText(/model/i)
    await user.click(screen.getByRole('button', { name: /test connection/i }))

    expect(
      await screen.findByText(/connection succeeded in 412 ms/i),
    ).toBeInTheDocument()
    expect(bodyOf(fetchMock, 'POST')).toEqual({
      provider: 'groq',
      base_url: 'https://api.groq.com/openai/v1',
      api_key: null,
      model: 'llama-3.3-70b-versatile',
    })
  })

  it('shows the actionable error when the connection test fails', async () => {
    const user = userEvent.setup()
    stubFetch(({ init }) => {
      if (init?.method === 'POST') {
        return mockResponse({
          success: false,
          latency_ms: null,
          model: null,
          error: 'The provider rejected the API key (HTTP 401). Check the key for this provider and save it again.',
        })
      }
      return mockResponse(savedSettings)
    })

    render(<AISettingsSection />)
    await screen.findByLabelText(/model/i)
    await user.click(screen.getByRole('button', { name: /test connection/i }))

    expect(
      await screen.findByText(/connection test failed/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/HTTP 401/)).toBeInTheDocument()
  })

  it('refuses to test before a base URL is entered', async () => {
    const user = userEvent.setup()
    const fetchMock = stubFetch(() =>
      mockResponse({ ...savedSettings, base_url: '', model: '' }),
    )

    render(<AISettingsSection />)
    await screen.findByLabelText(/base url/i)
    await user.click(screen.getByRole('button', { name: /test connection/i }))

    expect(await screen.findByText(/enter the base url/i)).toBeInTheDocument()
    expect(
      fetchMock.mock.calls.some(
        ([, init]) => (init as RequestInit | undefined)?.method === 'POST',
      ),
    ).toBe(false)
  })
})
