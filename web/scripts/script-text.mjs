// The English UI text built in scripts (toasts, confirm dialogs, labels, the live feed): string literals and
// template literals that read as words, as patterns ("Order {0} not moved"). Heuristic on purpose: the Hebrew
// dictionary may hold a few entries that never show; the coverage test only requires these to be translated.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const SKIP_FILES = /(i18n|page-tours|guide|\.d\.ts|api\.ts|roles\.ts)/
export const LIT = /'((?:[^'\\\n]|\\.)*)'|`((?:[^`\\]|\\.)*)`|"((?:[^"\\\n]|\\.)*)"/g

function files(dir) {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f)
    return statSync(p).isDirectory() ? files(p) : /\.(ts|vue)$/.test(p) && !SKIP_FILES.test(p) ? [p] : []
  })
}

/** `Order ${id} not moved` -> "Order {0} not moved" (nested ${} with braces are kept whole). */
export function toPattern(s) {
  let out = '', n = 0, i = 0
  while (i < s.length) {
    if (s[i] === '$' && s[i + 1] === '{') {
      let depth = 1, j = i + 2
      while (j < s.length && depth) { if (s[j] === '{') depth++; else if (s[j] === '}') depth--; j++ }
      out += `{${n++}}`
      i = j
    } else out += s[i++]
  }
  return out.replace(/\\n/g, ' ').replace(/\\(.)/g, '$1').replace(/\s+/g, ' ').trim()
}

export const looksLikeText = (p) => {
  const words = p.replace(/\{\d+\}/g, ' ').match(/[A-Za-z][a-z']+/g) ?? []
  return /^[A-Z{(“"·—–\d]/.test(p) && words.length >= 2 && !/[<>]|=>|\/api\/|\b(SELECT|var|px|rgba?)\b|^[a-z_.]+$|data-|\.vue|@\//.test(p)
    && !/^[A-Z][a-z]+[A-Z]/.test(p) && !/^\([a-z-]+:/.test(p)
}

export function scriptText(root = 'src') {
  const out = []
  for (const f of files(root)) {
    let src = readFileSync(f, 'utf8')
    if (f.endsWith('.vue')) src = [...src.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join('\n')
    src = src.replace(/^\s*import .*$/gm, '').replace(/\/\/.*$/gm, '')
    for (const m of src.matchAll(LIT)) {
      const p = toPattern(m[1] ?? m[2] ?? m[3] ?? '')
      if (looksLikeText(p)) out.push([p, f])
    }
  }
  return out
}

if (process.argv[1]?.endsWith('script-text.mjs')) {
  const uniq = [...new Set(scriptText().map(([s]) => s))]
  console.log(JSON.stringify({ count: uniq.length, items: uniq }, null, 1))
}
