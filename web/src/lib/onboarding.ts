// חיבור מדפסת חדשה (printer onboarding): the form, its validation, the one-off connection test request, and the lines
// the operator pastes into the stack's files afterwards. The dashboard never stores a printer or its access code:
// printers are configured in files (config/fleet.toml + cameras.env), as README steps 10-12 describe.

export type Vendor = 'bambu' | 'moonraker' | 'generic'
export type BambuModel = 'x1' | 'p1' | 'a1' | 'a1-mini'

export interface PrinterForm {
  vendor: Vendor
  id: string
  host: string
  // Bambu Lab
  model: BambuModel
  serial: string
  accessCode: string
  // Klipper / generic
  port: string
  apiKey: string
  // advanced (simulator or non-standard ports)
  mqttPort: string
  tls: boolean
  ftpHost: string
  ftpPort: string
  cameraHost: string
  cameraPort: string
  // the printer's defaults in the farm
  material: string
  color: string
  // Bambu: AMS slots attached (0 = one external spool; 4 = one AMS / AMS Lite) and print hours already on it
  amsSlots: string
  printHours: string
}

export const emptyForm = (vendor: Vendor): PrinterForm => ({
  vendor,
  id: '',
  host: '',
  model: 'p1',
  serial: '',
  accessCode: '',
  port: vendor === 'moonraker' ? '7125' : vendor === 'generic' ? '80' : '',
  apiKey: '',
  mqttPort: '8883',
  tls: true,
  ftpHost: '',
  ftpPort: '990',
  cameraHost: '',
  cameraPort: '',
  material: 'PLA',
  color: 'black',
  amsSlots: '0',
  printHours: '',
})

/** The built-in simulated printers (docker compose --profile sim), to practise the whole flow without hardware. */
export function simulatorForm(vendor: Vendor): PrinterForm {
  if (vendor === 'moonraker') return { ...emptyForm('moonraker'), id: 'practice-voron', host: 'simulator', port: '7125' }
  return {
    ...emptyForm('bambu'),
    id: 'practice-p1',
    model: 'p1',
    host: 'mosquitto',
    serial: 'SIM00000000001',
    accessCode: '12345678',
    mqttPort: '1883',
    tls: false,
    ftpHost: 'simulator',
    ftpPort: '9901',
    cameraHost: 'simulator',
    cameraPort: '6001',
  }
}

export const VENDORS: { id: Vendor; name: string; models: string; text: string }[] = [
  { id: 'bambu', name: 'Bambu Lab', models: 'X1 · P1 · A1 · A1 mini', text: 'מדפסות Bambu Lab, בחיבור מקומי (LAN Only Mode).' },
  { id: 'moonraker', name: 'Klipper / Voron', models: 'Moonraker', text: 'מדפסות עם Klipper: Voron, RatRig, Sovol ועוד, דרך Moonraker.' },
  { id: 'generic', name: 'Generic / Other', models: 'Network printer', text: 'מדפסת אחרת ברשת: בודקים רק שהיא עונה.' },
]

const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/
const HOSTNAME = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$/i

export const isHost = (s: string) => IPV4.test(s.trim()) || (HOSTNAME.test(s.trim()) && !/^\d+(\.\d+)*$/.test(s.trim()))
export const isPort = (s: string) => /^\d{1,5}$/.test(s.trim()) && Number(s) >= 1 && Number(s) <= 65535
export const isPrinterId = (s: string) => /^[a-z0-9][a-z0-9_-]{1,30}$/.test(s)
export const isAccessCode = (s: string) => /^[A-Za-z0-9]{8}$/.test(s.trim())
export const isSerial = (s: string) => /^[A-Za-z0-9]{8,20}$/.test(s.trim())

export type Errors = Partial<Record<keyof PrinterForm, string>>

