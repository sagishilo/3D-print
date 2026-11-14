// Small display helpers (en, times in the browser's time zone).

export function ago(fromMs: number, nowMs = Date.now()): string {
  if (!fromMs) return '—'
  const s = Math.max(0, (nowMs - fromMs) / 1000)
  if (s < 10) return `${s.toFixed(1)} s`
  if (s < 60) return `${Math.round(s)} s`
  if (s < 3600) return `${Math.round(s / 60)} min`
  if (s < 86400) return `${Math.round(s / 3600)} h`
  return `${Math.round(s / 86400)} d`
}

export function duration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h) return `${h} h ${String(m).padStart(2, '0')} m`
  if (m) return `${m} m`
  return `${s} s`
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString('en-GB', { hour12: false })
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false })
}

export function bytes(n: number | null | undefined): string {
  if (n == null) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

export function count(n: number | null | undefined): string {
  return n == null ? '—' : n.toLocaleString('en-US')
}

export function temp(value: number | null | undefined): string {
  return value == null ? '—' : String(Math.round(value))
}

export function pct(progress: number | null | undefined): string {
  return progress == null ? '—' : `${Math.round(progress * 100)}%`
}

/** Agorot (integer) as shekels. */
export function shekels(agorot: number | null | undefined): string {
  if (agorot == null) return '—'
  return `₪${(agorot / 100).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function title(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s
}
