// סיור מודרך (guided tour): the steps, written for a new operator who knows nothing about 3D printing, and the pure
// geometry that places the speech bubble next to the highlighted element. The overlay is components/tour/TourOverlay.vue,
// the state is stores/tour.ts.

export interface TourStep {
  id: string
  /** The screen the step is shown on. */
  route: string
  /** CSS selectors tried in order; the first visible match is highlighted. None: a centred bubble with no spotlight. */
  targets: string[]
  title: string
  /** What the operator sees, in plain words. */
  body: string[]
  /** Why it matters. */
  why?: string
  /** Shown instead of the spotlight when the element is not on the screen right now. */
  missing?: string
  /** Open the sidebar on narrow screens (it slides in from the side there). */
  openNav?: boolean
}

export const TOUR_STEPS: TourStep[] = [
  {
    id: 'welcome',
    route: '/help',
    targets: [],
    title: 'ברוכים הבאים ל-PrintFarm Ops 👋',
    body: [
      'בסיור קצר (כ-2 דקות) נעבור יחד על המסכים החשובים: נראה איפה כל דבר נמצא ומה עושים בו.',
      'לא צריך לדעת כלום על הדפסה בתלת-ממד. המערכת עושה את רוב העבודה לבד, ואתם בודקים, מאשרים, ומטפלים כשמשהו מבקש עזרה.',
    ],
    why: 'במהלך הסיור הכפתורים נעולים, כך שאי אפשר לשנות כלום בטעות. עוברים בין השלבים עם **הבא** ו**הקודם**, ויוצאים עם **Esc**.',
  },
  {
    id: 'sidebar',
    route: '/live',
    targets: ['[data-tour="sidebar"]'],
    openNav: true,
    title: 'התפריט: ארבעה אזורים',
    body: [
      '**Sell** (מכירה): פוסטים של לקוחות מהרשתות החברתיות, וקטלוג המוצרים.',
      '**Make** (ייצור): המדפסות, ההזמנות והפילמנט (חוט הפלסטיק שממנו מדפיסים).',
      '**Business** (עסק): הכנסות, הוצאות ומע״מ.',
      '**Admin** (ניהול): בריאות המערכת וההגדרות.',
    ],
    why: 'מספר אדום ליד שם של מסך אומר שמשהו שם מחכה לכם. ככה יודעים לאן ללכת קודם.',
  },
  {
    id: 'live',
    route: '/live',
    targets: ['[data-tour="live-indicator"]'],
    title: 'החיבור החי',
    body: [
      '**Live · 0.4 s ago** אומר שהמסך מחובר ומתעדכן לבד, בלי לרענן את הדף. המספר הוא כמה זמן עבר מהעדכון האחרון.',
      'ירוק: הכול תקין. צהוב או אדום: החיבור נפל, והנתונים על המסך כבר לא עדכניים.',
    ],
    why: 'לפני שמחליטים משהו לפי המסך, מוודאים שהוא ירוק. אחרת אולי מסתכלים על מצב ישן.',
  },
  {
    id: 'pause-all',
    route: '/live',
    targets: ['[data-testid="pause-all"]'],
    title: 'כפתור החירום: Pause all printers',
    body: [
      'הכפתור עוצר בבת אחת את כל המדפסות שמדפיסות עכשיו. העצירה זמנית (השהיה): ההדפסה לא נמחקת, ואפשר להמשיך אותה אחר כך מאותה נקודה.',
      'לפני העצירה המערכת מבקשת אישור, ואחריה בודקת שכל מדפסת אכן נעצרה.',
    ],
    why: 'לחצו כשמשהו נראה לא בסדר: ריח של שריפה, רעש חריג, פלסטיק שנשפך כמו "ספגטי", או כשצריך לעזוב את החדר בפתאומיות. עדיף לעצור לשווא מאשר להרוס הדפסה או מדפסת.',
  },
  {
    id: 'printer-tile',
    route: '/live',
    targets: [
      '[data-tour="printer-tile"]:has(button[aria-label^="Bed of"])',
      '[data-tour="printer-tile"]:has(button[aria-label^="Pause "])',
      '[data-tour="printer-tile"]',
    ],
    title: 'כרטיס של מדפסת',
    body: [
      'לכל מדפסת יש כרטיס: מה היא עושה עכשיו, החום של **Nozzle** (הראש שמתיך את הפלסטיק) ושל **Bed** (המשטח שעליו מדפיסים), וכמה אחוזים מההדפסה כבר הסתיימו.',
      '**Pause**: השהיית ההדפסה של המדפסת הזאת בלבד. **Resume**: המשך ההדפסה מאותה נקודה.',
      '**Bed cleared**: מופיע כשהדפסה הסתיימה. הוציאו את המוצר מהמשטח, ודאו שהמשטח ריק ונקי, ורק אז לחצו.',
    ],
    why: 'המדפסת לא יודעת לבד אם המשטח ריק. אם היא תתחיל הדפסה חדשה מעל מוצר שנשאר עליו, הראש ייתקע בו. לכן היא מחכה ל-**Bed cleared**.',
    missing: 'אין כרגע מדפסת על המסך. כשמדפסות מחוברות, לכל אחת יש כאן כרטיס.',
  },
  {
    id: 'orders',
    route: '/orders',
    targets: ['[data-tour="order-board"]'],
    title: 'לוח ההזמנות: Order Pipeline',
    body: [
      'כל הזמנה היא כרטיס שזז משמאל לימין, טור אחרי טור: פנייה ← התקבלה ← מכינים את הקובץ ← משבצים למדפסת ← מדפיסים ← בדיקת איכות (QC) ← נשלחה.',
      'מעבירים הזמנה לשלב הבא בגרירה של הכרטיס לטור הבא. לחיצה על כרטיס פותחת את כל הפרטים שלו.',
      'הזמנה שנתקעה יותר משעתיים מסומנת, כדי שתשימו לב אליה.',
    ],
    why: 'ככה רואים במבט אחד מה מחכה לכם: הזמנה חדשה לאישור, מוצר לבדיקה, או חבילה לשליחה.',
    missing: 'הלוח נטען. אפשר לראות אותו גם במסך **Order Pipeline** שבתפריט.',
  },
  {
    id: 'catalog',
    route: '/catalog',
    targets: ['[data-testid="import-3mf"]'],
    title: 'קטלוג המוצרים: Instant Catalog',
    body: [
      'כאן נמצאים המוצרים שהחווה יודעת להדפיס. כל מוצר הוא קובץ **3MF**: קובץ מוכן שאומר למדפסת בדיוק מה ואיך להדפיס.',
      '**Import 3MF** מוסיף מוצר חדש. אחר כך אפשר לבנות **Batch** (סדרה): לבחור מוצרים וכמויות ולשלוח להדפסה למלאי.',
    ],
    why: 'מוצר שנמצא בקטלוג אפשר להזמין ולהדפיס בלי להכין כלום מחדש.',
  },
  {
    id: 'social',
    route: '/social',
    targets: ['[data-testid="approve-copy"]', '[data-testid="review"]'],
    title: 'תור האישור: Approve & Copy',
    body: [
      'המערכת קוראת פוסטים שאנשי הצוות שמרו מקבוצות ברשתות החברתיות, ומזהה אנשים שרוצים לקנות. לכל אחד כזה היא כותבת טיוטה של תגובה.',
      'קראו את הטיוטה ותקנו אותה אם צריך. **Approve & Copy** מאשר אותה ומעתיק אותה. עכשיו פתחו את הפוסט, הדביקו את התגובה ופרסמו אותה בעצמכם.',
      'בסוף לחצו **Mark as posted**, כדי שהמערכת תדע שהתגובה פורסמה.',
    ],
    why: 'המערכת אף פעם לא מפרסמת לבד. אדם תמיד קורא ומאשר כל תגובה. ככה התגובות נשמעות אנושיות, נכונות, ומכבדות את כללי הקבוצה.',
    missing: 'עוד אין פוסטים שמחכים לאישור. כשיגיע פוסט של קונה, הטיוטה שלו תופיע כאן.',
  },
  {
    id: 'done',
    route: '/help',
    targets: ['[data-testid="tour-start"]'],
    title: 'סיימתם! 🎉',
    body: [
      'זה הבסיס. בכל רגע אפשר לחזור לכאן דרך **User guide · מדריך למשתמש** שבתחתית התפריט.',
      'בדף הזה יש גם את המדריך המלא, עם הסבר על כל מסך, וטבלה של בעיות נפוצות ומה עושים בהן.',
    ],
    why: 'הכפתור הזה מתחיל את הסיור מחדש, מתי שתרצו.',
  },
]

