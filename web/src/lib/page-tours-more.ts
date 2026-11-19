// Page tours of the other screens (Social, Catalog, Filament, Finance, System and the help page). The registry of all
// tours is lib/page-tours.ts.
import type { TourAction, TourStep } from './tour'

const done = (route: string, id: string, tip: string): TourStep => ({
  id: `${id}-done`,
  route,
  targets: [`[data-testid="page-tour-${id}"]`],
  title: 'סיימתם את המסך הזה 🎉',
  body: ['אפשר לחזור להדרכה בכל רגע: הכפתור **מדריך לעמוד** נמצא תמיד באותו מקום, בפס העליון.'],
  why: tip,
})

const intro = (route: string, id: string, title: string, body: string[], why: string, prepare?: TourAction[]): TourStep => ({
  id: `${id}-intro`,
  route,
  targets: [`[data-testid="page-tour-${id}"]`],
  title,
  body,
  why,
  prepare,
})

/** Switch to a tab of a screen's TabBar; the tour switches back to the first tab when it ends. */
const tab = (bar: string, id: string, first: string): TourAction => ({
  click: `[data-tour="${bar}"] [data-tab="${id}"]`,
  unless: `[data-tour="${bar}"] [data-tab="${id}"][aria-selected="true"]`,
  wait: true,
  undo: { click: `[data-tour="${bar}"] [data-tab="${first}"]`, unless: `[data-tour="${bar}"] [data-tab="${first}"][aria-selected="true"]` },
})

// ------------------------------------------------------------------------------------------------ Social & Trends
export const SOCIAL: TourStep[] = [
  intro('/social', 'social', 'מסך Social Listening: לקוחות מהרשתות', [
    'כאן מגיעים פוסטים שאנשי הצוות שמרו מקבוצות בפייסבוק ובמקומות דומים. המערכת מזהה מי מהכותבים רוצה לקנות, וכותבת לו טיוטת תגובה.',
    'אתם קוראים, מתקנים, מאשרים, ומפרסמים בעצמכם. המערכת אף פעם לא מפרסמת לבד.',
  ], 'כל פוסט כזה הוא לקוח פוטנציאלי. תגובה מהירה ואנושית הופכת אותו להזמנה.'),
  {
    id: 'social-add',
    route: '/social',
    targets: ['[data-testid="add-post-open"]'],
    title: 'Add post: הוספת פוסט ביד',
    body: [
      'ראיתם פוסט מעניין? **Add post** פותח טופס: מדביקים את הטקסט, שם הקבוצה והקישור לפוסט.',
      'הדרך המהירה יותר היא תוסף הדפדפן (**Capture extension**, בתחתית המסך): לחיצה אחת שולחת את הפוסט לכאן.',
    ],
    why: 'רק פוסטים שאדם שמר מגיעים למערכת. היא לא סורקת קבוצות לבד, כדי לכבד את כללי הקבוצות ואת הפרטיות של הכותבים.',
  },
  {
    id: 'social-kpis',
    route: '/social',
    targets: ['[data-tour="social-kpis"]'],
    title: 'המספרים של היום',
    body: [
      '**Posts captured · today**: כמה פוסטים נשמרו היום. **Leads · 7 days**: כמה מהם זוהו כקונים בשבוע האחרון.',
      '**Drafts awaiting review**: טיוטות שמחכות לכם. **Replies posted · 7 days**: כמה תגובות פורסמו.',
    ],
    why: 'אם **Drafts awaiting review** גדל, לקוחות מחכים לתשובה. תשובה מהירה מגדילה את הסיכוי למכירה.',
  },
  {
    id: 'social-feed',
    route: '/social',
    targets: ['[data-tour="capture-feed"]'],
    title: 'Buying-intent feed: כל הפוסטים',
    body: [
      'כל פוסט שנשמר, עם הציון שהמערכת נתנה לו: כמה הכותב נשמע כמו מישהו שרוצה לקנות.',
      'הכפתורים למעלה מסננים: **lead** (קונה), **not a lead** (לא קונה), **ignored** (לא קשור בכלל), **waiting** (מחכה לבדיקה), **failed** (נכשל).',
    ],
    why: 'פה רואים גם את מה שהמערכת דחתה. אם קונה אמיתי נדחה, תדעו לתקן אותה.',
  },
  {
    id: 'social-card',
    route: '/social',
    targets: ['[data-tour="capture-card"]'],
    title: 'כרטיס פוסט: Buyer / Not buyer',
    body: [
      'בכרטיס: מאיזו קבוצה הפוסט, הטקסט שלו, ומה המערכת הבינה ממנו.',
      'הכפתורים **Buyer** ו-**Not buyer** הם התשובה שלכם: האם הכותב באמת רוצה לקנות. כפתור החץ המעגלי (**Score again**) מבקש מהמערכת לבדוק שוב.',
    ],
    why: 'כל סימון מלמד את המערכת. ככל שתסמנו יותר, היא תזהה קונים טוב יותר.',
    missing: 'עוד לא נשמרו פוסטים. פוסט ראשון יופיע כאן ככרטיס.',
  },
  {
    id: 'social-review',
    route: '/social',
    targets: ['[data-testid="review"]'],
    title: 'Review queue: תור האישור',
    body: [
      'כאן נמצאות הטיוטות לקונים. קוראים את הפוסט המקורי, ואת התגובה שהמערכת הציעה, ומתקנים בתיבה אם צריך.',
      '**Approve & Copy** מאשר ומעתיק. **Open post** פותח את הפוסט. מדביקים ומפרסמים, ואז **Mark as posted**. טיוטה לא מתאימה? **Reject**.',
      'קיצורי מקלדת: **J** / **K** מעבר בין טיוטות, **A** אישור והעתקה, **P** פורסם, **R** דחייה.',
    ],
    why: 'אתם הפנים של העסק. קראו כל תגובה לפני פרסום: מחיר, שם המוצר והטון צריכים להיות נכונים.',
  },
  {
    id: 'social-settings',
    route: '/social',
    targets: ['[data-testid="social-settings"]'],
    title: 'Listening settings: הגדרות',
    body: [
      'כאן קובעים מאיזה ציון פוסט נחשב לקונה, ומה התקציב היומי לבינה המלאכותית שקוראת את הפוסטים.',
      '**Accuracy** מראה כמה המערכת צדקה, לפי הסימונים שלכם ב-**Buyer** / **Not buyer**.',
    ],
    why: 'התקציב היומי שומר שלא יהיו הפתעות בחשבון. כשהוא נגמר, פוסטים מחכים עד מחר ולא הולכים לאיבוד.',
  },
  {
    id: 'social-extension',
    route: '/social',
    targets: ['[data-tour="capture-extension"]'],
    title: 'Capture extension: תוסף הדפדפן',
    body: [
      'כדי לשמור פוסטים בלחיצה אחת, מתקינים את התוסף בדפדפן (Chrome או Edge), ונותנים לו "מפתח": **New token**.',
      'כל דפדפן מקבל מפתח משלו. דפדפן שאבד או נמכר? **Revoke** מבטל את המפתח שלו.',
    ],
    why: 'המפתח מוצג פעם אחת בלבד. העתיקו אותו מיד לתוסף, ואל תשלחו אותו לאף אחד.',
  },
  done('/social', 'social', 'הכי חשוב במסך הזה: תור האישור. בדקו אותו כמה פעמים ביום.'),
]

