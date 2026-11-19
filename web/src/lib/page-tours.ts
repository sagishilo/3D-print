// הדרכות עמוד (page tours): a deep dive into one screen, started from the "מדריך לעמוד" button on that screen. They use
// the same overlay as the general tour (components/tour/TourOverlay.vue); this file holds their steps and the registry
// of every tour.
import { CATALOG, FILAMENT, FINANCE, HELP, NICHES, ONBOARDING, PRICING, SCORECARD, SOCIAL, SYSTEM, USERS } from './page-tours-more'
import { TOUR_STEPS, type TourAction, type TourStep } from './tour'

export type TourId = 'general' | 'live' | 'orders' | 'fleet' | 'social' | 'catalog' | 'filament' | 'finance' | 'system' | 'help' | 'onboarding' | 'users' | 'niches' | 'pricing' | 'scorecard'

export interface Tour {
  id: TourId
  /** Shown above each bubble. */
  title: string
  steps: TourStep[]
}

// ------------------------------------------------------------------------------------------------ Live floor
const LIVE: TourStep[] = [
  {
    id: 'live-intro',
    route: '/live',
    targets: ['[data-testid="page-tour-live"]'],
    title: 'מסך Live floor: רצפת הייצור',
    body: [
      'זה המסך הראשי. רואים בו את כל המדפסות בזמן אמת: מה כל אחת עושה, איך היא נראית במצלמה, ומה קרה לאחרונה.',
      'נעבור עכשיו על כל חלק במסך, מלמעלה למטה.',
    ],
    why: 'רוב היום תעבדו מהמסך הזה. כדאי להכיר אותו היטב.',
  },
  {
    id: 'live-connection',
    route: '/live',
    targets: ['[data-tour="live-indicator"]'],
    title: 'בריאות החיבור',
    body: [
      '**Live · 0.4 s ago**: המסך מקבל עדכונים חיים. המספר אומר לפני כמה זמן הגיע העדכון האחרון.',
      'ירוק: הכול תקין. צהוב: העדכונים מתעכבים. אדום: החיבור נותק, והמסך מנסה להתחבר מחדש לבד.',
    ],
    why: 'כשהחיבור לא ירוק, המספרים והמצלמות על המסך עלולים להיות ישנים. לפני פעולה חשובה, ודאו שהוא ירוק.',
  },
  {
    id: 'live-pause-all',
    route: '/live',
    targets: ['[data-testid="pause-all"]'],
    title: 'Pause all printers: עצירת חירום',
    body: [
      'עוצר בבת אחת כל מדפסת שמדפיסה או מתחממת. זו השהיה: ההדפסה לא נמחקת, וממשיכים אותה אחר כך עם **Resume**.',
      'לפני העצירה נפתח חלון אישור. אחרי העצירה המערכת בודקת שכל מדפסת אכן נעצרה, ומתריעה על מדפסת שלא.',
    ],
    why: 'ריח שרוף, רעש חריג, פלסטיק שנשפך? לחצו קודם ותבדקו אחר כך. השהיה מיותרת עולה כמה דקות; הדפסה שהשתבשה יכולה לעלות מדפסת.',
  },
  {
    id: 'live-counts',
    route: '/live',
    targets: ['[data-tour="state-counts"]'],
    title: 'כמה מדפסות בכל מצב',
    body: [
      'סיכום מהיר: כמה מדפסות מדפיסות (**printing**), מחכות (**idle**), סיימו (**finished**), נכשלו (**failed**) או מנותקות (**offline**).',
    ],
    why: 'מבט אחד כאן אומר אם יש משהו שמחכה לכם, למשל מדפסת שסיימה וצריך לפנות אותה.',
  },
  {
    id: 'live-tile',
    route: '/live',
    targets: ['[data-tour="printer-tile"]'],
    title: 'כרטיס מדפסת',
    body: [
      'לכל מדפסת יש כרטיס. למעלה: השם שלה, והמצב שלה בתווית צבעונית (ירוק: מדפיסה, כחול: סיימה, אדום: תקלה).',
      'מתחת לשם כתוב מה היא מדפיסה עכשיו, ואיזה פילמנט (חוט פלסטיק) טעון בה, בחומר ובצבע.',
    ],
    why: 'ככה יודעים מה כל מדפסת עושה, בלי ללכת אליה.',
    missing: 'אין כרגע מדפסות על המסך. כשהן מחוברות, לכל אחת יש כרטיס.',
  },
  {
    id: 'live-camera',
    route: '/live',
    targets: ['[data-tour="printer-tile"] [data-tour="camera"]'],
    title: 'המצלמה החיה',
    body: [
      'תמונה חיה מתוך המדפסת. בפינה: **LIVE** (משדרת), **CONNECTING** (מתחברת), או הודעה שהמצלמה לא זמינה. לידה כתוב לפני כמה זמן הגיעה התמונה.',
      'כפתור המצלמה הקטן בפינה שומר תמונה (snapshot) של הרגע הזה.',
    ],
    why: 'במצלמה רואים בעיה לפני שהיא גדלה: הדפסה שהתנתקה מהמשטח, או "ספגטי" של פלסטיק. אם רואים כזה, עושים **Pause**.',
    missing: 'למדפסת הזאת אין מצלמה מוגדרת.',
  },
  {
    id: 'live-temps',
    route: '/live',
    targets: ['[data-tour="printer-tile"] [data-tour="tile-temps"]'],
    title: 'טמפרטורות',
    body: [
      '**Nozzle**: הראש שמתיך את הפלסטיק. **Bed**: המשטח שעליו מדפיסים. המספר הראשון הוא החום עכשיו, והשני הוא החום שהמדפסת מכוונת אליו.',
      'בזמן חימום המספר הראשון עולה עד שהוא מגיע לשני, ואז ההדפסה מתחילה.',
    ],
    why: 'חום שלא מגיע למטרה, או קופץ ויורד, הוא סימן לתקלה. אל תיגעו בראש או במשטח: הם יכולים להגיע ליותר מ-200 מעלות.',
  },
  {
    id: 'live-progress',
    route: '/live',
    targets: ['[data-tour="printer-tile"] [data-tour="tile-progress"]'],
    title: 'התקדמות ההדפסה',
    body: [
      'כמה אחוזים כבר הודפסו, ובאיזו שכבה המדפסת נמצאת: ההדפסה נבנית שכבה דקה אחרי שכבה, מלמטה למעלה.',
      '**ETA**: כמה זמן נשאר עד הסוף.',
    ],
    why: 'לפי ה-ETA מתכננים מתי לחזור לפנות את המשטח ולהתחיל את ההדפסה הבאה.',
  },
  {
    id: 'live-actions',
    route: '/live',
    targets: [
      '[data-tour="printer-tile"]:has([data-tour="tile-pause"]) [data-tour="tile-actions"]',
      '[data-tour="printer-tile"]:has([data-tour="tile-resume"]) [data-tour="tile-actions"]',
      '[data-tour="printer-tile"] [data-tour="tile-actions"]',
    ],
    title: 'הכפתורים של המדפסת',
    body: [
      'הכפתורים משתנים לפי המצב, ורואים רק את מה שאפשר לעשות עכשיו:',
      '**Pause**: השהיית המדפסת הזאת בלבד. **Resume**: המשך מאותה נקודה בדיוק.',
      '**Cancel**: ביטול ההדפסה עד הסוף. אי אפשר להמשיך אותה אחר כך. (מופיע במסך **Printer Fleet**.)',
    ],
    why: 'כשמשהו נראה לא בסדר, עדיף **Pause** ולא **Cancel**: מהשהיה אפשר לחזור, מביטול לא.',
  },
  {
    id: 'live-bed-cleared',
    route: '/live',
    targets: ['[data-tour="bed-cleared"]'],
    title: 'Bed cleared: המשטח פנוי',
    body: [
      'כשהדפסה מסתיימת, המוצר נשאר על המשטח, והמדפסת מחכה. הכרטיס כותב שצריך לפנות את המשטח, ומופיע הכפתור **Bed cleared**.',
      'מה עושים: 1. מחכים שהמשטח יתקרר מעט. 2. מוציאים את המוצר. 3. בודקים שלא נשארו שאריות. 4. לוחצים **Bed cleared**.',
    ],
    why: 'המדפסת לא רואה את המשטח. אם תלחצו לפני שפיניתם, ההדפסה הבאה תתחיל מעל המוצר הישן, והראש יתנגש בו.',
    missing: 'עכשיו אף מדפסת לא מחכה לפינוי, ולכן הכפתור לא מופיע. הוא יופיע בכרטיס של מדפסת שסיימה הדפסה.',
  },
  {
    id: 'live-history',
    route: '/live',
    targets: ['[data-tour="printer-tile"] [data-tour="tile-history"]'],
    title: 'History: מה קרה קודם',
    body: [
      '**History** פותח גרף של המדפסת הזאת לאורך הזמן: חום, התקדמות ואירועים. אפשר לראות בו מה קרה לפני תקלה.',
    ],
    why: 'כשהדפסה נכשלה, הגרף עוזר להבין למה: למשל, החום ירד באמצע.',
  },
  {
    id: 'live-events',
    route: '/live',
    targets: ['[data-tour="event-feed"]'],
    title: 'Live events: יומן האירועים',
    body: [
      'כל מה שקורה בחווה, לפי הסדר: הדפסה התחילה, הסתיימה, נכשלה, הזמנה חדשה, התראה.',
      'הלשוניות (**Printers**, **Orders**, **Safety**…) מסננות לפי נושא. **Replay** טוען אירועים מהשעה האחרונה, או מכמה זמן שתבחרו.',
      'התראה חמורה מופיעה גם כפס אדום בראש המסך, עם **Acknowledge** כדי לאשר שראיתם אותה.',
    ],
    why: 'אם יצאתם מהחדר, כאן רואים מה פספסתם.',
  },
  {
    id: 'live-done',
    route: '/live',
    targets: ['[data-testid="page-tour-live"]'],
    title: 'סיימתם את המסך הזה 🎉',
    body: ['אפשר לחזור להדרכה בכל רגע: הכפתור **מדריך לעמוד** נמצא תמיד באותו מקום, בפס העליון.'],
    why: 'לכל מסך במערכת יש הדרכה משלו, באותו כפתור.',
  },
]

