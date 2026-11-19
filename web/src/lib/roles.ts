// Roles (gateway: auth.ROLE_RANK). The gateway enforces them; the dashboard only hides or disables what a role
// cannot do, so nobody is offered a button that would be refused.
export type Role = 'owner' | 'operator' | 'viewer'

export const ROLE_RANK: Record<Role, number> = { viewer: 0, operator: 1, owner: 2 }

export const ROLE_LABEL: Record<Role, string> = { owner: 'Owner', operator: 'Operator', viewer: 'Viewer' }

export const ROLE_HELP: Record<Role, string> = {
  owner: 'Everything, including users, finance and system settings, and destructive actions',
  operator: 'Day-to-day work: floor, orders, fleet, filament, catalog, social review, payments',
  viewer: 'Read-only: sees the operational screens, changes nothing',
}

export function allows(role: Role | null | undefined, min: Role): boolean {
  return role != null && ROLE_RANK[role] >= ROLE_RANK[min]
}

/** The reason shown on a disabled control. */
export const needs = (min: Role) => `Needs the ${ROLE_LABEL[min].toLowerCase()} role`