// ------------------------------------------------------------------------------------------------ Instant Catalog
export const CATALOG: TourStep[] = [
  intro('/catalog', 'catalog', 'מסך Instant Catalog: קטלוג המוצרים', [
    'כל המוצרים שהחווה יודעת להדפיס מוכנים מראש. לכל מוצר יש קובץ **3MF**: קובץ שאומר למדפסת בדיוק מה ואיך להדפיס.',
    'מכאן גם מדפיסים סדרה (**Batch**) למלאי: כמה מוצרים יחד.',
  ], 'מוצר מהקטלוג מודפס בלי הכנה. זו הדרך המהירה לייצר את מה שנמכר הכי טוב.'),
  {
    id: 'catalog-import',
    route: '/catalog',
    targets: ['[data-testid="import-3mf"]'],
    title: 'Import 3MF: מוצר חדש',
    body: [
      '**Import 3MF** מעלה קובץ מוצר. עדיף קובץ שהוכן ("נחתך", sliced) בתוכנה Bambu Studio או OrcaSlicer, כי הוא מודפס כמו שהוא.',
      'בטופס נותנים למוצר שם, קוד (**SKU**), קטגוריה ומחיר.',
    ],
    why: 'קובץ שנחתך מראש כבר יודע כמה זמן וכמה פילמנט ההדפסה צריכה, וכך המערכת מתכננת נכון.',
  },
  {
    id: 'catalog-categories',
    route: '/catalog',
    targets: ['[data-tour="catalog-categories"]'],
    title: 'קטגוריות ומיון',
    body: [
      'הכפתורים מסננים לפי קטגוריה. **All** מציג הכול. המספר ליד כל קטגוריה הוא כמה מוצרים יש בה.',
      'בצד: **switched off too** מציג גם מוצרים שהושבתו, ו-**Sort** קובע את הסדר, למשל לפי מה שנמכר הכי הרבה השבוע.',
    ],
    why: 'המוצרים ממוינים לפי מכירות. מה שבראש הרשימה הוא מה שכדאי שיהיה במלאי.',
  },
  {
    id: 'catalog-card',
    route: '/catalog',
    targets: ['[data-tour="sku-card"]'],
    title: 'כרטיס מוצר',
    body: [
      'בכרטיס: תמונה של המוצר על המשטח (בגודל אמיתי), כמה נמכרו השבוע, כמה זמן ופילמנט כל יחידה צריכה, והמחיר.',
      '**Sliced ✓** אומר שהקובץ מוכן להדפסה. **Slice now** מכין אותו. גלגל השיניים פותח את פרטי המוצר לעריכה.',
    ],
    why: 'מוצר שלא הוכן (**Slice now**) עוד לא יודע כמה זמן הוא לוקח, ולכן ההערכה שלו פחות מדויקת.',
    missing: 'עוד אין מוצרים בקטלוג. הוסיפו את הראשון עם **Import 3MF**.',
  },
  {
    id: 'catalog-add',
    route: '/catalog',
    targets: ['[data-tour="sku-add"]', '[data-tour="sku-card"]'],
    title: 'Add to batch: הוספה לסדרה',
    body: [
      '**Add to batch** מוסיף את המוצר לסדרת הדפסה. הכמות שמוצעת היא כמה שנמכרו השבוע, פחות מה שכבר בייצור.',
      'אחרי ההוספה אפשר לשנות את הכמות בתיבת המספר.',
    ],
    why: 'הכמות המוצעת עוזרת לא להדפיס יותר מדי ולא פחות מדי: בדיוק מה שצפוי להימכר.',
  },
  {
    id: 'catalog-batch',
    route: '/catalog',
    targets: ['[data-testid="batch-bar"]'],
    title: 'הסדרה: Queue batch',
    body: [
      'הוספנו בשבילכם מוצר אחד לסדרה, ונפתח הפס הזה בתחתית. רואים בו כמה משטחים ושעות הסדרה תיקח, וכמה פילמנט היא צריכה.',
      'אם חסר פילמנט מסוים, מופיעה אזהרה כתומה. אם מוצר גדול מדי למשטח, כתוב **too big for the bed**.',
      '**Queue batch** שולח את הסדרה להדפסה. **Clear** מנקה אותה.',
    ],
    why: 'בדקו את האזהרות לפני **Queue batch**: סדרה בלי מספיק פילמנט תיעצר באמצע.',
    missing: 'כדי לראות את הפס הזה, הוסיפו מוצר לסדרה עם **Add to batch**.',
    prepare: [{ click: '[data-tour="sku-add"]:not([disabled])', unless: '[data-testid="batch-bar"]', undo: { click: '[data-tour="batch-clear"]' } }],
  },
  done('/catalog', 'catalog', 'פעם בשבוע, בדקו מה בראש הרשימה ושלחו סדרה למלאי.'),
]