// ------------------------------------------------------------------------------------------------ Order Pipeline
const ORDERS: TourStep[] = [
  {
    id: 'orders-intro',
    route: '/orders',
    targets: ['[data-testid="page-tour-orders"]'],
    title: 'מסך Order Pipeline: מסלול ההזמנות',
    body: [
      'כאן עוקבים אחרי כל הזמנה, מהרגע שלקוח פנה ועד שהחבילה נשלחה.',
      'כל הזמנה היא כרטיס, וכל טור הוא שלב. הכרטיס זז ימינה כשההזמנה מתקדמת.',
    ],
    why: 'במסך הזה רואים מה מחכה לכם עכשיו, ומה תקוע.',
  },
  {
    id: 'orders-board',
    route: '/orders',
    targets: ['[data-tour="order-board"]'],
    scrollTo: 'start',
    title: 'שבעת הטורים',
    body: [
      '**Lead**: פנייה של לקוח שעוד לא אושרה. **Order received**: הזמנה מאושרת.',
      '**Personalization**: מכינים את קובץ ההדפסה, למשל שלט עם שם. **Slicing / scheduling**: מחלקים את העבודה למדפסות.',
      '**Printing**: מדפיסים. **Quality control**: בודקים את המוצר המוגמר. **Shipped**: נשלח ללקוח.',
      'בראש כל טור כתוב כמה הזמנות יש בו, ומה קורה כשמשחררים בו כרטיס.',
    ],
    why: 'כרטיסים שנאספים בטור אחד מראים איפה העבודה נתקעת.',
    missing: 'הלוח מוצג בלשונית **Board**.',
  },
  {
    id: 'orders-new',
    route: '/orders',
    targets: ['[data-testid="new-order-button"]'],
    title: 'New manual order: הזמנה ידנית',
    body: [
      'הזמנה שהגיעה בטלפון, בהודעה או פנים אל פנים? **New manual order** פותח טופס: שם הלקוח, מה הוא הזמין, כמה, ועד מתי.',
      'ההזמנה נכנסת ללוח ומשם ממשיכה כמו כל הזמנה אחרת.',
    ],
    why: 'הזמנה שלא נרשמה במערכת לא תודפס. רושמים כל הזמנה, גם קטנה.',
  },
  {
    id: 'orders-card',
    route: '/orders',
    targets: ['[data-tour="order-card"]'],
    title: 'כרטיס הזמנה',
    body: [
      'בכרטיס: מספר ההזמנה, מה הוזמן, הלקוח, תאריך היעד, וכמה זמן היא כבר בשלב הנוכחי.',
      'לחיצה על הכרטיס פותחת את כל הפרטים ואת הפעולות של השלב, למשל **Confirm order** לאישור פנייה, או **Pass** ו-**Fail · reprint** בבדיקת איכות.',
    ],
    why: 'כל פעולה על הזמנה נמצאת בחלון הפרטים שלה, עם הסבר מה היא עושה.',
    missing: 'אין כרגע הזמנות על הלוח. הזמנה חדשה תופיע כאן ככרטיס.',
  },
  {
    id: 'orders-drag',
    route: '/orders',
    targets: ['[data-testid="column-RECEIVED"]'],
    title: 'גרירה לשלב הבא',
    body: [
      'כדי לקדם הזמנה, גוררים את הכרטיס לטור הבא ומשחררים. למשל: גרירה מ-**Lead** ל-**Order received** מאשרת את הפנייה.',
      'בזמן הגרירה טור מותר נצבע, וטור אסור אומר למה אי אפשר לשחרר בו. בטלפון, במקום לגרור, פותחים את הכרטיס ומשתמשים בכפתורים.',
      'את **Printing** אי אפשר לקדם ביד: המדפסות מקדמות אותו לבד כשההדפסה מסתיימת.',
    ],
    why: 'שחרור ב-**Personalization** או ב-**Slicing / scheduling** מתחיל עבודה אמיתית: הכנת קבצים ושליחה למדפסות. לכן המערכת מחכה לכם ולא עושה את זה לבד.',
  },
  {
    id: 'orders-auto',
    route: '/orders',
    targets: ['[data-tour="auto-advance"]'],
    title: 'Auto-advance: קידום אוטומטי',
    body: [
      'שלושה מתגים, כל אחד לשלב אחד: התחלת הכנת הקבצים, שליחה להדפסה, ומעבר בדיקת האיכות. **off**: מחכים לכם. **on**: המערכת מקדמת לבד.',
      'לחיצה על מתג מעבירה למסך **System & Agents**, שבו משנים אותו. כל שינוי נרשם, עם מי שינה ומתי.',
    ],
    why: 'בהתחלה השאירו הכול **off** ואשרו כל שלב בעצמכם. כשתסמכו על התהליך, אפשר להדליק.',
  },
  {
    id: 'orders-stuck',
    route: '/orders',
    targets: ['[data-tour="stuck-filter"]'],
    title: 'Stuck > 2 h: הזמנות תקועות',
    body: [
      'מסנן שמשאיר על הלוח רק הזמנות שנמצאות באותו שלב יותר משעתיים. בכרטיס עצמו מופיעה המילה **stuck** בכתום.',
      'לידו אפשר לסנן גם לפי ערוץ המכירה (**Channel**) ולפי תאריך היעד (**Due**).',
    ],
    why: 'הזמנה תקועה היא לקוח שמחכה. בדקו את הרשימה הזאת כמה פעמים ביום.',
  },
  {
    id: 'orders-failed',
    route: '/orders',
    targets: ['[data-tour="order-card"].attention', '[data-testid="column-QC"]'],
    title: 'הדפסה שנכשלה, והדפסה מחדש',
    body: [
      'כרטיס עם מסגרת אדומה צריך אתכם: הדפסה נכשלה, או שקובץ לא הוכן. השורה האדומה בכרטיס אומרת מה קרה.',
      'בבדיקת איכות (**Quality control**), מוצר פגום מסמנים **Fail · reprint**. רק היחידות הפגומות חוזרות לתור ההדפסה, וההזמנה ממשיכה כשהן מוכנות.',
      'הזמנה שנכשלה שלוש פעמים בבדיקת האיכות נעצרת ומחכה לכם. פותחים אותה ובוחרים מה לעשות, למשל **Print failed units again**.',
    ],
    why: 'המערכת לא מנסה שוב ושוב בלי סוף, כדי לא לבזבז פילמנט. אדם מחליט מה לעשות.',
    missing: 'אין עכשיו הזמנה שנכשלה. כשתהיה, הכרטיס שלה יקבל מסגרת אדומה.',
  },
  {
    id: 'orders-plates',
    route: '/orders',
    targets: ['[data-testid="plates"]'],
    title: 'Auto-assigned build plates: מה מודפס איפה',
    body: [
      'המערכת מקבצת פריטים מכמה הזמנות שצריכים אותו חומר ואותו צבע על משטח אחד (**plate**), כדי להדפיס אותם יחד.',
      'בטבלה: איזה משטח, באיזו מדפסת, מה עליו, ואיזה פילמנט. בזמן הדפסה רואים אחוזים, ולפני כן את הזמן המשוער.',
    ],
    why: 'כאן עוקבים אחרי כל פריט: אם פריט הודפס מחדש, הוא יופיע כאן על משטח חדש.',
  },
  {
    id: 'orders-nameplates',
    route: '/orders',
    targets: ['[data-testid="nameplates"]'],
    title: 'Nameplate generation: הכנת שלטים אישיים',
    body: [
      'שלט עם שם עובר קודם "הכנה": המערכת בונה ממנו קובץ תלת-ממדי. כאן רואים איזה טקסט נמצא בהכנה, ואיזה מוכן.',
      'אם ההכנה נכשלה, למשל בגלל תו שלא נתמך, מופיע **Retry**: מתקנים את הטקסט ומנסים שוב.',
    ],
    why: 'שגיאת כתיב מתגלה כאן, לפני שהודפסה. בדקו את הטקסט מול מה שהלקוח ביקש.',
  },
  {
    id: 'orders-done',
    route: '/orders',
    targets: ['[data-testid="page-tour-orders"]'],
    title: 'זהו, זה מסלול ההזמנות 🎉',
    body: ['אפשר לחזור להדרכה בכל רגע: הכפתור **מדריך לעמוד** נמצא תמיד באותו מקום, בפס העליון.'],
    why: 'אם משהו בלוח לא ברור, לחיצה על הכרטיס תמיד מסבירה מה הצעד הבא.',
  },
]