/** Hebrew messages for the operator, one per field. Empty when the form can be tested. */
export function validate(f: PrinterForm): Errors {
  const e: Errors = {}
  if (!isPrinterId(f.id)) e.id = 'שם קצר באותיות קטנות באנגלית, ספרות ו-"-" בלבד (למשל p1s-01)'
  if (!isHost(f.host)) e.host = 'כתובת IP במבנה 192.168.1.50'
  if (f.vendor === 'bambu') {
    if (!isSerial(f.serial)) e.serial = 'המספר הסידורי: 8 עד 20 אותיות וספרות, בלי רווחים'
    if (!isAccessCode(f.accessCode)) e.accessCode = 'קוד הגישה: בדיוק 8 תווים (ספרות או אותיות), כמו שמופיע במסך המדפסת'
    if (!isPort(f.mqttPort)) e.mqttPort = 'מספר בין 1 ל-65535'
    if (f.ftpHost && !isHost(f.ftpHost)) e.ftpHost = 'כתובת לא תקינה'
    if (!isPort(f.ftpPort)) e.ftpPort = 'מספר בין 1 ל-65535'
    if (f.cameraHost && !isHost(f.cameraHost)) e.cameraHost = 'כתובת לא תקינה'
    if (f.cameraPort && !isPort(f.cameraPort)) e.cameraPort = 'מספר בין 1 ל-65535'
    if (f.printHours.trim() && !/^\d{1,6}$/.test(f.printHours.trim())) e.printHours = 'מספר שלם של שעות (או ריק)'
  } else if (!isPort(f.port)) e.port = 'מספר בין 1 ל-65535'
  return e
}

/** The body of POST /api/v1/printers/test-connection. */
export function testRequest(f: PrinterForm): Record<string, unknown> {
  const host = f.host.trim()
  if (f.vendor === 'bambu') {
    const body: Record<string, unknown> = {
      vendor: 'bambu', host, model: f.model, serial: f.serial.trim(), access_code: f.accessCode.trim(),
      mqtt_port: Number(f.mqttPort), tls: f.tls, ftp_port: Number(f.ftpPort),
    }
    if (f.ftpHost.trim()) body.ftp_host = f.ftpHost.trim()
    if (f.cameraHost.trim()) body.camera_host = f.cameraHost.trim()
    if (f.cameraPort.trim()) body.camera_port = Number(f.cameraPort)
    return body
  }
  const body: Record<string, unknown> = { vendor: f.vendor, host, port: Number(f.port) }
  if (f.vendor === 'moonraker' && f.apiKey.trim()) body.api_key = f.apiKey.trim()
  return body
}

export interface Check {
  key: string
  label: string
  ok: boolean
  detail: string
  optional: boolean
  ms: number
}
export interface TestResult {
  ok: boolean
  vendor: Vendor
  checks: Check[]
  tested_at: number
}

/** What each check means for a beginner, and what to do when it fails (by printer type where it differs). */
export function checkHelp(vendor: Vendor, key: string): { what: string; fix: string } | undefined {
  return (vendor === 'moonraker' ? MOONRAKER_HELP[key] : undefined) ?? CHECK_HELP[key]
}

const MOONRAKER_HELP: Record<string, { what: string; fix: string }> = {
  telemetry: { what: 'המדפסת שולחת מצב וטמפרטורות', fix: 'Moonraker עונה, אבל Klipper לא מוסר נתונים. פתחו את Mainsail או Fluidd ובדקו ש-Klipper במצב ready.' },
  camera: { what: 'המצלמה משדרת', fix: 'רשות: ההדפסה תעבוד גם בלי מצלמה. אם יש מצלמה, בדקו ש-Crowsnest מותקן ופועל.' },
  network: { what: 'הבקר עונה ברשת', fix: 'בדקו שהבקר (Raspberry Pi / BTT Pad) דולק, שהכתובת והפורט (7125) נכונים, ושהוא באותה רשת כמו המחשב הזה.' },
}

export const CHECK_HELP: Record<string, { what: string; fix: string }> = {
  network: { what: 'המדפסת עונה ברשת', fix: 'בדקו שהמדפסת דולקת, שהכתובת נכונה, ושהיא באותה רשת כמו המחשב הזה (לא ברשת אורחים).' },
  login: { what: 'קוד הגישה מתקבל', fix: 'העתיקו שוב את קוד הגישה מהמסך של המדפסת. הקוד משתנה כשמכבים ומדליקים את LAN Only Mode.' },
  telemetry: { what: 'המדפסת שולחת מצב וטמפרטורות', fix: 'בדקו את המספר הסידורי (Settings ➔ Device). בלי מספר נכון המדפסת לא שולחת לנו נתונים.' },
  upload: { what: 'אפשר לשלוח אליה קבצי הדפסה', fix: 'ודאו ש-LAN Only Mode דלוק. בלי זה פורט ההעלאה (990) סגור.' },
  camera: { what: 'המצלמה משדרת', fix: 'רשות: ההדפסה תעבוד גם בלי מצלמה. ב-X1, הדליקו גם LAN Mode Liveview.' },
  api: { what: 'Moonraker עונה', fix: 'בדקו שהבקר (Raspberry Pi / BTT Pad) דולק ומחובר, ושהפורט הוא 7125.' },
}