// ------------------------------------------------------------------------------------------------ Filament
export const FILAMENT: TourStep[] = [
  intro('/filament', 'filament', 'מסך Filament: מלאי הפילמנט', [
    '**פילמנט** הוא חוט הפלסטיק שממנו מדפיסים. הוא מגיע בגלילים (**spools**), ולכל גליל יש חומר (למשל PLA) וצבע.',
    'כאן רואים כמה פילמנט יש, כמה משתמשים ביום, ומתי צריך להזמין עוד.',
  ], 'בלי פילמנט אין הדפסות. המסך הזה עוזר שהמלאי לא ייגמר אף פעם באמצע עבודה.'),
  {
    id: 'filament-scan',
    route: '/filament',
    targets: ['[data-tour="spool-scan"]'],
    title: 'סריקת גליל ורישום גליל חדש',
    body: [
      'לכל גליל יש מדבקה עם קוד. סורקים אותה (או מקלידים) בתיבה **Scan or type a spool label**, ולוחצים **Find** כדי לפתוח את הגליל.',
      'גליל חדש שהגיע? **Register spool** רושם אותו: חומר, צבע, מותג ומשקל.',
    ],
    why: 'גליל שלא נרשם לא נספר במלאי, והמערכת עלולה להזמין עוד מיותר, או לא לדעת שהוא קיים.',
  },
  {
    id: 'filament-kpis',
    route: '/filament',
    targets: ['[data-tour="filament-kpis"]'],
    title: 'המלאי במבט אחד',
    body: [
      '**Active spools**: גלילים שטעונים במדפסות עכשיו. **Backup spools**: גלילים על המדף.',
      '**Below threshold**: חומרים שהמלאי שלהם ירד מתחת למינימום. **Spend · 30 d**: כמה הוצאתם על פילמנט בחודש האחרון.',
    ],
    why: 'מספר ב-**Below threshold** אומר שצריך להזמין בקרוב.',
  },
  {
    id: 'filament-spools',
    route: '/filament',
    targets: ['[data-tour="spool-card"]', '[data-tour="spools"]'],
    title: 'כרטיס גליל',
    body: [
      'בכרטיס: החומר והצבע, כמה גרם נשארו (ובאחוזים), איפה הגליל נמצא (באיזו מדפסת או על איזה מדף), ולכמה ימים הוא יספיק.',
      'בזמן הדפסה רואים כמה גרם ההדפסה כבר השתמשה. לחיצה על הכרטיס פותחת את הגליל: שקילה, טעינה למדפסת או הוצאה ממנה.',
    ],
    why: 'שקילה מדי פעם מתקנת את המספר במערכת, אם הוא התרחק מהמשקל האמיתי.',
    missing: 'עוד אין גלילים. רשמו את הראשון עם **Register spool**.',
  },
  {
    id: 'filament-rules',
    route: '/filament',
    targets: ['[data-testid="reorder-rules"]'],
    title: 'Reorder rules: מתי להזמין',
    body: [
      'לכל חומר וצבע: כמה משתמשים ביום, כמה יש, ו-**Min stock**: המינימום שתמיד צריך להיות.',
      'המערכת מחשבת מתי להזמין לפי זמן האספקה של הספק, כך שהגליל החדש יגיע לפני שהישן נגמר.',
      '**Auto**: הזמנות קטנות יוצאות לבד. **Manual**: כל הזמנה מחכה לאישור שלכם.',
    ],
    why: 'בהתחלה השאירו **Manual**. כשתכירו את הצריכה, **Auto** חוסך עבודה.',
  },
  {
    id: 'filament-po',
    route: '/filament',
    targets: ['[data-testid="purchase-orders"]'],
    title: 'Purchase orders: הזמנות רכש',
    body: [
      'הזמנות פילמנט שהמערכת הציעה, לפי השלבים: מחכה לאישור (**Approve**, **Dismiss**, או **Edit qty** לשינוי הכמות), ואז הוזמנה בפועל מהספק (**Confirm ordered**, עם המחיר האמיתי), ובסוף הגיעה (**Receive**).',
      'ב-**Receive** הגלילים נרשמים במלאי לבד.',
    ],
    why: '**Confirm ordered** עם המחיר האמיתי רושם את ההוצאה גם בכספים, כך שהרווח מחושב נכון.',
  },
  {
    id: 'filament-usage',
    route: '/filament',
    targets: ['[data-testid="usage"]'],
    title: 'צריכה יומית',
    body: ['גרף של כמה גרם פילמנט הודפסו בכל יום בשבועיים האחרונים, והממוצע.'],
    why: 'קפיצה בצריכה (למשל לפני חג) אומרת שכדאי להזמין מוקדם יותר מהרגיל.',
  },
  done('/filament', 'filament', 'כשמגיעה חבילה מהספק, לחצו **Receive** באותו יום.'),
]

