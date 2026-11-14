import { defineConfig } from '@playwright/test'

// Browser tests against a running stack (docker compose --profile sim up -d). They drive the Edge (or Chrome) already
// installed on this PC, so no browser download is needed: PW_CHANNEL=chrome to switch.
//   PRINTFARM_URL       https://localhost (default)
//   PRINTFARM_PASSWORD  the admin password of that stack (required)
//   PRINTFARM_COMPOSE_PROJECT  compose project name; enables the dead-letter replay test (injects one dead letter)
//   PW_SCREENSHOTS      directory for full-page screenshots of each screen (optional)
export default defineConfig({
  testDir: 'e2e',
  timeout: 90_000,
  expect: { timeout: 20_000 },
  workers: 1,
  reporter: [['list']],
  outputDir: 'test-results',
  use: {
    baseURL: process.env.PRINTFARM_URL ?? 'https://localhost',
    ignoreHTTPSErrors: true, // the stack's local CA is not trusted by a fresh browser profile
    channel: process.env.PW_CHANNEL ?? 'msedge',
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
