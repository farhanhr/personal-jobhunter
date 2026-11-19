import type { TargetRole } from '../api/targetRoles'

/**
 * Resolve an opportunity's stored role id to the configured role's name.
 *
 * The detail API returns the id only, and an internal id must never reach the
 * page as content. The settings endpoint is the one place that knows the name,
 * so the detail page reads that list once. A role deleted after discovery no
 * longer matches; the caller then keeps a human-readable fallback.
 */
export function targetRoleName(
  roles: TargetRole[],
  roleId: string,
): string | null {
  return roles.find((role) => role.role_id === roleId)?.canonical_name ?? null
}