// ------------------------------------------------------------------------------------------------ Finance
const FIN_FIRST: TourAction = { click: '[data-tour="finance-tabs"] [data-tab="overview"]', unless: '[data-tour="finance-tabs"] [data-tab="overview"][aria-selected="true"]', wait: true }

export const FINANCE: TourStep[] = [
  intro('/finance', 'finance', 'מסך Finance: הכסף של העסק', [
    'כאן רואים כמה הכנסתם, כמה הוצאתם, ומה נשאר כרווח. כולל מע״מ, ומה כל סוג מוצר מרוויח.',
    'המערכת רושמת את רוב המספרים לבד: תשלומים מהזמנות, פילמנט שנקנה, וחשמל לפי שעות ההדפסה.',
  ], 'ככה יודעים אם העסק מרוויח, ואיזה מוצר שווה לקדם.', [FIN_FIRST]),
  {
    id: 'finance-actions',
    route: '/finance',
    targets: ['[data-testid="add-expense"]'],
    title: 'רישום כסף שנכנס ויצא',
    body: [
      '**Record payment**: לקוח שילם (במזומן, בהעברה או בביט)? רושמים כאן, ומקשרים להזמנה.',
      '**Add expense**: הוצאה שהמערכת לא יודעת עליה, למשל חלקי חילוף, חשבון חשמל או דמי משלוח.',
      '**Export CSV** מוריד את הנתונים לקובץ, למשל בשביל רואה החשבון.',
    ],
    why: 'רק מה שנרשם נספר. תשלום שלא נרשם נראה כחוב של הלקוח, והוצאה שלא נרשמה מנפחת את הרווח.',
    prepare: [FIN_FIRST],
  },
  {
    id: 'finance-period',
    route: '/finance',
    targets: ['[data-tour="finance-period"]'],
    title: 'בחירת תקופה',
    body: ['**Month**, **Quarter** או **Year**: לאיזו תקופה המספרים. לידם בוחרים את החודש, וכל מספר מושווה לתקופה הקודמת.'],
    why: 'השוואה לחודש הקודם מראה מגמה: האם העסק גדל.',
    prepare: [FIN_FIRST],
  },
  {
    id: 'finance-kpis',
    route: '/finance',
    targets: ['[data-tour="finance-kpis"]'],
    title: 'הסיכום: הכנסות, הוצאות, רווח',
    body: [
      '**Net revenue**: ההכנסות בלי מע״מ. **Expenses**: ההוצאות. **Net profit**: מה שנשאר.',
      'מתחת לכל מספר: באחוזים, כמה הוא עלה או ירד לעומת התקופה הקודמת.',
      'מתחת לסיכום: גרף הרווח של 12 החודשים האחרונים, ופילוח ההוצאות לפי סוג.',
    ],
    why: 'רווח שיורד בזמן שההכנסות עולות אומר שההוצאות גדלות מהר מדי. שווה לבדוק למה.',
    prepare: [FIN_FIRST],
  },
  {
    id: 'finance-tabs',
    route: '/finance',
    targets: ['[data-tour="finance-tabs"]'],
    title: 'הלשוניות',
    body: [
      '**Income** (הכנסות) ו-**Expenses** (הוצאות): כל רישום בנפרד. **Products**: כמה מרוויח כל סוג מוצר.',
      '**Receivables**: הזמנות שנשלחו ועוד לא שולמו. **VAT**: מע״מ לדיווח. **Settings**: אחוז המע״מ, סוג העסק ומחירים.',
    ],
    why: 'בכל לשונית אותה תקופה שבחרתם למעלה.',
    prepare: [FIN_FIRST],
  },
  {
    id: 'finance-income',
    route: '/finance',
    targets: ['[data-tour="finance-income"]'],
    title: 'Income: הכנסות',
    body: ['כל תשלום שהתקבל: מתי, מאיזו הזמנה, כמה, וכמה מזה מע״מ. גם כאן יש **Record payment**.'],
    why: 'הכנסה נספרת רק כשהכסף התקבל, לא כשההזמנה נשלחה.',
    prepare: [tab('finance-tabs', 'income', 'overview')],
  },
  {
    id: 'finance-receivables',
    route: '/finance',
    targets: ['[data-testid="receivables"]'],
    title: 'Receivables: כסף שמגיע לכם',
    body: ['הזמנות שנשלחו ללקוח ועוד לא שולמו: מי, כמה, וכמה זמן עבר.'],
    why: 'עברו כמה ימים בלי תשלום? זה הזמן לשלוח ללקוח תזכורת נעימה.',
    prepare: [tab('finance-tabs', 'receivables', 'overview')],
  },
  {
    id: 'finance-vat',
    route: '/finance',
    targets: ['[data-testid="vat"]'],
    title: 'VAT: מע״מ',
    body: ['סיכום המע״מ לכל תקופת דיווח: מע״מ שגביתם מלקוחות, מע״מ ששילמתם על הוצאות, וכמה צריך להעביר.'],
    why: 'את הדיווח עצמו עושים עם רואה החשבון. המסך עוזר לבדוק שהמספרים הגיוניים.',
    missing: 'נתוני המע״מ נטענים. אפשר לפתוח אותם בלשונית **VAT**.',
    prepare: [tab('finance-tabs', 'vat', 'overview')],
  },
  {
    id: 'finance-settings',
    route: '/finance',
    targets: ['[data-testid="finance-settings"]'],
    title: 'Settings: הגדרות הכספים',
    body: [
      'סוג העסק (**Osek murshe**, עוסק מורשה, או **Osek patur**, עוסק פטור), אחוז המע״מ, מחיר החשמל, וצריכת החשמל של כל מדפסת.',
      'כל עוד ההגדרות לא אושרו, מופיע פס אזהרה בראש המסך: **Settings not confirmed yet**.',
    ],
    why: 'בדקו את ההגדרות עם רואה החשבון ואשרו אותן. עד אז, כל החישובים מבוססים על ערכים זמניים.',
    prepare: [tab('finance-tabs', 'settings', 'overview')],
  },
  { ...done('/finance', 'finance', 'פעם בחודש: ודאו שכל התשלומים וההוצאות נרשמו, ובדקו את **Receivables**.'), prepare: [FIN_FIRST] },
]

