// Niches (Wave 1 / M3): each product has one main niche plus optional extra tags; order lines keep the niche they
// were sold under. The colours are the --niche-1 .. --niche-8 tokens (tokens.css, contrast-checked in both themes).

export interface Niche {
  slug: string
  name: string
  color: number // 1..8 -> var(--niche-N)
  icon: string
  sort: number
  archived: boolean
  default_material: string
  default_colors: string[]
  target_margin_pct: number
  qc_checklist: string[]
  packaging: string[]
  products: number
  tagged: number
  updated_by?: string
  updated_at?: string
}

export const UNASSIGNED = 'unassigned'
export const NICHE_COLORS = [1, 2, 3, 4, 5, 6, 7, 8] as const

export const nicheColor = (n: Pick<Niche, 'color'> | null | undefined): string => `var(--niche-${n?.color ?? 8})`

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,31}$/

/** A slug from a name: "Kids & toys" -> "kids-toys". */
export function slugFromName(name: string): string {
  return name
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '') // accents: é -> e
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32)
}

export function slugError(slug: string): string | null {
  return SLUG_RE.test(slug) ? null : 'the short name uses a-z, 0-9 and dashes (up to 32)'
}

/** Text area lines -> a clean list (empty lines dropped). */
export const lines = (text: string): string[] => text.split('\n').map((s) => s.trim()).filter(Boolean)
/** "white, black" -> ["white", "black"]. */
export const commaList = (text: string): string[] => text.split(',').map((s) => s.trim()).filter(Boolean)

/** Add ?niche= to an API path when one niche is chosen. */
export function withNiche(path: string, niche: string | null): string {
  if (!niche) return path
  return `${path}${path.includes('?') ? '&' : '?'}niche=${encodeURIComponent(niche)}`
}