// ------------------------------------------------------------------------------------------------ Printer Fleet
const DRAWER = '[data-testid="printer-drawer"]'
const CLOSE_DRAWER: TourAction = { click: `${DRAWER} [aria-label="Close details"]` }
const UNTICK: TourAction = { click: '[data-tour="tile-select"] input:checked' }
const OPEN_DRAWER: TourAction = { click: '[data-tour="tile-details"]', unless: DRAWER, wait: true, undo: CLOSE_DRAWER }
const tab = (id: string): TourAction => ({ click: `${DRAWER} [data-tab="${id}"]`, unless: `${DRAWER} [data-tab="${id}"][aria-selected="true"]`, wait: true })

const FLEET: TourStep[] = [
  {
    id: 'fleet-intro',
    route: '/fleet',
    targets: ['[data-testid="page-tour-fleet"]'],
    title: 'מסך Printer Fleet: צי המדפסות',
    body: [
      'כאן מנהלים את המדפסות עצמן: רואים את מצב החומרה של כל אחת, עושים פעולה על כמה מדפסות יחד, ומכיילים.',
      'במהלך ההדרכה נפתח בשבילכם את חלון הפרטים של מדפסת, ונסגור אותו בסוף.',
    ],
    why: 'מדפסת מטופלת היא מדפסת שמדפיסה טוב. כאן רואים בעיות לפני שהן הורסות הדפסה.',
    prepare: [CLOSE_DRAWER],
  },
  {
    id: 'fleet-filters',
    route: '/fleet',
    targets: ['[data-tour="fleet-filters"]'],
    title: 'סינון לפי מצב',
    body: [
      'הכפתורים מסננים את המדפסות לפי מצב: **All** (כולן), או רק אלה שמדפיסות, מחכות, סיימו, או בתקלה. לחיצה נוספת מבטלת את הסינון.',
    ],
    why: 'כשיש הרבה מדפסות, ככה מוצאים מהר את אלה שצריכות טיפול, למשל את כל אלה שסיימו ומחכות לפינוי.',
    prepare: [CLOSE_DRAWER],
  },
  {
    id: 'fleet-details-button',
    route: '/fleet',
    targets: ['[data-tour="tile-details"]'],
    title: 'Details: חלון הפרטים',
    body: ['**Details** פותח את כל המידע על המדפסת. בואו נפתח אותו ונראה מה יש בו.'],
    why: 'בחלון הזה רואים מה קורה בתוך המדפסת, הרבה מעבר למה שיש בכרטיס.',
    prepare: [CLOSE_DRAWER, UNTICK],
  },
  {
    id: 'fleet-drawer',
    route: '/fleet',
    targets: [DRAWER],
    title: 'חלון הפרטים: Overview',
    body: [
      'למעלה: שם המדפסת, המצב שלה וסוג החיבור. מתחת: ארבע לשוניות, **Overview**, **Hardware**, **Calibration** ו-**History**.',
      'ב-**Overview**: מצלמה גדולה, גודל המשטח, הפילמנט הטעון, כמה הדפסות הצליחו השבוע, והאם המשטח פנוי (**clear**) או צריך פינוי (**needs clearing**).',
    ],
    why: 'אחוז הצלחה שיורד הוא סימן ראשון שהמדפסת צריכה טיפול או כיול.',
    prepare: [OPEN_DRAWER, tab('overview')],
  },
  {
    id: 'fleet-temps',
    route: '/fleet',
    targets: [`${DRAWER} [data-testid="hw-temps"]`],
    title: 'Hardware: טמפרטורות',
    body: [
      'בלשונית **Hardware** רואים את החיישנים של המדפסת. **Temperatures**: חום הראש (nozzle), המשטח (bed), ולפעמים גם חום התא שבתוכו מדפיסים (chamber), כל אחד מול היעד שלו.',
      'לידם: מאווררים (**Fans**), תנועה (**Motion**) וסוג הראש (**Nozzle**).',
    ],
    why: 'חום שלא מגיע ליעד גורם להדפסה חלשה או להתקלפות. אם זה קורה שוב ושוב, כיול **PID** (בהמשך) בדרך כלל פותר את זה.',
    prepare: [OPEN_DRAWER, tab('hardware')],
  },
  {
    id: 'fleet-ams',
    route: '/fleet',
    targets: [`${DRAWER} [data-testid="hw-ams"]`],
    title: 'AMS: מחסנית הפילמנט',
    body: [
      '**AMS** היא קופסה שמחזיקה כמה גלילי פילמנט ומחליפה ביניהם לבד. לכל מקום (**slot**) רואים את החומר, הצבע, וכמה פילמנט נשאר בגליל.',
      'ליד הקופסה רואים גם את הלחות ואת החום בתוכה.',
    ],
    why: 'פילמנט שנגמר באמצע עוצר את ההדפסה. לחות גבוהה מקלקלת את הפילמנט, וההדפסה יוצאת חלשה ומחוספסת.',
    missing: 'למדפסת הזאת אין **AMS**. היא קיימת רק בחלק מהמדפסות של Bambu Lab.',
    prepare: [OPEN_DRAWER, tab('hardware')],
  },
  {
    id: 'fleet-connection',
    route: '/fleet',
    targets: [`${DRAWER} [data-testid="hw-connection"]`],
    title: 'Connection: חיבור ו-Wi-Fi',
    body: [
      'איך המדפסת מחוברת, ואיזו גרסת תוכנה (**Firmware**) יש בה.',
      '**Wi-Fi**: עוצמת הקליטה. מספר קרוב יותר לאפס הוא קליטה טובה יותר: ‎-50 מצוין, ‎-80 חלש.',
    ],
    why: 'קליטה חלשה גורמת לניתוקים ולמצלמה שנתקעת. לפעמים מספיק להזיז את הנתב או את המדפסת.',
    prepare: [OPEN_DRAWER, tab('hardware')],
  },
  {
    id: 'fleet-health',
    route: '/fleet',
    targets: [`${DRAWER} [data-testid="hw-health"]`],
    title: 'Health: קודי תקלה',
    body: [
      'אם המדפסת מדווחת על בעיה, הקוד שלה מופיע כאן (**HMS** במדפסות Bambu Lab, או **Printer error**). כשהכול בסדר כתוב **No warnings reported**.',
      'את הקוד מחפשים באתר של יצרן המדפסת, ושם כתוב מה הוא אומר ומה עושים.',
    ],
    why: 'קוד תקלה מתריע מראש: פילמנט תקוע, מאוורר חלש, או ראש סתום. טיפול מוקדם חוסך הדפסות שנכשלות.',
    prepare: [OPEN_DRAWER, tab('hardware')],
  },
  {
    id: 'fleet-calibration',
    route: '/fleet',
    targets: [`${DRAWER} [data-tour="calibration-routines"]`],
    title: 'Calibration: כיולים',
    body: [
      'כיול הוא בדיקה שהמדפסת עושה לעצמה כדי להדפיס מדויק. לכל כיול יש **Run**:',
      '**Bed leveling**: מודד את המשטח בהרבה נקודות, כדי שהשכבה הראשונה תהיה אחידה. **Vibration compensation** (ב-Klipper נקרא Input Shaper): מודד רעידות של המדפסת ומבטל אותן.',
      '**Nozzle PID tuning** / **Bed PID tuning**: מלמד את המדפסת לשמור על חום יציב. בוחרים את הטמפרטורה שבה מדפיסים בדרך כלל.',
    ],
    why: 'עושים כיול אחרי הזזת מדפסת, החלפת חלק, או כשהשכבה הראשונה לא נדבקת טוב. לא צריך לכייל לפני כל הדפסה.',
    missing: 'הכיולים נטענים. אפשר לפתוח אותם בלשונית **Calibration** שבחלון הפרטים.',
    prepare: [OPEN_DRAWER, tab('calibration')],
  },
  {
    id: 'fleet-calibration-safety',
    route: '/fleet',
    targets: [`${DRAWER} [data-tour="calibration-intro"]`],
    title: 'בטיחות בכיול',
    body: [
      'כיול רץ רק על מדפסת מחוברת ופנויה, ורק אחד בכל פעם. בזמן כיול המערכת לא שולחת לה עבודה.',
      'לפני ההתחלה נפתח חלון אישור, ובו צריך לסמן **The bed is empty and nothing blocks the print head** (המשטח ריק ושום דבר לא חוסם את הראש).',
      'במהלך הכיול המדפסת זזה ומתחממת לבד. **Pause all printers** לא עוצר כיול: עוצרים אותו רק מהמסך של המדפסת עצמה.',
    ],
    why: 'בכיול הראש נע לכל פינות המשטח. חפץ שנשכח עליו יכול לשבור את הראש. לכן הישארו ליד המדפסת עד שהכיול נגמר.',
    prepare: [OPEN_DRAWER, tab('calibration')],
  },
  {
    id: 'fleet-select',
    route: '/fleet',
    targets: ['[data-tour="tile-select"]'],
    title: 'בחירת כמה מדפסות',
    body: ['לכל כרטיס יש תיבת סימון בפינה. מסמנים כמה מדפסות, או לוחצים **Select all** כדי לבחור את כל המדפסות שמוצגות.'],
    why: 'ככה עושים פעולה אחת על כמה מדפסות, במקום לעבור על כל אחת בנפרד.',
    prepare: [CLOSE_DRAWER],
  },
  {
    id: 'fleet-batch',
    route: '/fleet',
    targets: ['[data-testid="batch-bar"]'],
    title: 'Batch actions: פעולה על כמה מדפסות',
    body: [
      'סימנו בשבילכם מדפסת אחת, ונפתח הפס הזה. הכפתורים: **Pause**, **Resume**, **Cancel print**, **Bed cleared**, **Home axes** (החזרת הראש לנקודת ההתחלה).',
      'המספר על כל כפתור אומר על כמה מהמדפסות שבחרתם הפעולה תעבוד. כפתור אפור לא מתאים לאף אחת מהן, והסבר יופיע כשתעמדו עליו עם העכבר.',
      'לפני כל פעולה נפתח חלון אישור. **Clear** מבטל את הבחירה.',
    ],
    why: 'פעולה קבוצתית חוסכת זמן, וגם שומרת עליכם: היא מדלגת לבד על מדפסת שהפעולה לא מתאימה לה, למשל **Resume** למדפסת שלא הושהתה.',
    prepare: [CLOSE_DRAWER, { click: '[data-tour="tile-select"] input', unless: '[data-testid="batch-bar"]', undo: UNTICK }],
  },
  {
    id: 'fleet-add',
    route: '/fleet',
    targets: ['[data-testid="add-printer"]'],
    title: 'Add printer: מדפסת חדשה',
    body: ['**Add printer** פותח מדריך חיבור צעד אחרי צעד, לפי סוג המדפסת, עם בדיקת חיבור אמיתית לפני ההוספה.'],
    why: 'כך מחברים מדפסת בלי לנחש: הבדיקה אומרת בדיוק מה עובד, ומה לתקן.',
    prepare: [CLOSE_DRAWER, UNTICK],
  },
  {
    id: 'fleet-log',
    route: '/fleet',
    targets: ['[data-testid="fleet-log"]'],
    title: 'יומן הצי',
    body: ['כל מה שקרה למדפסות: הדפסות, תקלות, השהיות, כיולים ופעולות שעשו אנשי הצוות, עם השעה ומי עשה.'],
    why: 'כשמדפסת מתנהגת מוזר, היומן מראה מה השתנה לאחרונה.',
    prepare: [CLOSE_DRAWER, UNTICK],
  },
  {
    id: 'fleet-done',
    route: '/fleet',
    targets: ['[data-testid="page-tour-fleet"]'],
    title: 'סיימתם את צי המדפסות 🎉',
    body: ['אפשר לחזור להדרכה בכל רגע: הכפתור **מדריך לעמוד** נמצא תמיד באותו מקום, בפס העליון.'],
    why: 'לפני כיול ראשון, כדאי לעבור שוב על שלב הבטיחות.',
    prepare: [CLOSE_DRAWER, UNTICK],
  },
]