// ------------------------------------------------------------------------------------------------ System & Agents
const SYS_FIRST: TourAction = { click: '[data-tour="system-tabs"] [data-tab="overview"]', unless: '[data-tour="system-tabs"] [data-tab="overview"][aria-selected="true"]', wait: true }

export const SYSTEM: TourStep[] = [
  intro('/system', 'system', 'מסך System & Agents: בריאות המערכת', [
    'המערכת בנויה מכמה "סוכנים": תוכנות קטנות שכל אחת אחראית על תחום. אחת מנהלת הזמנות, אחת מדפסות, אחת מלאי, אחת כספים.',
    'במסך הזה רואים שכולם עובדים, ומשנים הגדרות של אוטומציה.',
  ], 'בדרך כלל לא צריך להיכנס לכאן. נכנסים כשמספר אדום מופיע ליד **System & Agents** בתפריט.', [SYS_FIRST]),
  {
    id: 'system-kpis',
    route: '/system',
    targets: ['[data-tour="system-kpis"]'],
    title: 'מדדי המערכת',
    body: [
      'מספרים טכניים על התנועה במערכת. החשובים למפעיל: **Consumer lag** (כמה הודעות מחכות לטיפול) ו-**Dead letters** (הודעות שנכשלו).',
      'שניהם צריכים להיות אפס, או קרוב לאפס.',
    ],
    why: 'מספר שעולה ולא יורד אומר שסוכן תקוע. ספרו על זה לאחראי הטכני.',
    prepare: [SYS_FIRST],
  },
  {
    id: 'system-agents',
    route: '/system',
    targets: ['[data-tour="agents-panel"]'],
    title: 'Agents: הסוכנים',
    body: [
      'כל סוכן שולח "דופק" כל 10 שניות. ירוק (**healthy**): עובד. צהוב (**late**): מאחר. אדום (**down**): לא עונה.',
      'אותם סוכנים מופיעים גם בתחתית התפריט, עם נקודה צבעונית.',
    ],
    why: 'סוכן אדום אומר שחלק מהמערכת לא עובד. למשל, בלי סוכן המדפסות לא נשלחות הדפסות חדשות.',
    prepare: [SYS_FIRST],
  },
  {
    id: 'system-settings',
    route: '/system',
    targets: ['[data-testid="settings"]'],
    title: 'Automation toggles: מתגי אוטומציה',
    body: [
      'כאן מדליקים ומכבים מה המערכת עושה לבד: למשל קידום הזמנות לשלב הבא, או הזמנת פילמנט אוטומטית.',
      'כל שינוי נרשם: מי שינה, מתי, ומה היה קודם.',
    ],
    why: 'מתג דלוק חוסך עבודה, אבל גם מוריד את הבדיקה האנושית. הדליקו רק מה שבדקתם שעובד טוב.',
    prepare: [SYS_FIRST],
  },
  {
    id: 'system-servers',
    route: '/system',
    targets: ['[data-tour="servers"]'],
    title: 'Servers: השרתים',
    body: ['השירותים שהמערכת צריכה: מסד הנתונים, מערכת ההודעות, ושרת המצלמות. ליד כל אחד כתוב אם הוא תקין.'],
    why: 'שרת אדום כאן מסביר תקלות במקומות אחרים, למשל מצלמות שלא עובדות.',
    prepare: [SYS_FIRST],
  },
  {
    id: 'system-printers',
    route: '/system',
    targets: ['[data-tour="system-printers"]'],
    title: 'Printers: המדפסות מצד המערכת',
    body: ['רשימת המדפסות שהוגדרו, איך כל אחת מחוברת, והאם המערכת מקבלת ממנה נתונים.'],
    why: 'מדפסת שלא מופיעה כאן לא הוגדרה. מוסיפים מדפסת עם **Add printer** במסך **Printer Fleet**: שם יש מדריך צעד אחרי צעד ובדיקת חיבור.',
    prepare: [SYS_FIRST],
  },
  {
    id: 'system-backups',
    route: '/system',
    targets: ['[data-tour="backups"]'],
    title: 'Backups: גיבויים',
    body: [
      'כל יום המערכת שומרת עותק של כל הנתונים במחשב הזה. אם מוגדר גיבוי מחוץ לאתר, נשלח גם עותק **מוצפן** לענן.',
      'ירוק (**Protected**): יש עותק מקומי ועותק בענן. כתום (**Local only**): העותקים רק במחשב הזה. אדום (**Needs attention**): גיבוי נכשל או ישן מדי.',
    ],
    why: 'מחשב שמתקלקל או נגנב לא צריך למחוק את העסק. עותק בענן שומר הזמנות, מלאי וכספים גם אז.',
    prepare: [SYS_FIRST],
  },
  {
    id: 'system-dlq',
    route: '/system',
    targets: ['[data-tour="dlq"]'],
    title: 'Dead letter queue: הודעות שנכשלו',
    body: [
      'כשסוכן לא מצליח לטפל בהודעה גם אחרי כמה ניסיונות, היא נשמרת כאן ולא הולכת לאיבוד.',
      'לחיצה על הודעה פותחת את **Message inspector**: **Replay** מנסה שוב (למשל אחרי שתקלה תוקנה), **Discard** מוחק. **Replay all** מנסה שוב את כולן.',
    ],
    why: 'לפני **Discard**, ודאו שאתם מבינים מה ההודעה. היא יכולה להיות הזמנה או תשלום.',
    prepare: [SYS_FIRST],
  },
  {
    id: 'system-tabs',
    route: '/system',
    targets: ['[data-tour="system-tabs"]'],
    title: 'הלשוניות',
    body: ['**Agents**: פירוט מלא של כל סוכן. **NATS**: מערכת ההודעות שבין הסוכנים. **DLQ**: כל ההודעות שנכשלו. **Config**: כל ההגדרות.'],
    why: 'הלשוניות מיועדות בעיקר לאחראי הטכני, כשמחפשים מקור של תקלה.',
    prepare: [SYS_FIRST],
  },
  {
    id: 'system-nats',
    route: '/system',
    targets: ['[data-testid="nats-streams"]'],
    title: 'NATS: מערכת ההודעות',
    body: ['הסוכנים מדברים זה עם זה בהודעות. כאן רואים את "התורים" של ההודעות (**JetStream streams**), ואת החיבורים של כל סוכן.'],
    why: 'אם סוכן לא מחובר כאן, הוא לא מקבל עבודה.',
    prepare: [tab('system-tabs', 'nats', 'overview')],
  },
  {
    id: 'system-refresh',
    route: '/system',
    targets: ['[data-tour="system-refresh"]'],
    title: 'Refresh: רענון',
    body: ['המסך מתעדכן לבד כל כמה שניות. **Refresh** מעדכן מיד.'],
    why: 'אחרי שתוקנה תקלה, **Refresh** מראה מיד אם הכול חזר לירוק.',
    prepare: [SYS_FIRST],
  },
  done('/system', 'system', 'מספר אדום ליד **System & Agents** בתפריט? התחילו מ-**Agents** ומ-**Dead letter queue**.'),
]

