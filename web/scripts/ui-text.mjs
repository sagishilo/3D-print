// Lists the English UI text of every .vue template (text nodes, static attributes people read, and the static parts
// of text with {{ }} values as a pattern "Due {0}"). Used by the Hebrew coverage test and to find missing words.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

import { parse } from '@vue/compiler-sfc'

import { LIT, toPattern } from './script-text.mjs'

const ATTRS = new Set(['placeholder', 'title', 'aria-label', 'label', 'alt', 'confirm-label', 'empty', 'hint'])
// bound props whose strings people read (a KPI card's sub line, a tab's label…)
const BOUND = new Set([...ATTRS, 'sub', 'text', 'why', 'note', 'help', 'tip', 'heading', 'caption', 'tabs', 'value-label'])
const CLASSY = /^[a-z0-9-]+( [a-z0-9-]+)*$/                  // 'btn btn-sm', 'is-on': classes, not words

/** The UI strings written inside an expression (`{{ ok ? 'Paid' : `due ${d}` }}`), as patterns. */
function literals(expr, file, out) {
  for (const m of String(expr ?? '').matchAll(LIT)) {
    const p = toPattern(m[1] ?? m[2] ?? m[3] ?? '')
    if (/[A-Za-z]{3}/.test(p.replace(/\{\d+\}/g, '')) && !CLASSY.test(p) && !/^[\w.-]+$/.test(p) && !/[<>]|=>|\/api\/|^#|^\.|^@|^\)/.test(p) && !/^[a-zA-Z]+\.[\w.{}]+$/.test(p))
      out.push([p, file])
  }
}
const SKIP_TEXT = /^[\s\d.,:;·•—–\-+−×%/()#₪'"“”!?…|<>=*&@]*$/

function files(dir) {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f)
    return statSync(p).isDirectory() ? files(p) : p.endsWith('.vue') ? [p] : []
  })
}

function walk(node, out, file) {
  if (!node) return
  if (node.type === 1) {                                   // element
    for (const p of node.props ?? []) {
      if (p.type === 6 && ATTRS.has(p.name) && p.value && !SKIP_TEXT.test(p.value.content)) out.push([p.value.content.trim(), file])
      if (p.type === 7 && p.name === 'bind' && BOUND.has(p.arg?.content) && p.exp) literals(p.exp.content, file, out)
    }
    if (['code', 'pre', 'kbd'].includes(node.tag)) return
    // one text run: adjacent text and {{ }} children become one DOM text node
    let run = [], n = 0
    const flush = () => {
      const stat = run.filter((r) => r.type === 2).map((r) => r.content).join('')
      if (stat.replace(/\s+/g, ' ').trim() && !SKIP_TEXT.test(stat)) {
        const pattern = run.map((r) => (r.type === 2 ? r.content : `{${n++}}`)).join('').replace(/\s+/g, ' ').trim()
        out.push([pattern, file])
      }
      run = []
      n = 0
    }
    for (const c of node.children ?? []) {
      if (c.type === 5) literals(c.content?.content, file, out)
      if (c.type === 2 || c.type === 5) run.push(c)
      else if (c.type === 3) continue                      // comment
      else {
        flush()
        walk(c, out, file)
      }
    }
    flush()
    return
  }
  for (const c of node.children ?? []) walk(c, out, file)
  if (node.branches) for (const b of node.branches) walk(b, out, file)
}

export function uiText(root = 'src') {
  const out = []
  for (const f of files(root)) {
    const { descriptor } = parse(readFileSync(f, 'utf8'), { filename: f })
    if (descriptor.template?.ast) walk(descriptor.template.ast, out, f)
  }
  return out
}

if (process.argv[1]?.endsWith('ui-text.mjs')) {
  const all = uiText()
  const uniq = [...new Set(all.map(([s]) => s))]
  console.log(JSON.stringify({ count: uniq.length, items: uniq }, null, 1))
}
