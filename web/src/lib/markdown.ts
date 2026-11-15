// The user guide (docs/USER_GUIDE_HE.md) rendered in the dashboard: one source for GitHub and for the app.
// Headings get GitHub-style ids so the guide's table of contents works here too; links to other repository files
// open on GitHub. The input is our own file, built into the image (never user content).
import { Marked } from 'marked'

export const REPO_URL = 'https://github.com/sagishilo/3D-print/blob/ops-integration'

/** GitHub's heading anchor: lower case, punctuation dropped (letters of any script kept), spaces to hyphens. */
export function slug(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/<[^>]+>/g, '')
    .replace(/[^\p{L}\p{M}\p{N}\s_-]/gu, '')
    .replace(/\s/g, '-')
}

/** "../README.md" from docs/ -> the file on GitHub; "#anchor" and absolute URLs stay as they are. */
export function resolveLink(href: string, from = 'docs/'): string {
  if (!href || href.startsWith('#') || /^[a-z]+:/i.test(href)) return href
  const parts = (from + href).split('/')
  const out: string[] = []
  for (const p of parts) {
    if (p === '..') out.pop()
    else if (p && p !== '.') out.push(p)
  }
  return `${REPO_URL}/${out.join('/')}`
}

const plain = (s: string) => s.replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'")

export function renderGuide(md: string): string {
  const marked = new Marked({ gfm: true })
  marked.use({
    renderer: {
      heading({ tokens, depth }) {
        const html = this.parser.parseInline(tokens)
        return `<h${depth} id="${slug(plain(html))}">${html}</h${depth}>\n`
      },
      link({ href, title, tokens }) {
        const url = resolveLink(href)
        const external = url !== href || /^https?:/i.test(url)
        const t = title ? ` title="${title}"` : ''
        return `<a href="${url}"${t}${external ? ' target="_blank" rel="noopener noreferrer"' : ''}>${this.parser.parseInline(tokens)}</a>`
      },
    },
  })
  return marked.parse(md, { async: false })
}