// ------------------------------------------------------------------------------------------------ printer onboarding
export const ONBOARDING: TourStep[] = [
  intro('/fleet/add', 'onboarding', 'מסך Connect a new printer: חיבור מדפסת חדשה', [
    'כאן מחברים מדפסת אמיתית לחווה, צעד אחרי צעד: בוחרים סוג, מכינים את המדפסת, ממלאים כתובת וקוד, ובודקים שהחיבור עובד.',
    'בסוף מקבלים את השורות המדויקות להוסיף לקבצי ההגדרות של המערכת.',
  ], 'בדיקה לפני הוספה חוסכת ניחושים: אם משהו לא עובד, רואים בדיוק מה, ואיך מתקנים.'),
  {
    id: 'onboarding-start',
    route: '/fleet/add',
    targets: ['[data-tour="onboarding-vendors"]', '[data-tour="onboarding-steps"]'],
    title: 'בוחרים סוג מדפסת',
    body: [
      '**Bambu Lab** (סדרות X1, P1, A1), **Klipper / Voron** (מדפסות עם Moonraker), או **Generic / Other** למדפסת אחרת.',
      'אחרי הבחירה מופיעים השלבים למעלה: כל שלב עם הוראות קצרות. השלבים שהושלמו מסומנים ב-✓.',
    ],
    why: 'לכל סוג מדפסת יש הגדרות אחרות. ההוראות והבדיקה מותאמות לסוג שבחרתם.',
  },
  {
    id: 'onboarding-test',
    route: '/fleet/add',
    targets: ['[data-testid="test-connection"]'],
    title: 'Test Connection: בדיקת חיבור',
    body: [
      'בשלב 3 ממלאים את פרטי המדפסת ולוחצים **Test Connection**. המערכת בודקת: שהמדפסת עונה, שקוד הגישה נכון, שהיא שולחת נתונים, שאפשר לשלוח אליה קבצים, ושהמצלמה עובדת.',
      'ליד כל בדיקה שנכשלה כתוב בעברית מה לתקן. **הבא** נפתח רק כשהבדיקה עוברת.',
    ],
    why: 'קוד הגישה נשלח רק לבדיקה עצמה. הוא לא נשמר במערכת ולא נרשם ביומן.',
    missing: 'הכפתור מופיע בשלב 3, אחרי שבוחרים סוג מדפסת.',
  },
  {
    id: 'onboarding-practice',
    route: '/fleet/add',
    targets: ['[data-testid="use-simulator"]', '[data-tour="onboarding-vendors"]'],
    title: 'תרגול בלי מדפסת',
    body: ['בשלב 3 יש כפתור **תרגול**: הוא ממלא את הפרטים של מדפסת מדומה שרצה במחשב, כך שאפשר לעבור את כל התהליך עוד לפני שהמדפסת הגיעה.'],
    why: 'פעם ראשונה? תרגלו קודם. כשתגיע מדפסת אמיתית, כבר תכירו כל שלב.',
  },
  {
    id: 'onboarding-back',
    route: '/fleet/add',
    targets: ['[data-testid="back-to-fleet"]'],
    title: 'חזרה לצי המדפסות',
    body: ['**Printer Fleet** מחזיר למסך המדפסות. אחרי שמוסיפים את השורות לקבצים ומפעילים מחדש, המדפסת החדשה מופיעה שם.'],
    why: 'לפני הדפסה אמיתית ראשונה, נסו על המדפסת החדשה **Pause** ו-**Resume**, כדי לוודא שהחווה שולטת בה.',
  },
  done('/fleet/add', 'onboarding', 'כל מדפסת חדשה עוברת כאן, גם אם היא מאותו דגם כמו האחרות: לכל אחת כתובת וקוד משלה.'),
]

