// The gateway's readiness (/api/health -> gateway /health): NATS, Postgres, the schema, cameras.
export interface Migrations {
  ok: boolean
  applied: number
  pending: string[]
  problems: string[]
  latest: string | null
}

export interface Health {
  ok: boolean
  nats: boolean
  postgres: boolean
  go2rtc: boolean
  migrations: Migrations | null
}

/** What is wrong, in words, worst first; empty when all is well. `null` = the gateway did not answer at all. */
export function healthProblems(h: Health | null): string[] {
  if (!h) return ['the server is not answering: reconnecting…']
  const out: string[] = []
  if (!h.postgres) out.push('the database is unreachable: nothing can be saved')
  if (!h.nats) out.push('the message bus is down: commands and live updates are paused')
  if (h.migrations) {
    out.push(...h.migrations.problems)
    if (h.migrations.pending.length) out.push(`database not up to date: ${h.migrations.pending.length} migration(s) pending (run: docker compose up migrate)`)
  }
  if (!h.go2rtc) out.push('the camera relay is down: camera tiles are offline')
  return out
}
