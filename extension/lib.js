// Pure helpers for the capture extension (unit-tested from web/tests/extension.spec.ts).
// ADR-0007: the extension sends only what the person selected and chose to capture; it never reads a page itself.

export const MAX_TEXT = 5000

/** "(3) הורים בחיפה | Facebook" -> "הורים בחיפה" */
export function groupFromTitle(title) {
  return String(title ?? '')
    .replace(/^\(\d+\+?\)\s*/, '')
    .replace(/\s*[|·-]\s*Facebook\s*$/i, '')
    .trim()
    .slice(0, 200)
}

/** The post's link: the link that was right-clicked if it is one, else the page; https only, no tracking query. */
export function postUrl(linkUrl, pageUrl) {
  for (const raw of [linkUrl, pageUrl]) {
    try {
      const u = new URL(raw)
      if (u.protocol !== 'https:') continue
      for (const k of [...u.searchParams.keys()]) if (/^(__cft__|__tn__|fbclid|ref|mibextid)/.test(k)) u.searchParams.delete(k)
      return u.toString().slice(0, 1000)
    } catch {
      /* not a URL */
    }
  }
  return ''
}

/** The dashboard's origin from what the person typed ("192.168.50.10" -> "https://192.168.50.10"); null if unusable. */
export function farmOrigin(input) {
  const t = String(input ?? '').trim().replace(/\/+$/, '')
  if (!t) return null
  try {
    const u = new URL(/^[a-z]+:\/\//i.test(t) ? t : `https://${t}`)
    return u.protocol === 'https:' ? u.origin : null
  } catch {
    return null
  }
}

export function capturePayload(selection, info, tab) {
  const text = String(selection ?? '').trim().slice(0, MAX_TEXT)
  if (!text) return null
  return { post_text: text, group_name: groupFromTitle(tab?.title), post_url: postUrl(info?.linkUrl, info?.pageUrl ?? tab?.url) }
}

export function newKey() {
  return 'ext-' + crypto.randomUUID().replaceAll('-', '').slice(0, 24)
}