/** `**bold**` -> <strong>; everything else escaped (the steps are ours, but keep it safe anyway). */
export function richText(s: string): string {
  const esc = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return esc.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}

export interface Rect {
  top: number
  left: number
  width: number
  height: number
}

export type Side = 'bottom' | 'top' | 'left' | 'right' | 'center'

export interface Placement {
  top: number
  left: number
  side: Side
}

const GAP = 14
const MARGIN = 12

/**
 * Where the bubble goes: below the element, else above, else beside it (either side), else over it at the bottom of the
 * screen. Always kept inside the viewport.
 */
export function placeBubble(target: Rect | null, bubble: { width: number; height: number }, view: { width: number; height: number }): Placement {
  const clampX = (x: number) => Math.max(MARGIN, Math.min(x, view.width - bubble.width - MARGIN))
  const clampY = (y: number) => Math.max(MARGIN, Math.min(y, view.height - bubble.height - MARGIN))
  if (!target) return { top: clampY((view.height - bubble.height) / 2), left: clampX((view.width - bubble.width) / 2), side: 'center' }

  const cx = target.left + target.width / 2
  const cy = target.top + target.height / 2
  const below = view.height - (target.top + target.height) - GAP
  const above = target.top - GAP
  const right = view.width - (target.left + target.width) - GAP
  const left = target.left - GAP

  if (below >= bubble.height + MARGIN) return { top: clampY(target.top + target.height + GAP), left: clampX(cx - bubble.width / 2), side: 'bottom' }
  if (above >= bubble.height + MARGIN) return { top: clampY(target.top - GAP - bubble.height), left: clampX(cx - bubble.width / 2), side: 'top' }
  if (right >= bubble.width + MARGIN) return { top: clampY(cy - bubble.height / 2), left: target.left + target.width + GAP, side: 'right' }
  if (left >= bubble.width + MARGIN) return { top: clampY(cy - bubble.height / 2), left: target.left - GAP - bubble.width, side: 'left' }
  return { top: clampY(view.height - bubble.height - MARGIN), left: clampX(cx - bubble.width / 2), side: 'center' }
}

/** The first selector with a visible element (a hidden sidebar or a zero-size node does not count). */
export function findTarget(selectors: string[], root: ParentNode = document): HTMLElement | null {
  for (const sel of selectors) {
    let nodes: NodeListOf<HTMLElement>
    try {
      nodes = root.querySelectorAll<HTMLElement>(sel)
    } catch {
      continue // a selector the browser does not support (e.g. :has in an old engine)
    }
    for (const el of nodes) {
      const r = el.getBoundingClientRect()
      if (r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden') return el
    }
  }
  return null
}
