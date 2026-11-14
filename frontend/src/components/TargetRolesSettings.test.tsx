import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TargetRolesSettings } from './TargetRolesSettings'

type RolePayload = {
  role_id: string
  canonical_name: string
  aliases: string[]
}

const savedRole: RolePayload = {
  role_id: 'role_1',
  canonical_name: 'Backend Engineer',
  aliases: ['Backend Developer'],
}

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

function settingsResponse(roles: RolePayload[]) {
  return mockResponse({ target_roles: roles, max_target_roles: 3 })
}

function getPutBody(fetchMock: ReturnType<typeof vi.fn>) {
  const putCall = fetchMock.mock.calls.find(
    ([, init]) => (init as RequestInit | undefined)?.method === 'PUT',
  )
  if (!putCall) {
    throw new Error('Expected a PUT request')
  }

  const init = putCall[1] as RequestInit
  return JSON.parse(String(init.body)) as {
    target_roles: Array<{
      role_id?: string
      canonical_name: string
      aliases: string[]
    }>
  }
}

describe('TargetRolesSettings', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads and renders saved roles with their explicit aliases', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(settingsResponse([savedRole])),
    )

    render(<TargetRolesSettings />)

    expect(screen.getByText(/loading target roles/i)).toBeInTheDocument()
    expect(await screen.findByDisplayValue('Backend Engineer')).toBeInTheDocument()
    expect(screen.getByLabelText(/aliases/i)).toHaveValue('Backend Developer')
  })

  it('shows an empty state when no roles are configured', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(settingsResponse([])),
    )

    render(<TargetRolesSettings />)

    expect(
      await screen.findByText(/no primary target roles configured yet/i),
    ).toBeInTheDocument()
  })

  it('adds a new role and saves it without a role id', async () => {
    const user = userEvent.setup()
    const saved = {
      role_id: 'role_new',
      canonical_name: 'Data Analyst',
      aliases: [],
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(settingsResponse([]))
      .mockResolvedValueOnce(settingsResponse([saved]))
    vi.stubGlobal('fetch', fetchMock)

    render(<TargetRolesSettings />)

    await user.click(
      await screen.findByRole('button', { name: /add primary role/i }),
    )
    await user.type(screen.getByLabelText(/primary role name/i), 'Data Analyst')
    await user.click(screen.getByRole('button', { name: /save target roles/i }))

    expect(await screen.findByText(/target roles saved/i)).toBeInTheDocument()
    expect(getPutBody(fetchMock)).toEqual({
      target_roles: [{ canonical_name: 'Data Analyst', aliases: [] }],
    })
    expect(screen.getByDisplayValue('Data Analyst')).toBeInTheDocument()
  })

  it('edits a role, keeps its role id, and splits comma-separated aliases', async () => {
    const user = userEvent.setup()
    const updated = {
      ...savedRole,
      canonical_name: 'Senior Backend Engineer',
      aliases: ['Backend Dev', 'Platform Engineer'],
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(settingsResponse([savedRole]))
      .mockResolvedValueOnce(settingsResponse([updated]))
    vi.stubGlobal('fetch', fetchMock)

    render(<TargetRolesSettings />)

    const nameInput = await screen.findByDisplayValue('Backend Engineer')
    await user.clear(nameInput)
    await user.type(nameInput, 'Senior Backend Engineer')

    const aliasInput = screen.getByLabelText(/aliases/i)
    await user.clear(aliasInput)
    await user.type(aliasInput, 'Backend Dev, Platform Engineer')

    await user.click(screen.getByRole('button', { name: /save target roles/i }))

    expect(await screen.findByText(/target roles saved/i)).toBeInTheDocument()
    expect(getPutBody(fetchMock)).toEqual({
      target_roles: [
        {
          role_id: 'role_1',
          canonical_name: 'Senior Backend Engineer',
          aliases: ['Backend Dev', 'Platform Engineer'],
        },
      ],
    })
  })

  it('removes a role and saves the empty configuration', async () => {
    const user = userEvent.setup()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(settingsResponse([savedRole]))
      .mockResolvedValueOnce(settingsResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    render(<TargetRolesSettings />)

    await screen.findByDisplayValue('Backend Engineer')
    await user.click(
      screen.getByRole('button', { name: /remove primary role 1/i }),
    )

    expect(
      screen.getByText(/no primary target roles configured yet/i),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /save target roles/i }))

    expect(await screen.findByText(/target roles saved/i)).toBeInTheDocument()
    expect(getPutBody(fetchMock)).toEqual({ target_roles: [] })
  })

  it('prevents adding more than three primary roles', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        settingsResponse([
          { role_id: 'role_1', canonical_name: 'Backend Engineer', aliases: [] },
          { role_id: 'role_2', canonical_name: 'Data Analyst', aliases: [] },
          { role_id: 'role_3', canonical_name: 'Product Manager', aliases: [] },
        ]),
      ),
    )

    render(<TargetRolesSettings />)

    expect(
      await screen.findByRole('button', { name: /add primary role/i }),
    ).toBeDisabled()
    expect(screen.getByText(/maximum of 3 primary roles/i)).toBeInTheDocument()
  })

  it('shows a backend validation error and keeps the editable values', async () => {
    const user = userEvent.setup()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(settingsResponse([]))
      .mockResolvedValueOnce(
        mockResponse(
          {
            error: {
              code: 'duplicate_target_role',
              message: 'Primary target role names must be unique.',
              details: { canonical_name: 'backend engineer' },
              request_id: 'req_test',
            },
          },
          422,
        ),
      )
    vi.stubGlobal('fetch', fetchMock)

    render(<TargetRolesSettings />)

    await user.click(
      await screen.findByRole('button', { name: /add primary role/i }),
    )
    await user.type(screen.getByLabelText(/primary role name/i), 'Backend Engineer')
    await user.click(screen.getByRole('button', { name: /save target roles/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/could not save target roles/i)
    expect(alert).toHaveTextContent(/primary target role names must be unique/i)
    expect(screen.getByDisplayValue('Backend Engineer')).toBeInTheDocument()
  })

  it('requires a role name before saving a new role', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn().mockResolvedValueOnce(settingsResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    render(<TargetRolesSettings />)

    await user.click(
      await screen.findByRole('button', { name: /add primary role/i }),
    )
    await user.click(screen.getByRole('button', { name: /save target roles/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      /role name is required/i,
    )
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('stops adding roles when the form reaches three', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        settingsResponse([
          { role_id: 'role_1', canonical_name: 'Backend Engineer', aliases: [] },
          { role_id: 'role_2', canonical_name: 'Data Analyst', aliases: [] },
        ]),
      ),
    )

    render(<TargetRolesSettings />)

    await user.click(
      await screen.findByRole('button', { name: /add primary role/i }),
    )

    expect(screen.getByRole('button', { name: /add primary role/i })).toBeDisabled()
    expect(screen.getByText(/maximum of 3 primary roles/i)).toBeInTheDocument()
  })

  it('disables the form while a save is in flight', async () => {
    const user = userEvent.setup()
    let resolveSave!: (response: Response) => void
    const pendingSave = new Promise<Response>((resolve) => {
      resolveSave = resolve
    })
    const saved = {
      role_id: 'role_new',
      canonical_name: 'Data Analyst',
      aliases: [],
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(settingsResponse([]))
      .mockReturnValueOnce(pendingSave)
    vi.stubGlobal('fetch', fetchMock)

    render(<TargetRolesSettings />)

    await user.click(
      await screen.findByRole('button', { name: /add primary role/i }),
    )
    await user.type(screen.getByLabelText(/primary role name/i), 'Data Analyst')
    await user.click(screen.getByRole('button', { name: /save target roles/i }))

    expect(screen.getByLabelText(/primary role name/i)).toBeDisabled()
    expect(
      screen.getByRole('button', { name: /add primary role/i }),
    ).toBeDisabled()

    await act(async () => {
      resolveSave(settingsResponse([saved]))
      await pendingSave
    })

    expect(await screen.findByText(/target roles saved/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/primary role name/i)).not.toBeDisabled()
  })

  it('shows a save failure when the request cannot be sent', async () => {
    const user = userEvent.setup()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(settingsResponse([]))
      .mockRejectedValueOnce(new Error('Failed to fetch'))
    vi.stubGlobal('fetch', fetchMock)

    render(<TargetRolesSettings />)

    await user.click(
      await screen.findByRole('button', { name: /add primary role/i }),
    )
    await user.type(screen.getByLabelText(/primary role name/i), 'Data Analyst')
    await user.click(screen.getByRole('button', { name: /save target roles/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/could not save target roles/i)
    expect(alert).toHaveTextContent(/failed to fetch/i)
  })

  it('shows a load failure with a retry action', async () => {
    const user = userEvent.setup()
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('Network down'))
      .mockResolvedValueOnce(settingsResponse([]))
    vi.stubGlobal('fetch', fetchMock)

    render(<TargetRolesSettings />)

    expect(
      await screen.findByText(/could not load target roles/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/network down/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /retry/i }))

    expect(
      await screen.findByText(/no primary target roles configured yet/i),
    ).toBeInTheDocument()
  })

  it('labels each role group for accessible editing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        settingsResponse([
          savedRole,
          { role_id: 'role_2', canonical_name: 'Data Analyst', aliases: [] },
        ]),
      ),
    )

    render(<TargetRolesSettings />)

    const roleGroups = await screen.findAllByRole('group')
    expect(roleGroups).toHaveLength(2)
    expect(within(roleGroups[0]).getByText(/primary role 1/i)).toBeInTheDocument()
    expect(within(roleGroups[1]).getByText(/primary role 2/i)).toBeInTheDocument()
  })
})