// ------------------------------------------------------------------------------------------------ help page
export const HELP: TourStep[] = [
  intro('/help', 'help', 'דף המדריך', [
    'בדף הזה יש שני דברים: סיור כללי במערכת, והמדריך המלא בכתב.',
  ], 'כשמשהו לא ברור, זה המקום הראשון לחפש בו.'),
  {
    id: 'help-start',
    route: '/help',
    targets: ['[data-testid="tour-start"]'],
    title: 'הסיור הכללי',
    body: ['**התחל סיור מודרך** עובר על כל המערכת בכמה דקות: התפריט, כפתור החירום, המדפסות, ההזמנות והרשתות החברתיות.'],
    why: 'מתאים ליום הראשון, או למי שחוזר אחרי הפסקה.',
  },
  {
    id: 'help-contents',
    route: '/help',
    targets: ['[data-testid="user-guide"] ol'],
    title: 'תוכן העניינים',
    body: ['לחיצה על נושא קופצת ישר אליו: התחברות, הזמנות, מדפסות, קטלוג, רשתות חברתיות, ומה עושים כשמשהו לא עובד.'],
    why: 'בסוף המדריך יש טבלה של בעיות נפוצות ופתרונות. שווה להכיר אותה.',
  },
  {
    id: 'help-page-tours',
    route: '/help',
    targets: ['[data-testid="page-tour-help"]'],
    title: 'הדרכה לכל מסך',
    body: ['בכל מסך במערכת יש כפתור **מדריך לעמוד**, תמיד באותו מקום בפס העליון. הוא מסביר את המסך שאתם נמצאים בו, כפתור אחרי כפתור.'],
    why: 'לא צריך לזכור הכול. כשנכנסים למסך לא מוכר, פשוט לוחצים עליו.',
  },
  {
    id: 'help-sidebar',
    route: '/help',
    targets: ['[data-testid="nav-help"]'],
    openNav: true,
    title: 'הדרך חזרה לכאן',
    body: ['מכל מסך, **User guide · מדריך למשתמש** בתחתית התפריט מחזיר לדף הזה.'],
    why: 'סיימתם! מכאן אפשר להתחיל את הסיור הכללי, או לקרוא את המדריך.',
  },
]
