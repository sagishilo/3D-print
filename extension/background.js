// PrintFarm Capture: one context-menu item on selected text. Clicking it sends that text (plus the page title as
// the group name and the post's link) to the dashboard with this browser's capture token. Nothing else is read.
import { capturePayload, newKey } from './lib.js'

const MENU = 'printfarm-capture'

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: MENU, title: 'Capture post to PrintFarm', contexts: ['selection'] })
})

function badge(text, color) {
  chrome.action.setBadgeBackgroundColor({ color })
  chrome.action.setBadgeText({ text })
  setTimeout(() => chrome.action.setBadgeText({ text: '' }), 4000)
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== MENU) return
  const { origin, token } = await chrome.storage.local.get(['origin', 'token'])
  if (!origin || !token) {
    badge('SET', '#E3A008')
    chrome.runtime.openOptionsPage()
    return
  }
  const body = capturePayload(info.selectionText, info, tab)
  if (!body) return
  try {
    const res = await fetch(`${origin}/api/v1/social/captures`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, 'Idempotency-Key': newKey() },
      body: JSON.stringify(body),
    })
    if (res.status === 202) badge('✓', '#2EA043')
    else if (res.status === 401) {
      badge('KEY', '#F85149')
      chrome.runtime.openOptionsPage()
    } else badge(String(res.status), '#F85149')
  } catch {
    badge('!', '#F85149')   // unreachable, or this device does not trust the dashboard's certificate yet
  }
})
