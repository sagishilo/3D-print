// Printer onboarding (חיבור מדפסת חדשה): validation, the test request, the generated config lines, and the wizard.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import {
  applyCommands,
  emptyForm,
  envName,
  isHost,
  type PrinterForm,
  simulatorForm,
  snippets,
  testRequest,
  validate,
} from '@/lib/onboarding'
import PrinterOnboardingView from '@/views/PrinterOnboardingView.vue'

import { json, serve } from './helpers'

const bambu = (over: Partial<PrinterForm> = {}): PrinterForm => ({
  ...emptyForm('bambu'),
  id: 'p1s-01',
  host: '192.168.50.23',
  serial: '01P00A123456789',
  accessCode: '12345678',
  ...over,
})

describe('validation', () => {
  it('accepts a complete Bambu printer', () => {
    expect(validate(bambu())).toEqual({})
  })

  it('explains every wrong field in Hebrew', () => {
    const e = validate(bambu({ id: 'P1S 01', host: '192.168.1', serial: 'x', accessCode: '1234' }))
    expect(Object.keys(e).sort()).toEqual(['accessCode', 'host', 'id', 'serial'])
    for (const m of Object.values(e)) expect(m).toMatch(/[֐-׿]/)
    expect(e.accessCode).toContain('8')
  })

  it('knows addresses from host names', () => {
    for (const ok of ['192.168.1.50', '10.0.0.1', 'mosquitto', 'voron.local']) expect(isHost(ok), ok).toBe(true)
    for (const bad of ['192.168.1', '256.1.1.1', '192.168.1.50:8883', 'http://x', '', 'a b']) expect(isHost(bad), bad).toBe(false)
  })

  it('needs only an address and port for Klipper', () => {
    expect(validate({ ...emptyForm('moonraker'), id: 'voron-01', host: '192.168.50.31' })).toEqual({})
    expect(validate({ ...emptyForm('moonraker'), id: 'voron-01', host: '192.168.50.31', port: '99999' })).toHaveProperty('port')
  })
})

describe('the connection test request', () => {
  it('sends a real Bambu printer with its defaults', () => {
    expect(testRequest(bambu())).toEqual({
      vendor: 'bambu', host: '192.168.50.23', model: 'p1', serial: '01P00A123456789', access_code: '12345678',
      mqtt_port: 8883, tls: true, ftp_port: 990,
    })
  })

  it('sends the simulator overrides', () => {
    expect(testRequest(simulatorForm('bambu'))).toMatchObject({
      host: 'mosquitto', mqtt_port: 1883, tls: false, ftp_host: 'simulator', ftp_port: 9901, camera_host: 'simulator', camera_port: 6001,
    })
    expect(testRequest(simulatorForm('moonraker'))).toEqual({ vendor: 'moonraker', host: 'simulator', port: 7125 })
  })

  it('sends a Moonraker API key only when given', () => {
    const f = { ...emptyForm('moonraker'), id: 'v', host: '10.0.0.9' }
    expect(testRequest(f)).not.toHaveProperty('api_key')
    expect(testRequest({ ...f, apiKey: 'k' })).toHaveProperty('api_key', 'k')
  })
})

describe('the lines to paste', () => {
  it('Bambu P1: fleet.toml points at the code variable; the code itself goes to cameras.env only', () => {
    const [toml, env, go2rtc] = snippets(bambu({ accessCode: 'Zq7Kp2Mx' }))
    expect(toml.file).toBe('config/fleet.toml')
    expect(toml.text).toContain('id = "p1s-01"')
    expect(toml.text).toContain('access_code_env = "BAMBU_CODE_P1S_01"')
    expect(toml.text).toContain('serial = "01P00A123456789"')
    expect(toml.text).not.toContain('Zq7Kp2Mx')
    expect(env.file).toBe('cameras.env')
    expect(env.text).toBe('BAMBU_CODE_P1S_01=Zq7Kp2Mx\nBAMBU_CAMS=p1s-01=192.168.50.23')
    expect(go2rtc.text).toBe('  p1s-01: http://bambu-cam:8090/p1s-01.mjpeg')
    expect(applyCommands(bambu())[0]).toBe('docker compose --profile p1a1 up -d')
  })

  it('Bambu X1: RTSPS camera through go2rtc, using the variable (not the code)', () => {
    const [, env, go2rtc] = snippets(bambu({ id: 'x1c-02', model: 'x1', host: '192.168.50.21' }))
    expect(env.text).toBe('BAMBU_CODE_X1C_02=12345678')
    expect(go2rtc.text).toContain('rtspx://bblp:${BAMBU_CODE_X1C_02}@192.168.50.21:322/streaming/live/1')
    expect(applyCommands(bambu({ model: 'x1' }))).toEqual(['docker compose up -d', 'docker compose restart telemetry fleet go2rtc'])
  })

  it('Klipper: the Moonraker websocket URL, and an API key variable only when needed', () => {
    const f = { ...emptyForm('moonraker'), id: 'voron-01', host: '192.168.50.31' }
    const files = snippets(f)
    expect(files.map((s) => s.file)).toEqual(['config/fleet.toml', 'infra/go2rtc/go2rtc.yaml'])
    expect(files[0].text).toContain('url = "ws://192.168.50.31:7125/websocket"')
    expect(files[0].text).toContain('api_key_env = ""')
    const keyed = snippets({ ...f, apiKey: 'abc' })
    expect(keyed[0].text).toContain('api_key_env = "MOONRAKER_KEY_VORON_01"')
    expect(keyed[1]).toMatchObject({ file: 'cameras.env', text: 'MOONRAKER_KEY_VORON_01=abc' })
  })

  it('names variables like README step 11', () => {
    expect(envName('BAMBU_CODE', 'a1-mini')).toBe('BAMBU_CODE_A1_MINI')
  })

  it('has nothing to paste for a generic printer', () => {
    expect(snippets({ ...emptyForm('generic'), id: 'x', host: '10.0.0.2' })).toEqual([])
  })
})