export const envName = (prefix: string, id: string) => `${prefix}_${id.toUpperCase().replace(/-/g, '_')}`

export interface Snippet {
  file: string
  what: string
  text: string
}

/** fleet.toml lines that depend on the model: A1-series files sit on the SD card and the model gives the bed, height
 *  and watts (fleet.toml `model`); an AMS takes the colours, a counter of past print hours seeds maintenance. */
function bambuModelLines(f: PrinterForm): string[] {
  const a1 = f.model === 'a1' || f.model === 'a1-mini'
  const slots = Number(f.amsSlots) || 0
  const hours = Number(f.printHours.trim()) || 0
  return [
    a1 ? 'print_url = "file:///sdcard/{file}"' : 'print_url = "ftp:///{file}"',
    ...(a1 ? [] : ['bed_mm = [256, 256, 256]']),
    'nozzle_mm = 0.4',
    ...(slots
      ? [`ams_slots = ${slots}`, `ams_filaments = ["${f.material}/${f.color}"]   # slot 1; add the other slots: "PLA/white", ...`]
      : [`material = "${f.material}"`, `color = "${f.color}"`]),
    ...(hours ? [`initial_print_hours = ${hours}`] : []),
  ]
}

/** The lines to paste, file by file, for a printer that passed the test. */
export function snippets(f: PrinterForm): Snippet[] {
  const id = f.id
  const host = f.host.trim()
  if (f.vendor === 'bambu') {
    const code = envName('BAMBU_CODE', id)
    const out: Snippet[] = [
      {
        file: 'config/fleet.toml',
        what: 'הוסיפו בסוף רשימת המדפסות',
        text: [
          '[[printers]]',
          `id = "${id}"`,
          'vendor = "bambu"',
          `model = "${f.model}"`,
          `host = "${host}"`,
          'port = 8883',
          'tls = true',
          `serial = "${f.serial.trim()}"`,
          `access_code_env = "${code}"`,
          'ftp_port = 990',
          ...bambuModelLines(f),
        ].join('\n'),
      },
      {
        file: 'cameras.env',
        what: f.model === 'x1' ? 'קוד הגישה (הקובץ נשאר רק במחשב הזה)' : 'קוד הגישה והמצלמה (אם כבר יש שורת BAMBU_CAMS, הוסיפו אליה בפסיק)',
        text: [
          `${code}=${f.accessCode.trim()}`,
          ...(f.model === 'x1' ? [] : [`BAMBU_CAMS=${id}=${host}`]),
        ].join('\n'),
      },
      {
        file: 'infra/go2rtc/go2rtc.yaml',
        what: 'המצלמה בדשבורד: תחת streams',
        text:
          f.model === 'x1'
            ? [`  ${id}:`, `    - rtspx://bblp:\${${code}}@${host}:322/streaming/live/1`, `    - ffmpeg:${id}#video=mjpeg#width=960`].join('\n')
            : `  ${id}: http://bambu-cam:8090/${id}.mjpeg`,
      },
    ]
    return out
  }
  if (f.vendor === 'moonraker') {
    const key = envName('MOONRAKER_KEY', id)
    const out: Snippet[] = [
      {
        file: 'config/fleet.toml',
        what: 'הוסיפו בסוף רשימת המדפסות',
        text: [
          '[[printers]]',
          `id = "${id}"`,
          'vendor = "moonraker"',
          'model = "klipper"',
          `url = "ws://${host}:${Number(f.port)}/websocket"`,
          `api_key_env = "${f.apiKey.trim() ? key : ''}"`,
          'bed_mm = [300, 300]',
          'nozzle_mm = 0.4',
          `material = "${f.material}"`,
          `color = "${f.color}"`,
        ].join('\n'),
      },
    ]
    if (f.apiKey.trim()) out.push({ file: 'cameras.env', what: 'מפתח ה-API של Moonraker', text: `${key}=${f.apiKey.trim()}` })
    out.push({ file: 'infra/go2rtc/go2rtc.yaml', what: 'המצלמה בדשבורד: תחת streams', text: `  ${id}: http://${host}/webcam/?action=stream` })
    return out
  }
  return []
}

/** The commands that make the stack read the new files (README steps 11-12). */
export function applyCommands(f: PrinterForm): string[] {
  const p1 = f.vendor === 'bambu' && f.model !== 'x1'
  return [
    p1 ? 'docker compose --profile p1a1 up -d' : 'docker compose up -d',
    'docker compose restart telemetry fleet go2rtc',
  ]
}
