import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  fetchTargetRoles,
  saveTargetRoles,
  type TargetRole,
} from './targetRoles'

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

const role: TargetRole = {
  role_id: 'role_1',
  canonical_name: 'Backend Engineer',
  aliases: ['Backend Developer'],
}

describe('targetRoles api', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads target roles from the settings endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      mockResponse({ target_roles: [role], max_target_roles: 3 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await fetchTargetRoles()

    expect(result).toEqual({ target_roles: [role], max_target_roles: 3 })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/settings/search',
      expect.objectContaining({
        headers: { Accept: 'application/json' },
      }),
    )
  })

  it('rejects a malformed target-role payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(mockResponse({ target_roles: 'invalid' })),
    )

    await expect(fetchTargetRoles()).rejects.toThrow(/unexpected payload/i)
  })

  it('saves target roles with an explicit PUT body', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(mockResponse({ target_roles: [role], max_target_roles: 3 }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await saveTargetRoles([
      {
        role_id: 'role_1',
        canonical_name: 'Backend Engineer',
        aliases: ['Backend Developer'],
      },
    ])

    expect(result.target_roles).toEqual([role])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/settings/search',
      expect.objectContaining({
        method: 'PUT',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          target_roles: [
            {
              role_id: 'role_1',
              canonical_name: 'Backend Engineer',
              aliases: ['Backend Developer'],
            },
          ],
        }),
      }),
    )
  })

  it('surfaces the backend error envelope', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
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
      ),
    )

    await expect(saveTargetRoles([])).rejects.toMatchObject({
      name: 'TargetRolesApiError',
      code: 'duplicate_target_role',
      message: 'Primary target role names must be unique.',
      details: { canonical_name: 'backend engineer' },
      status: 422,
    })
  })

  it('uses a stable fallback error for non-envelope failures', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(mockResponse({ unexpected: true }, 500)),
    )

    await expect(fetchTargetRoles()).rejects.toMatchObject({
      name: 'TargetRolesApiError',
      code: 'request_failed',
      status: 500,
    })
  })
})