describe('the wizard', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => vi.unstubAllGlobals())

  async function open() {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/fleet/add', component: PrinterOnboardingView }, { path: '/fleet', component: { render: () => null } }] })
    await router.push('/fleet/add')
    const w = mount(PrinterOnboardingView, { global: { plugins: [router, createPinia()] } })
    return w
  }
  const next = (w: Awaited<ReturnType<typeof open>>) => w.find('[data-testid="step-next"]')

  it('walks a Bambu printer from the type to the files, with a passing test in between', async () => {
    const calls = serve((c) =>
      c.url === '/api/v1/printers/test-connection'
        ? json(200, { ok: true, vendor: 'bambu', tested_at: 1, checks: [
            { key: 'network', label: 'Network · 192.168.50.23:8883', ok: true, detail: 'port open', optional: false, ms: 3 },
            { key: 'login', label: 'Login (access code)', ok: true, detail: 'logged in as bblp', optional: false, ms: 40 },
            { key: 'camera', label: 'Camera · port 6000', ok: false, detail: 'no answer', optional: true, ms: 5000 },
          ] })
        : undefined,
    )
    const w = await open()
    expect(w.findAll('[data-testid^="vendor-"]').map((b) => b.attributes('data-testid'))).toEqual(['vendor-bambu', 'vendor-moonraker', 'vendor-generic'])
    await w.find('[data-testid="vendor-bambu"]').trigger('click')
    expect(w.find('[data-testid="guide-step"]').text()).toContain('LAN Only Mode')
    await next(w).trigger('click')
    expect(w.find('[data-testid="guide-step"]').text()).toContain('Access Code')
    await next(w).trigger('click')

    // Step 3: nothing can be tested until the form is valid, and Next waits for a passing test.
    expect(next(w).attributes('disabled')).toBeDefined()
    await w.find('[data-testid="test-connection"]').trigger('submit')
    await w.find('form').trigger('submit')
    expect(w.find('[data-testid="form-invalid"]').exists()).toBe(true)
    expect(calls).toHaveLength(0)

    await w.find('[data-testid="f-id"]').setValue('p1s-01')
    await w.find('[data-testid="f-host"]').setValue('192.168.50.23')
    await w.find('[data-testid="f-serial"]').setValue('01P00A123456789')
    await w.find('[data-testid="f-code"]').setValue('12345678')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(calls[0].body).toMatchObject({ vendor: 'bambu', access_code: '12345678', model: 'p1' })
    expect(calls[0].headers['X-CSRF-Token']).toBeDefined()
    const result = w.find('[data-testid="test-result"]')
    expect(result.attributes('data-ok')).toBe('true')
    // An optional camera failure shows its fix but does not block.
    expect(w.find('[data-testid="check-camera"]').text()).toContain('רשות')
    expect(next(w).attributes('disabled')).toBeUndefined()

    // Editing a field drops the stale result.
    await w.find('[data-testid="f-host"]').setValue('192.168.50.24')
    expect(w.find('[data-testid="test-result"]').exists()).toBe(false)
    await w.find('[data-testid="f-host"]').setValue('192.168.50.23')
    await w.find('form').trigger('submit')
    await flushPromises()

    await next(w).trigger('click')
    const add = w.find('[data-testid="add-step"]')
    expect(add.text()).toContain('config/fleet.toml')
    expect(w.find('[data-testid="snippet-1"] pre').text()).toBe('BAMBU_CODE_P1S_01=12345678\nBAMBU_CAMS=p1s-01=192.168.50.23')
    expect(w.find('[data-testid="snippet-commands"] pre').text()).toContain('docker compose restart telemetry fleet go2rtc')
    expect(w.find('[data-testid="finish"]').exists()).toBe(true)
  })

  it('a failing test keeps Next closed and shows how to fix each failure', async () => {
    serve(() => json(200, { ok: false, vendor: 'moonraker', tested_at: 1, checks: [
      { key: 'network', label: 'Network · simulator:7125', ok: false, detail: 'no answer (timed out)', optional: false, ms: 5000 },
    ] }))
    const w = await open()
    await w.find('[data-testid="vendor-moonraker"]').trigger('click')
    await next(w).trigger('click')
    await next(w).trigger('click')
    await w.find('[data-testid="use-simulator"]').trigger('click')
    expect((w.find('[data-testid="f-host"]').element as HTMLInputElement).value).toBe('simulator')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.find('[data-testid="test-result"]').attributes('data-ok')).toBe('false')
    expect(w.find('[data-testid="check-network"]').text()).toContain('באותה רשת')
    expect(next(w).attributes('disabled')).toBeDefined()
  })

  it('shows a gateway refusal (for example an address outside the LAN)', async () => {
    serve(() => json(422, { detail: '8.8.8.8 (8.8.8.8) is not on the local network: printers are tested on the LAN only' }))
    const w = await open()
    await w.find('[data-testid="vendor-generic"]').trigger('click')
    await next(w).trigger('click')
    await w.find('[data-testid="f-id"]').setValue('other-01')
    await w.find('[data-testid="f-host"]').setValue('8.8.8.8')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.find('[data-testid="test-error"]').text()).toContain('not on the local network')
  })
})
