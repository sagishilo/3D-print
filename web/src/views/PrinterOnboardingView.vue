<script setup lang="ts">
// חיבור מדפסת חדשה: a step-by-step guide for connecting a real printer, by printer type, with a live connection test
// (gateway: POST /api/v1/printers/test-connection, nothing stored) and the exact lines to paste into the stack's files.
import { computed, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import Icon from '@/components/ui/Icon.vue'
import { api, errorText } from '@/lib/api'
import {
  applyCommands,
  checkHelp,
  emptyForm,
  type Errors,
  type PrinterForm,
  simulatorForm,
  snippets,
  testRequest,
  type TestResult,
  validate,
  type Vendor,
  VENDORS,
} from '@/lib/onboarding'
import { copyText } from '@/lib/social'
import { useToasts } from '@/stores/toasts'

const toasts = useToasts()

const vendor = ref<Vendor | null>(null)
const step = ref(0)
const form = reactive<PrinterForm>(emptyForm('bambu'))
const touched = reactive<Partial<Record<keyof PrinterForm, boolean>>>({})
const tried = ref(false)
const advanced = ref(false)
const testing = ref(false)
const result = ref<TestResult | null>(null)
const testError = ref('')

const STEPS: Record<Vendor, string[]> = {
  bambu: ['סוג המדפסת', 'LAN Only Mode', 'כתובת וקוד גישה', 'חיבור ובדיקה', 'הוספה לחווה'],
  moonraker: ['סוג המדפסת', 'חיבור הבקר לרשת', 'כתובת Moonraker', 'חיבור ובדיקה', 'הוספה לחווה'],
  generic: ['סוג המדפסת', 'מה אפשר לעשות', 'חיבור ובדיקה', 'מה הלאה'],
}
const steps = computed(() => STEPS[vendor.value ?? 'bambu'])
const testStep = computed(() => steps.value.indexOf('חיבור ובדיקה'))
const lastStep = computed(() => steps.value.length - 1)

const errors = computed<Errors>(() => validate(form))
const valid = computed(() => Object.keys(errors.value).length === 0)
const shown = (k: keyof PrinterForm) => (tried.value || touched[k] ? errors.value[k] : undefined)
const out = computed(() => snippets(form))
const commands = computed(() => applyCommands(form))
const canNext = computed(() => step.value !== testStep.value || !!result.value?.ok)
// The built-in simulator (practice) hosts: its lines are an example only.
const practice = computed(() => ['mosquitto', 'simulator'].includes(form.host.trim()))

function choose(v: Vendor) {
  vendor.value = v
  Object.assign(form, emptyForm(v))
  for (const k of Object.keys(touched)) delete touched[k as keyof PrinterForm]
  tried.value = false
  advanced.value = false
  result.value = null
  step.value = 1
}

function practise() {
  if (!vendor.value || vendor.value === 'generic') return
  Object.assign(form, simulatorForm(vendor.value))
  advanced.value = vendor.value === 'bambu'
}

// Any change to the details makes the last test result stale.
watch(form, () => {
  result.value = null
  testError.value = ''
})

async function runTest() {
  tried.value = true
  if (!valid.value) return
  testing.value = true
  testError.value = ''
  result.value = null
  const body = testRequest(form)
  try {
    const r = await api<TestResult>('/api/v1/printers/test-connection', { method: 'POST', body })
    // Only show it if the details did not change meanwhile.
    if (JSON.stringify(testRequest(form)) === JSON.stringify(body)) result.value = r
  } catch (err) {
    testError.value = errorText(err)
  } finally {
    testing.value = false
  }
}

async function copy(text: string, what: string) {
  if (await copyText(text)) toasts.ok('הועתק', what)
  else toasts.error('ההעתקה נכשלה', 'סמנו את הטקסט והעתיקו ידנית (Ctrl+C).')
}

const go = (n: number) => {
  if (n <= step.value || canNext.value) step.value = n
}
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Connect a new printer</h1>
      <p lang="he" dir="rtl" class="he-sub">חיבור מדפסת חדשה: צעד אחרי צעד, עם בדיקת חיבור אמיתית לפני שמוסיפים אותה לחווה</p>
    </div>
    <div class="head-actions">
      <RouterLink to="/fleet" class="btn" data-testid="back-to-fleet"><Icon name="monitor" :size="14" />Printer Fleet</RouterLink>
    </div>
  </div>

  <div class="hub" dir="rtl" lang="he">
    <!-- the steps -->
    <ol v-if="vendor" class="stepper" aria-label="שלבים" data-tour="onboarding-steps">
      <li v-for="(s, i) in steps" :key="s" :class="{ on: i === step, done: i < step }" :data-testid="`onboarding-step-${i}`">
        <button type="button" :disabled="i > step && !(i === step + 1 && canNext)" :aria-current="i === step ? 'step' : undefined" @click="i === 0 ? (vendor = null, step = 0) : go(i)">
          <span class="n">{{ i < step ? '✓' : i + 1 }}</span><span class="t">{{ s }}</span>
        </button>
      </li>
    </ol>

    <!-- 0. which printer -->
    <section v-if="!vendor" class="panel" data-tour="onboarding-vendors">
      <h2>איזו מדפסת מחברים?</h2>
      <p class="muted">בחרו את סוג המדפסת. ההוראות והבדיקה מותאמות לכל סוג.</p>
      <div class="vendors">
        <button v-for="v in VENDORS" :key="v.id" type="button" class="vendor" :data-testid="`vendor-${v.id}`" @click="choose(v.id)">
          <span class="logo" :class="v.id" aria-hidden="true">{{ v.id === 'bambu' ? 'B' : v.id === 'moonraker' ? 'K' : '?' }}</span>
          <b dir="ltr">{{ v.name }}</b>
          <span class="models" dir="ltr">{{ v.models }}</span>
          <span class="text">{{ v.text }}</span>
        </button>
      </div>
      <p class="note"><Icon name="alert" :size="14" />הדשבורד בודק את החיבור בלבד, ולא שומר את קוד הגישה. בסוף מקבלים את השורות המדויקות להוסיף לקבצי ההגדרות, כך שהקודים נשארים רק במחשב הזה.</p>
    </section>

    <!-- Bambu Lab -->
    <template v-else-if="vendor === 'bambu'">
      <section v-if="step === 1" class="panel guide" data-testid="guide-step">
        <h2>שלב 1: מדליקים LAN Only Mode במדפסת</h2>
        <p>במצב הזה המדפסת מדברת ישירות עם המחשב הזה ברשת המקומית, בלי לעבור דרך הענן של Bambu.</p>
        <ol>
          <li>במסך המגע של המדפסת, לוחצים על <b dir="ltr">Settings</b> (גלגל השיניים).</li>
          <li>נכנסים ל-<b dir="ltr">LAN Only Mode</b> (ב-P1 וב-A1 הוא בתוך <b dir="ltr">Network</b> / <b dir="ltr">WLAN</b>) ומדליקים אותו.</li>
          <li>ב-X1: מדליקים שם גם <b dir="ltr">LAN Mode Liveview</b>, בשביל המצלמה.</li>
          <li>אם מופיע גם <b dir="ltr">Developer Mode</b>, מדליקים אותו: בלעדיו גרסאות תוכנה חדשות לא מקבלות פקודות (השהיה, התחלה) מתוכנות אחרות.</li>
        </ol>
        <p class="why"><b>למה זה חשוב?</b> בלי LAN Only Mode הפורטים של המדפסת סגורים לחווה. שימו לב: במצב הזה אפליקציית Bambu Handy והענן לא עובדים מול המדפסת.</p>
      </section>

      <section v-else-if="step === 2" class="panel guide" data-testid="guide-step">
        <h2>שלב 2: מוצאים את כתובת ה-IP, קוד הגישה והמספר הסידורי</h2>
        <ol>
          <li><b>כתובת IP</b>: באותו מסך של <b dir="ltr">LAN Only Mode</b> (או <b dir="ltr">Settings ➔ Network</b>). נראית כמו <span class="mono" dir="ltr">192.168.1.50</span>.</li>
          <li><b>קוד גישה</b> (<b dir="ltr">Access Code</b>): 8 תווים, מופיע ליד LAN Only Mode. הקוד משתנה בכל פעם שמכבים ומדליקים את המצב הזה.</li>
          <li><b>מספר סידורי</b> (<b dir="ltr">Serial</b>): ב-<b dir="ltr">Settings ➔ Device</b>, או על המדבקה בגב המדפסת.</li>
          <li>בנתב, תנו למדפסת <b>כתובת קבועה</b> (DHCP reservation), כדי שהכתובת לא תשתנה מחר (README, שלב 10).</li>
        </ol>
        <p class="why"><b>למה זה חשוב?</b> החווה מתחברת למדפסת לפי הכתובת, ונכנסת עם קוד הגישה. כתובת שמשתנה = מדפסת "נעלמת".</p>
      </section>
    </template>

    <!-- Klipper / Voron -->
    <template v-else-if="vendor === 'moonraker'">
      <section v-if="step === 1" class="panel guide" data-testid="guide-step">
        <h2>שלב 1: מחברים את הבקר לנתב</h2>
        <p>במדפסות Klipper יש "מחשב קטן" שמנהל אותן: Raspberry Pi, BTT Pad או דומה. אליו החווה מתחברת.</p>
        <ol>
          <li>מחברים את הבקר לנתב <b>בכבל רשת</b> (הכי יציב), או מגדירים לו Wi-Fi.</li>
          <li>בנתב, נותנים לבקר <b>כתובת קבועה</b> (DHCP reservation, README שלב 10).</li>
          <li>מוודאים ש-<b dir="ltr">Moonraker</b> מותקן. אם יש לכם <b dir="ltr">Mainsail</b> או <b dir="ltr">Fluidd</b>, הוא כבר שם.</li>
        </ol>
        <p class="why"><b>למה זה חשוב?</b> חיבור Wi-Fi חלש גורם לניתוקים באמצע הדפסה. כבל רשת פותר את זה.</p>
      </section>

      <section v-else-if="step === 2" class="panel guide" data-testid="guide-step">
        <h2>שלב 2: מוצאים את כתובת Moonraker</h2>
        <ol>
          <li>הדרך הקלה: אם אתם נכנסים ל-Mainsail או Fluidd בכתובת כמו <span class="mono" dir="ltr">http://192.168.1.60</span>, זו הכתובת.</li>
          <li>או: ברשימת המכשירים של הנתב, או ב-<b dir="ltr">KlipperScreen ➔ Network</b>.</li>
          <li>הפורט של <b dir="ltr">Moonraker</b> הוא בדרך כלל <span class="mono" dir="ltr">7125</span>.</li>
          <li>אם Moonraker מוגדר לדרוש מפתח (<b dir="ltr">API key</b>), תצטרכו אותו בשלב הבא. ברוב ההתקנות לא צריך.</li>
        </ol>
        <p class="why"><b>למה זה חשוב?</b> דרך Moonraker החווה רואה טמפרטורות והתקדמות, שולחת קבצים, ומשהה הדפסות.</p>
      </section>
    </template>

    <!-- Generic -->
    <template v-else>
      <section v-if="step === 1" class="panel guide" data-testid="guide-step">
        <h2>מה אפשר לעשות עם מדפסת אחרת</h2>
        <p>החווה יודעת לשלוט היום רק במדפסות <b dir="ltr">Bambu Lab</b> ו-<b dir="ltr">Klipper</b>. מדפסת אחרת (למשל Prusa, או Creality עם התוכנה המקורית) עוד לא יכולה לקבל עבודות מהחווה.</p>
        <ol>
          <li>אם המדפסת מריצה Klipper (למשל Creality K1 אחרי שדרוג, או Sovol SV08), חזרו ובחרו <b dir="ltr">Klipper / Voron</b>.</li>
          <li>אחרת, אפשר לבדוק כאן שהיא עונה ברשת, כהכנה לתמיכה בעתיד.</li>
        </ol>
        <p class="why"><b>למה זה חשוב?</b> כדי לא לבזבז זמן: מדפסת שהחווה לא יודעת לשלוט בה לא תקבל הדפסות, גם אם היא מחוברת.</p>
      </section>
    </template>

    <!-- connect & test (every type) -->
    <section v-if="vendor && step === testStep" class="panel" data-testid="connect-step" data-tour="onboarding-form">
      <div class="panel-head">
        <h2>{{ vendor === 'generic' ? 'שלב 2' : 'שלב 3' }}: פרטי המדפסת ובדיקת חיבור</h2>
        <button v-if="vendor !== 'generic'" type="button" class="btn btn-sm" data-testid="use-simulator" @click="practise">
          תרגול: מלאו מדפסת מדומה
        </button>
      </div>

      <form class="form" novalidate @submit.prevent="runTest">
        <label>
          <span>שם המדפסת בחווה</span>
          <input v-model.trim="form.id" class="input mono" dir="ltr" placeholder="p1s-01" data-testid="f-id" @blur="touched.id = true" />
          <small v-if="shown('id')" class="err" role="alert">{{ shown('id') }}</small>
          <small v-else class="hint">אותיות קטנות באנגלית, ספרות ו-"-". יופיע בכל מסך.</small>
        </label>

        <label v-if="vendor === 'bambu'">
          <span>דגם</span>
          <select v-model="form.model" class="select" dir="ltr" data-testid="f-model">
            <option value="x1">X1 series (X1C, X1E)</option>
            <option value="p1">P1 series (P1S, P1P)</option>
            <option value="a1">A1 series (A1, A1 mini)</option>
          </select>
        </label>

        <label>
          <span>כתובת IP</span>
          <input v-model.trim="form.host" class="input mono" dir="ltr" inputmode="decimal" placeholder="192.168.1.50" data-testid="f-host" @blur="touched.host = true" />
          <small v-if="shown('host')" class="err" role="alert">{{ shown('host') }}</small>
        </label>

        <template v-if="vendor === 'bambu'">
          <label>
            <span>מספר סידורי (<span dir="ltr">Serial</span>)</span>
            <input v-model.trim="form.serial" class="input mono" dir="ltr" placeholder="01P00A000000000" data-testid="f-serial" @blur="touched.serial = true" />
            <small v-if="shown('serial')" class="err" role="alert">{{ shown('serial') }}</small>
          </label>
          <label>
            <span>קוד גישה (<span dir="ltr">Access Code</span>)</span>
            <input v-model.trim="form.accessCode" class="input mono" dir="ltr" maxlength="8" autocomplete="off" spellcheck="false" placeholder="8 תווים" data-testid="f-code" @blur="touched.accessCode = true" />
            <small v-if="shown('accessCode')" class="err" role="alert">{{ shown('accessCode') }}</small>
            <small v-else class="hint">נשלח רק לבדיקה הזאת. לא נשמר ולא נרשם ביומן.</small>
          </label>
        </template>
        <template v-else>
          <label>
            <span>פורט</span>
            <input v-model.trim="form.port" class="input mono" dir="ltr" inputmode="numeric" data-testid="f-port" @blur="touched.port = true" />
            <small v-if="shown('port')" class="err" role="alert">{{ shown('port') }}</small>
          </label>
          <label v-if="vendor === 'moonraker'">
            <span>מפתח API (רשות)</span>
            <input v-model.trim="form.apiKey" class="input mono" dir="ltr" autocomplete="off" data-testid="f-apikey" />
          </label>
        </template>

        <details v-if="vendor === 'bambu'" class="advanced" :open="advanced" @toggle="advanced = ($event.target as HTMLDetailsElement).open">
          <summary data-testid="advanced">מתקדם: פורטים אחרים או מדפסת מדומה</summary>
          <div class="grid">
            <label><span>פורט MQTT</span><input v-model.trim="form.mqttPort" class="input mono" dir="ltr" data-testid="f-mqtt-port" /></label>
            <label class="check"><input v-model="form.tls" type="checkbox" data-testid="f-tls" /> <span>TLS (מדפסת אמיתית: כן)</span></label>
            <label><span>שרת העלאה (FTPS)</span><input v-model.trim="form.ftpHost" class="input mono" dir="ltr" placeholder="כמו כתובת המדפסת" data-testid="f-ftp-host" /></label>
            <label><span>פורט העלאה</span><input v-model.trim="form.ftpPort" class="input mono" dir="ltr" data-testid="f-ftp-port" /></label>
            <label><span>שרת מצלמה</span><input v-model.trim="form.cameraHost" class="input mono" dir="ltr" placeholder="כמו כתובת המדפסת" data-testid="f-cam-host" /></label>
            <label><span>פורט מצלמה</span><input v-model.trim="form.cameraPort" class="input mono" dir="ltr" :placeholder="form.model === 'x1' ? '322' : '6000'" data-testid="f-cam-port" /></label>
          </div>
        </details>

        <div class="test-row">
          <button type="submit" class="btn btn-primary" :disabled="testing" data-testid="test-connection">
            <Icon :name="testing ? 'refresh' : 'activity'" :size="14" :class="{ spin: testing }" />
            <span dir="ltr">{{ testing ? 'Testing…' : 'Test Connection' }}</span>
          </button>
          <span v-if="tried && !valid" class="err" role="alert" data-testid="form-invalid">יש שדות לתקן לפני הבדיקה.</span>
          <span v-else-if="testing" class="muted">בודקים את החיבור, את קוד הגישה, נתוני המדפסת ואת המצלמה (עד 15 שניות)…</span>
        </div>
      </form>

      <p v-if="testError" class="banner bad" role="alert" data-testid="test-error"><Icon name="alert" :size="16" />הבדיקה לא רצה: {{ testError }}</p>

      <div v-if="result" class="results" :class="result.ok ? 'pass' : 'fail'" data-testid="test-result" :data-ok="String(result.ok)">
        <p class="verdict" role="status">
          <Icon :name="result.ok ? 'check' : 'alert'" :size="18" />
          <b>{{ result.ok ? 'החיבור עובד! אפשר להמשיך להוספה לחווה.' : 'החיבור עוד לא עובד. התיקון מופיע ליד כל בדיקה שנכשלה.' }}</b>
        </p>
        <ul>
          <li v-for="c in result.checks" :key="c.key" :class="c.ok ? 'ok' : c.optional ? 'opt' : 'bad'" :data-testid="`check-${c.key}`" :data-ok="String(c.ok)">
            <span class="mark" aria-hidden="true">{{ c.ok ? '✓' : c.optional ? '!' : '✕' }}</span>
            <div>
              <b>{{ checkHelp(form.vendor, c.key)?.what ?? c.label }}</b>
              <span class="label" dir="ltr">{{ c.label }} · {{ c.detail }}</span>
              <span v-if="!c.ok" class="fix">{{ checkHelp(form.vendor, c.key)?.fix }}</span>
            </div>
          </li>
        </ul>
      </div>
    </section>

    <!-- add to the farm -->
    <section v-if="vendor && step === lastStep" class="panel" data-testid="add-step">
      <template v-if="vendor === 'generic'">
        <h2>מה הלאה</h2>
        <p>המדפסת {{ result?.ok ? 'עונה ברשת' : 'נבדקה' }}, אבל החווה עוד לא יודעת לשלוח לה עבודות. כשתתווסף תמיכה בסוג הזה, היא תופיע כאן.</p>
        <p class="muted">אם המדפסת יכולה להריץ Klipper, זה הפתרון המהיר: אחרי ההתקנה חוזרים לכאן ובוחרים <b dir="ltr">Klipper / Voron</b>.</p>
      </template>
      <template v-else>
        <h2>שלב 4: מוסיפים את <span dir="ltr" class="mono">{{ form.id }}</span> לחווה</h2>
        <p v-if="practice" class="practice" role="note" data-testid="practice-note">
          <Icon name="alert" :size="16" />זו הייתה מדפסת תרגול (מדומה): אל תוסיפו את השורות האלה לקבצים. ככה בדיוק ייראו השורות של מדפסת אמיתית.
        </p>
        <p>הבדיקה עברה. עכשיו מעתיקים את השורות האלה לקבצים, בתיקייה של המערכת במחשב הזה. כל חלון כאן הוא קובץ אחד.</p>
        <div v-for="(s, i) in out" :key="s.file" class="snippet" :data-testid="`snippet-${i}`">
          <div class="snip-head">
            <span><b class="mono" dir="ltr">{{ s.file }}</b> · {{ s.what }}</span>
            <button type="button" class="btn btn-sm" :data-testid="`copy-${i}`" @click="copy(s.text, s.file)"><Icon name="copy" :size="14" />העתקה</button>
          </div>
          <pre dir="ltr">{{ s.text }}</pre>
        </div>
        <div class="snippet" data-testid="snippet-commands">
          <div class="snip-head">
            <span><b>ואז, ב-PowerShell בתיקיית המערכת</b> · כדי שהמערכת תקרא את הקבצים החדשים</span>
            <button type="button" class="btn btn-sm" @click="copy(commands.join('\n'), 'הפקודות')"><Icon name="copy" :size="14" />העתקה</button>
          </div>
          <pre dir="ltr">{{ commands.join('\n') }}</pre>
        </div>
        <ul class="after">
          <li>במחשב שעובד עם מדפסות אמיתיות, בקובץ <span class="mono" dir="ltr">.env</span> צריכה להיות השורה <span class="mono" dir="ltr">FLEET_FILE=fleet.toml</span>.</li>
          <li>בתוך דקה המדפסת מופיעה במסך <RouterLink to="/fleet">Printer Fleet</RouterLink>. לפני הדפסה אמיתית ראשונה, נסו עליה <b dir="ltr">Pause</b> ו-<b dir="ltr">Resume</b>.</li>
          <li class="warn">את <span class="mono" dir="ltr">cameras.env</span> לא שולחים לאף אחד ולא מעלים ל-GitHub: יש בו את קוד הגישה.</li>
        </ul>
      </template>
    </section>

    <div v-if="vendor" class="nav">
      <button type="button" class="btn" data-testid="step-back" @click="step > 1 ? step-- : (vendor = null, step = 0)">הקודם</button>
      <button v-if="step < lastStep" type="button" class="btn btn-primary" :disabled="!canNext" data-testid="step-next" @click="go(step + 1)">
        {{ step === testStep && !result?.ok ? 'קודם צריך בדיקה שעוברת' : 'הבא' }}
      </button>
      <RouterLink v-else to="/fleet" class="btn btn-primary" data-testid="finish">סיום: למסך Printer Fleet</RouterLink>
    </div>
  </div>
</template>

<style scoped>
.he-sub {
  text-align: start;
}
.hub {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 920px;
  width: 100%;
  line-height: 1.65;
}
.stepper {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.stepper li {
  flex: 1 1 140px;
}
.stepper button {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--muted);
  font: 500 13px var(--font);
  cursor: pointer;
  text-align: start;
}
.stepper button:disabled {
  cursor: default;
  opacity: 0.6;
}
.stepper .n {
  width: 22px;
  height: 22px;
  flex: none;
  border-radius: 50%;
  border: 1px solid currentColor;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font: 700 11px var(--font);
}
.stepper .on button {
  color: var(--text);
  border-color: var(--accent);
  background: var(--accent-bg);
}
.stepper .on .n {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--bg);
}
.stepper .done button {
  color: var(--ok);
}
h2 {
  font: 700 18px var(--font-display);
  margin: 0 0 8px;
}
.panel-head h2 {
  margin: 0;
}
.vendors {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
  margin: 14px 0;
}
.vendor {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 18px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text);
  cursor: pointer;
  text-align: start;
  transition:
    border-color 0.15s,
    transform 0.15s;
}
.vendor:hover,
.vendor:focus-visible {
  border-color: var(--accent);
  transform: translateY(-2px);
}
.vendor b {
  font: 700 17px var(--font-display);
}
.logo {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font: 800 18px var(--font-display);
  margin-bottom: 6px;
  color: #fff;
}
.logo.bambu {
  background: #1fb35a;
}
.logo.moonraker {
  background: #c0392b;
}
.logo.generic {
  background: var(--idle);
}
.models {
  font: 500 12px var(--font-mono);
  color: var(--accent);
}
.vendor .text {
  font-size: 13px;
  color: var(--muted);
}
.note {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  font-size: 13px;
  color: var(--muted);
  margin: 0;
}
.guide ol {
  padding-inline-start: 22px;
  margin: 10px 0;
}
.guide li {
  margin: 6px 0;
}
.why {
  margin: 12px 0 0;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--accent-bg);
  font-size: 14px;
}
.mono {
  font-family: var(--font-mono);
  unicode-bidi: isolate;
}
.form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 14px 18px;
  margin-top: 12px;
}
.form label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}
.form label > span:first-child {
  font-weight: 600;
}
.form .input,
.form .select {
  text-align: left;
}
.hint {
  color: var(--muted);
}
.err {
  color: var(--danger);
}
.advanced {
  grid-column: 1 / -1;
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
}
.advanced summary {
  cursor: pointer;
  color: var(--muted);
  font-size: 13px;
}
.advanced .grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px 14px;
  margin-top: 10px;
}
.form .check {
  flex-direction: row;
  align-items: center;
  gap: 8px;
}
.test-row {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.spin {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.banner.bad {
  display: flex;
  gap: 8px;
  color: var(--danger);
  margin-top: 12px;
}
.results {
  margin-top: 14px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  padding: 12px 14px;
}
.results.pass {
  border-color: var(--ok);
  background: rgba(63, 185, 80, 0.06);
}
.results.fail {
  border-color: var(--danger);
  background: var(--danger-bg);
}
.verdict {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 0 0 8px;
}
.results ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.results li {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.results li div {
  display: flex;
  flex-direction: column;
}
.mark {
  width: 22px;
  height: 22px;
  flex: none;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font: 700 12px var(--font);
  color: var(--bg);
}
.ok .mark {
  background: var(--ok);
}
.bad .mark {
  background: var(--danger);
}
.opt .mark {
  background: var(--warn);
}
.results .label {
  font: 12px var(--font-mono);
  color: var(--muted);
  text-align: start;
  unicode-bidi: plaintext;
}
.fix {
  font-size: 13px;
  color: var(--warn);
}
.snippet {
  margin-top: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.snip-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: var(--surface-2);
  font-size: 13px;
  flex-wrap: wrap;
}
pre {
  margin: 0;
  padding: 10px 12px;
  font: 12.5px/1.6 var(--font-mono);
  overflow-x: auto;
  text-align: left;
  background: var(--bg);
}
.practice {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--warn-bg);
  color: var(--warn);
}
.after {
  margin: 14px 0 0;
  padding-inline-start: 22px;
  font-size: 14px;
}
.after .warn {
  color: var(--warn);
}
.nav {
  display: flex;
  gap: 8px;
  justify-content: flex-start;
}
@media (max-width: 600px) {
  .stepper .t {
    display: none;
  }
  .stepper li {
    flex: 1 1 0;
  }
  .stepper button {
    justify-content: center;
  }
}
</style>
