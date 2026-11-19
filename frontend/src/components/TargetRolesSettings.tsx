import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  fetchTargetRoles,
  saveTargetRoles,
  type TargetRole,
  type TargetRoleDraft,
} from '../api/targetRoles'
import { Input } from './ui/input'
import { Label } from './ui/label'

type RoleForm = {
  key: string
  role_id?: string
  canonical_name: string
  aliasesText: string
}

type LoadState =
  | { status: 'loading' }
  | { status: 'loaded' }
  | { status: 'error'; message: string }

type SaveState =
  | { status: 'idle' }
  | { status: 'saving' }
  | { status: 'saved' }
  | { status: 'error'; message: string }

const FALLBACK_MAX_TARGET_ROLES = 3

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown settings error'
}

function toRoleForm(role: TargetRole): RoleForm {
  return {
    key: role.role_id,
    role_id: role.role_id,
    canonical_name: role.canonical_name,
    aliasesText: role.aliases.join(', '),
  }
}

function splitAliases(value: string): string[] {
  return value
    .split(',')
    .map((alias) => alias.trim())
    .filter((alias) => alias.length > 0)
}

export function TargetRolesSettings() {
  const [roles, setRoles] = useState<RoleForm[]>([])
  const [maxTargetRoles, setMaxTargetRoles] = useState(FALLBACK_MAX_TARGET_ROLES)
  const [loadState, setLoadState] = useState<LoadState>({ status: 'loading' })
  const [saveState, setSaveState] = useState<SaveState>({ status: 'idle' })
  const [reloadToken, setReloadToken] = useState(0)
  const nextRoleKey = useRef(0)

  useEffect(() => {
    const controller = new AbortController()

    fetchTargetRoles(controller.signal)
      .then((settings) => {
        setRoles(settings.target_roles.map(toRoleForm))
        setMaxTargetRoles(settings.max_target_roles)
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

  function editRole(key: string, patch: Partial<RoleForm>) {
    setRoles((current) =>
      current.map((role) => (role.key === key ? { ...role, ...patch } : role)),
    )
    setSaveState({ status: 'idle' })
  }

  function addRole() {
    if (roles.length >= maxTargetRoles) {
      return
    }

    nextRoleKey.current += 1
    setRoles((current) => [
      ...current,
      {
        key: `new-${nextRoleKey.current}`,
        canonical_name: '',
        aliasesText: '',
      },
    ])
    setSaveState({ status: 'idle' })
  }

  function removeRole(key: string) {
    setRoles((current) => current.filter((role) => role.key !== key))
    setSaveState({ status: 'idle' })
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()

    if (roles.some((role) => role.canonical_name.trim().length === 0)) {
      setSaveState({
        status: 'error',
        message: 'Role name is required. Enter a name or remove this role.',
      })
      return
    }

    const drafts: TargetRoleDraft[] = roles.map((role) => ({
      ...(role.role_id ? { role_id: role.role_id } : {}),
      canonical_name: role.canonical_name,
      aliases: splitAliases(role.aliasesText),
    }))

    setSaveState({ status: 'saving' })
    try {
      const saved = await saveTargetRoles(drafts)
      setRoles(saved.target_roles.map(toRoleForm))
      setMaxTargetRoles(saved.max_target_roles)
      setSaveState({ status: 'saved' })
    } catch (error: unknown) {
      setSaveState({ status: 'error', message: errorMessage(error) })
    }
  }

  const isSaving = saveState.status === 'saving'

  return (
    <section className="settings-panel" aria-labelledby="target-roles-heading">
      <div className="settings-heading">
        <h2 id="target-roles-heading">Target roles</h2>
        <p>
          Configure one to three primary roles. Aliases are only used when you
          add them explicitly.
        </p>
      </div>

      {loadState.status === 'loading' && (
        <p className="settings-note" aria-live="polite">
          Loading target roles…
        </p>
      )}

      {loadState.status === 'error' && (
        <div className="settings-alert settings-alert-error" role="alert">
          <h3>Could not load target roles</h3>
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
          {roles.length === 0 && (
            <p className="settings-empty">
              No primary target roles configured yet in this draft. A search
              cannot start until at least one role is saved.
            </p>
          )}

          <div className="role-list">
            {roles.map((role, index) => (
              <fieldset
                className="role-card"
                key={role.key}
                disabled={isSaving}
              >
                <legend>Primary role {index + 1}</legend>

                <Label htmlFor={`${role.key}-name`}>Primary role name</Label>
                <Input
                  id={`${role.key}-name`}
                  value={role.canonical_name}
                  onChange={(event) =>
                    editRole(role.key, { canonical_name: event.target.value })
                  }
                  autoComplete="off"
                  maxLength={200}
                />

                <Label htmlFor={`${role.key}-aliases`}>
                  Aliases (comma-separated)
                </Label>
                <Input
                  id={`${role.key}-aliases`}
                  value={role.aliasesText}
                  onChange={(event) =>
                    editRole(role.key, { aliasesText: event.target.value })
                  }
                  autoComplete="off"
                  placeholder="e.g. Backend Developer, API Engineer"
                  maxLength={200}
                  aria-describedby={`${role.key}-aliases-hint`}
                />
                <p className="field-hint" id={`${role.key}-aliases-hint`}>
                  Leave empty to use only the primary role name. Separate
                  aliases with commas.
                </p>

                <button
                  type="button"
                  className="button-danger"
                  aria-label={`Remove primary role ${index + 1}`}
                  onClick={() => removeRole(role.key)}
                >
                  Remove
                </button>
              </fieldset>
            ))}
          </div>

          <div className="settings-actions">
            <button
              type="button"
              className="button-secondary"
              onClick={addRole}
              disabled={isSaving || roles.length >= maxTargetRoles}
            >
              Add primary role
            </button>

            <button
              type="submit"
              className="button-primary"
              disabled={isSaving}
            >
              {saveState.status === 'saving' ? 'Saving…' : 'Save target roles'}
            </button>
          </div>

          {roles.length >= maxTargetRoles && (
            <p className="field-hint">
              Maximum of {maxTargetRoles} primary roles reached.
            </p>
          )}

          {saveState.status === 'saved' && (
            <p className="settings-success" role="status">
              Target roles saved.
            </p>
          )}

          {saveState.status === 'error' && (
            <div className="settings-alert settings-alert-error" role="alert">
              <h3>Could not save target roles</h3>
              <p>{saveState.message}</p>
            </div>
          )}
        </form>
      )}
    </section>
  )
}
