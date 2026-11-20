// Hebrew for the whole dashboard (Wave 2 / W2-D). Screens built since W2-0 take their words from the vue-i18n
// catalogues (en.ts / he.ts). The older screens, and text the server sends (Today, the schedule's reasons, order
// status lines, notifications), are translated here: a dictionary from the English text to the Hebrew, where a
// "{0}" in an entry stands for a value ("Order {0} is waiting in {1}" -> "הזמנה {0} ממתינה ב-{1}"), applied to the
// page's text and to the attributes people read (placeholder, title, aria-label, alt) as Vue renders them.
//
// web/tests/hebrew.spec.ts keeps the dictionary complete: every text of every template, every UI string built in
// scripts and every server pattern (src/i18n/server-text.json) must have its Hebrew. Values in the page (names,
// numbers, SKUs) never match an entry, so they stay as they are; an element marked translate="no" is skipped.
import a from './he/ui-a.json'
import b from './he/ui-b.json'
import c from './he/ui-c.json'
import d from './he/ui-d.json'
import e from './he/ui-e.json'
import f from './he/ui-f.json'
import g from './he/ui-g.json'
import h from './he/ui-h.json'
import i from './he/ui-i.json'
import { en } from './en'
import { he } from './he'

/** The keyed catalogues as text pairs too, so a label quoted in the guide or a tour ("**Move to top**") reads in
 *  Hebrew exactly like the button it names. The hand-written dictionary wins where both have an entry. */
function pairs(enPart: object, hePart: object, out: Record<string, string> = {}): Record<string, string> {
  for (const [k, v] of Object.entries(enPart)) {
    const h = (hePart as Record<string, unknown>)[k]
    if (typeof v === 'string' && typeof h === 'string' && /[A-Za-z]{2}/.test(v)) out[v] ??= h
    else if (v && typeof v === 'object' && h && typeof h === 'object') pairs(v, h, out)
  }
  return out
}

export const HE_UI: Record<string, string> = { ...pairs(en, he), ...a, ...b, ...c, ...d, ...e, ...f, ...g, ...h, ...i }

const ATTRS = ['placeholder', 'title', 'aria-label', 'alt'] as const
const HEBREW = /[֐-׿]/
const PLACEHOLDER = /\{(\w+)\}/g
const SPECIFIC = 12                   // letters of its own from which a pattern may take phrases as values

interface Rule {
  re: RegExp
  names: string[]                      // the placeholders in the English order
  out: string
  weight: number                       // letters outside placeholders: the most specific rule wins
}

function escape(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function compile(dict: Record<string, string>): Rule[] {
  const rules: Rule[] = []
  for (const [en, heText] of Object.entries(dict)) {
    if (!PLACEHOLDER.test(en)) continue
    PLACEHOLDER.lastIndex = 0
    const names: string[] = []
    let src = '^', last = 0
    for (const m of en.matchAll(PLACEHOLDER)) {
      src += escape(en.slice(last, m.index)) + '(.*?)'
      names.push(m[1])
      last = (m.index ?? 0) + m[0].length
    }
    src += escape(en.slice(last)) + '$'
    const weight = en.replace(PLACEHOLDER, '').replace(/[^A-Za-z]/g, '').length
    if (weight >= 2) rules.push({ re: new RegExp(src, 's'), names, out: heText, weight })
  }
  return rules.sort((x, y) => y.weight - x.weight)
}

// compiled rules and answers, per dictionary (the app has one; tests bring their own)
const compiled = new WeakMap<Record<string, string>, { rules: Rule[]; cache: Map<string, string | null> }>()
function forDict(dict: Record<string, string>) {
  let c = compiled.get(dict)
  if (!c) compiled.set(dict, (c = { rules: compile(dict), cache: new Map() }))
  return c
}

/** The Hebrew of one piece of English UI text, or null when it is not UI text we know (a name, a number). */
export function translate(text: string, dict: Record<string, string> = HE_UI): string | null {
  const core = text.replace(/\s+/g, ' ').trim()
  if (!core || HEBREW.test(core) || !/[A-Za-z]/.test(core)) return null
  const { rules, cache } = forDict(dict)
  const hit = cache.get(core)
  if (hit !== undefined) return hit === null ? null : keepSpace(text, hit)
  let out: string | null = dict[core] ?? null
  if (out === null) {
    for (const r of rules) {
      const m = r.re.exec(core)
      if (!m) continue
      // a short pattern ("{0} of {1}") takes only single words or numbers as its values, never a whole sentence
      // a value that is itself UI text (a stage name, "Nov 2026") is translated too; a short pattern ("{0} vs {1}")
      // takes a phrase as a value only when that phrase is UI text itself, never a sentence it does not know
      const inner = m.slice(1).map((v) => (/[A-Za-z]{2}/.test(v) && !HEBREW.test(v) ? translate(v, dict) : null))
      if (r.weight < SPECIFIC && m.slice(1).some((v, i) => /\s/.test(v.trim()) && /[A-Za-z]{3}/.test(v) && inner[i] === null))
        continue
      const values = Object.fromEntries(r.names.map((n, i) => [n, inner[i] ?? m[i + 1]]))
      out = r.out.replace(PLACEHOLDER, (_, n: string) => values[n] ?? '')
      break
    }
  }
  if (cache.size > 5000) cache.clear()
  cache.set(core, out)
  return out === null ? null : keepSpace(text, out)
}

function keepSpace(original: string, text: string): string {
  const lead = /^\s*/.exec(original)?.[0] ?? ''
  const trail = /\s*$/.exec(original)?.[0] ?? ''
  return lead + text + trail
}

// ---------------------------------------------------------------- applying it to the page

const written = new WeakMap<Node, string>()                    // what we wrote: never translate our own text
const writtenAttr = new WeakMap<Element, Record<string, string>>()
const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE', 'PRE', 'KBD'])

function skipped(el: Element | null): boolean {
  for (let e = el; e; e = e.parentElement) {
    if (SKIP.has(e.tagName) || e.getAttribute('translate') === 'no' || (e as HTMLElement).isContentEditable) return true
  }
  return false
}

function doText(node: Text) {
  const cur = node.nodeValue ?? ''
  if (written.get(node) === cur || skipped(node.parentElement)) return
  const out = translate(cur)
  if (out !== null && out !== cur) {
    written.set(node, out)
    node.nodeValue = out
  }
}

function doAttrs(el: Element) {
  if (skipped(el)) return
  const done = writtenAttr.get(el) ?? {}
  for (const name of ATTRS) {
    const cur = el.getAttribute(name)
    if (cur === null || done[name] === cur) continue
    const out = translate(cur)
    if (out !== null && out !== cur) {
      done[name] = out
      el.setAttribute(name, out)
    }
  }
  writtenAttr.set(el, done)
}

export function translateTree(root: Node) {
  if (root.nodeType === Node.TEXT_NODE) return doText(root as Text)
  if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return
  if (root.nodeType === Node.ELEMENT_NODE) doAttrs(root as Element)
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT)
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (n.nodeType === Node.TEXT_NODE) doText(n as Text)
    else doAttrs(n as Element)
  }
}

let observer: MutationObserver | null = null

/** Translate the page now and keep translating what Vue renders next. */
export function startTranslating(root: Node = document.documentElement) {
  if (observer) return
  translateTree(root)
  observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type === 'characterData') doText(m.target as Text)
      else if (m.type === 'attributes') doAttrs(m.target as Element)
      else m.addedNodes.forEach((n) => translateTree(n))
    }
  })
  observer.observe(root, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: [...ATTRS] })
}

export function stopTranslating() {
  observer?.disconnect()
  observer = null
}
