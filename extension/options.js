import { farmOrigin } from './lib.js'

const $ = (id) => document.getElementById(id)
const status = (text, ok) => {
  $('status').textContent = text
  $('status').className = ok ? 'ok' : 'bad'
}

chrome.storage.local.get(['origin', 'token']).then(({ origin, token }) => {
  if (origin) $('origin').value = origin
  if (token) $('token').value = token
})

$('form').addEventListener('submit', async (e) => {
  e.preventDefault()
  const origin = farmOrigin($('origin').value)
  const token = $('token').value.trim()
  if (!origin) return status('The address must be an https:// address (or just the IP).', false)
  if (!token.startsWith('pfc_')) return status('A capture token starts with pfc_.', false)
  // Ask for access to this one address only (not to every site).
  const granted = await chrome.permissions.request({ origins: [`${origin}/*`] })
  if (!granted) return status('Access to the dashboard address was not allowed.', false)
  await chrome.storage.local.set({ origin, token })
  try {
    const res = await fetch(`${origin}/api/v1/social/token-check`, { headers: { Authorization: `Bearer ${token}` } })
    if (res.ok) status(`Saved. Connected as “${(await res.json()).label}”.`, true)
    else status(res.status === 401 ? 'Saved, but the token is unknown or revoked.' : `Saved, but the dashboard answered ${res.status}.`, false)
  } catch {
    status('Saved, but the dashboard cannot be reached (address, network, or certificate).', false)
  }
})