export const TOURS: Record<TourId, Tour> = {
  general: { id: 'general', title: 'סיור מודרך', steps: TOUR_STEPS },
  live: { id: 'live', title: 'מדריך לעמוד · Live floor', steps: LIVE },
  orders: { id: 'orders', title: 'מדריך לעמוד · Order Pipeline', steps: ORDERS },
  fleet: { id: 'fleet', title: 'מדריך לעמוד · Printer Fleet', steps: FLEET },
  social: { id: 'social', title: 'מדריך לעמוד · Social & Trends', steps: SOCIAL },
  catalog: { id: 'catalog', title: 'מדריך לעמוד · Instant Catalog', steps: CATALOG },
  filament: { id: 'filament', title: 'מדריך לעמוד · Inventory', steps: FILAMENT },
  finance: { id: 'finance', title: 'מדריך לעמוד · Finance', steps: FINANCE },
  system: { id: 'system', title: 'מדריך לעמוד · System & Agents', steps: SYSTEM },
  help: { id: 'help', title: 'מדריך לעמוד · מדריך למשתמש', steps: HELP },
  onboarding: { id: 'onboarding', title: 'מדריך לעמוד · Connect a new printer', steps: ONBOARDING },
  users: { id: 'users', title: 'מדריך לעמוד · Users', steps: USERS },
  niches: { id: 'niches', title: 'מדריך לעמוד · Niches', steps: NICHES },
  pricing: { id: 'pricing', title: 'מדריך לעמוד · Price calculator', steps: PRICING },
  scorecard: { id: 'scorecard', title: 'מדריך לעמוד · Niche scorecard', steps: SCORECARD },
}

/** One deep-dive tour per screen of the dashboard (route meta `tour` names it; the top bar shows its button). */
export const PAGE_TOURS: TourId[] = ['live', 'orders', 'fleet', 'social', 'catalog', 'filament', 'finance', 'system', 'help', 'onboarding', 'users', 'niches', 'pricing', 'scorecard']
