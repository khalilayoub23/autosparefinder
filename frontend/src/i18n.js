import { useState, useEffect, useCallback } from 'react'

// Supported languages. he + ar are RTL, en is LTR.
export const LANGS = ['en', 'he', 'ar']
export const RTL_LANGS = new Set(['he', 'ar'])
export const LANG_NAMES = { en: 'English', he: 'עברית', ar: 'العربية' }
export const LANG_FLAGS = { en: 'us', he: 'il', ar: 'sa' }

function detectLang() {
  try {
    const url = new URLSearchParams(window.location.search).get('lang')
    if (url && LANGS.includes(url)) return url
    const stored = localStorage.getItem('asf-lang')
    if (stored && LANGS.includes(stored)) return stored
    const nav = (navigator.language || '').slice(0, 2).toLowerCase()
    if (LANGS.includes(nav)) return nav
  } catch { /* SSR / no window */ }
  return 'en'
}

// Flat translation dictionary. English is the source + fallback.
const DICT = {
  en: {
    'nav.home': 'Home', 'nav.categories': 'Categories', 'nav.catalog': 'Catalog',
    'nav.quote': 'Request a Quote', 'nav.how': 'How It Works', 'nav.about': 'About Us', 'nav.support': 'Support',
    'topbar.tagline': 'Millions of Parts. Multiple Suppliers. The Right Part for Your Vehicle.',
    'topbar.trackOrder': 'Track Order',
    'search.navPlaceholder': 'Search by VIN, OEM Number, SKU or Part Name...',
    'search.allCategories': 'All Categories',
    'account.chatWithUs': 'Chat with us', 'account.online': "We're online",
    'account.myAccount': 'My Account', 'account.signIn': 'Sign In', 'account.cart': 'Cart',
    'hero.title1': 'Find the Right Part.', 'hero.fast': 'Fast.', 'hero.easy': 'Easy.', 'hero.reliable': 'Reliable.',
    'hero.subtitle': 'Search millions of auto parts from trusted suppliers worldwide. Best prices. Fast delivery.',
    'hero.tab.vin': 'Search by VIN', 'hero.tab.oem': 'OEM Number', 'hero.tab.sku': 'SKU / Part Number', 'hero.tab.vehicle': 'Vehicle Details',
    'hero.vinPlaceholder': 'Enter VIN Number (e.g., 1HGCM82633A004352)', 'hero.partPlaceholder': 'Enter Part Number...',
    'hero.searchBtn': 'Search Parts', 'hero.safe': '100% Safe & Secure Search', 'hero.noStore': "We don't store your VIN",
    'trust.suppliers': 'Trusted Suppliers', 'trust.suppliersSub': '1000+ verified suppliers',
    'trust.prices': 'Best Prices', 'trust.pricesSub': 'Compare & save more',
    'trust.delivery': 'Fast Delivery', 'trust.deliverySub': 'Local & international',
    'trust.payments': 'Secure Payments', 'trust.paymentsSub': '100% secure checkout',
    'trust.expert': 'Expert Support', 'trust.expertSub': '24/7 support',
    'how.title': 'How {brand} Works',
    'how.step1': 'Search', 'how.step1d': 'Find parts by VIN, OEM, SKU or vehicle details.',
    'how.step2': 'Compare', 'how.step2d': 'Compare prices, condition, delivery & suppliers.',
    'how.step3': 'Order or Quote', 'how.step3d': 'Place an order or request a quote if stock is limited.',
    'how.step4': 'Track & Receive', 'how.step4d': 'Track your order and get support anytime.',
    'cat.title': 'Top Categories', 'cat.viewAll': 'View All Categories', 'cat.parts': 'parts', 'cat.browseAll': 'Browse all',
    'cat.engine': 'Engine Parts', 'cat.brakes': 'Brake System', 'cat.suspension': 'Suspension', 'cat.electrical': 'Electrical',
    'cat.body': 'Body Parts', 'cat.transmission': 'Transmission', 'cat.cooling': 'Cooling System', 'cat.filters': 'Filters',
    'cat.exhaust': 'Exhaust System', 'cat.more': 'More Categories',
    'ai.title': 'Need help finding the right part?',
    'ai.desc': 'Our AI Assistant can help you match parts and check compatibility in seconds.',
    'ai.try': 'Try AI Assistant',
    'feat.compat': 'Parts Compatibility', 'feat.compatD': 'Guaranteed fit for your vehicle.',
    'feat.cond': 'Multiple Conditions', 'feat.condD': 'New, Used, OEM, Aftermarket options.',
    'feat.ship': 'Global Shipping', 'feat.shipD': 'We ship worldwide to most countries.',
    'feat.returns': 'Easy Returns', 'feat.returnsD': 'Hassle-free returns within 30 days.',
    'footer.tagline': 'Your global marketplace for auto parts. Millions of parts, thousands of suppliers, one platform.',
    'footer.quickLinks': 'Quick Links', 'footer.browse': 'Browse Parts Catalog', 'footer.aiAssistant': 'AI Parts Assistant',
    'footer.track': 'Track Your Order', 'footer.createAccount': 'Create Account', 'footer.developers': 'Developers / API',
    'footer.support': 'Support', 'footer.waSupport': 'WhatsApp Support', 'footer.liveChat': 'Live AI Chat',
    'footer.privacy': 'Privacy Policy', 'footer.terms': 'Terms of Use', 'footer.refunds': 'Returns & Refunds',
    'footer.rights': '© {year} AutoSpareFinder. All rights reserved.',
    'footer.api': 'API', 'footer.privacyShort': 'Privacy', 'footer.termsShort': 'Terms', 'footer.refundsShort': 'Refunds',
    'lang.label': 'Language',
  },
  he: {
    'nav.home': 'דף הבית', 'nav.categories': 'קטגוריות', 'nav.catalog': 'קטלוג',
    'nav.quote': 'בקשת הצעת מחיר', 'nav.how': 'איך זה עובד', 'nav.about': 'אודות', 'nav.support': 'תמיכה',
    'topbar.tagline': 'מיליוני חלקים. ספקים מרובים. החלק הנכון לרכב שלך.',
    'topbar.trackOrder': 'מעקב הזמנה',
    'search.navPlaceholder': 'חיפוש לפי VIN, מספר OEM, מק״ט או שם חלק...',
    'search.allCategories': 'כל הקטגוריות',
    'account.chatWithUs': 'דברו איתנו', 'account.online': 'אנחנו מחוברים',
    'account.myAccount': 'החשבון שלי', 'account.signIn': 'התחברות', 'account.cart': 'עגלה',
    'hero.title1': 'מצאו את החלק הנכון.', 'hero.fast': 'מהיר.', 'hero.easy': 'קל.', 'hero.reliable': 'אמין.',
    'hero.subtitle': 'חפשו בין מיליוני חלקי רכב מספקים מהימנים בכל העולם. המחירים הטובים ביותר. משלוח מהיר.',
    'hero.tab.vin': 'חיפוש לפי VIN', 'hero.tab.oem': 'מספר OEM', 'hero.tab.sku': 'מק״ט / מספר חלק', 'hero.tab.vehicle': 'פרטי רכב',
    'hero.vinPlaceholder': 'הזינו מספר VIN (לדוגמה: 1HGCM82633A004352)', 'hero.partPlaceholder': 'הזינו מספר חלק...',
    'hero.searchBtn': 'חפשו חלקים', 'hero.safe': 'חיפוש 100% בטוח ומאובטח', 'hero.noStore': 'איננו שומרים את ה-VIN שלכם',
    'trust.suppliers': 'ספקים מהימנים', 'trust.suppliersSub': '1000+ ספקים מאומתים',
    'trust.prices': 'המחירים הטובים ביותר', 'trust.pricesSub': 'השוו וחסכו יותר',
    'trust.delivery': 'משלוח מהיר', 'trust.deliverySub': 'מקומי ובינלאומי',
    'trust.payments': 'תשלומים מאובטחים', 'trust.paymentsSub': 'תשלום 100% מאובטח',
    'trust.expert': 'תמיכה מקצועית', 'trust.expertSub': 'תמיכה 24/7',
    'how.title': 'איך {brand} עובד',
    'how.step1': 'חיפוש', 'how.step1d': 'מצאו חלקים לפי VIN, OEM, מק״ט או פרטי רכב.',
    'how.step2': 'השוואה', 'how.step2d': 'השוו מחירים, מצב, משלוח וספקים.',
    'how.step3': 'הזמנה או הצעת מחיר', 'how.step3d': 'בצעו הזמנה או בקשו הצעת מחיר אם המלאי מוגבל.',
    'how.step4': 'מעקב וקבלה', 'how.step4d': 'עקבו אחר ההזמנה וקבלו תמיכה בכל עת.',
    'cat.title': 'קטגוריות מובילות', 'cat.viewAll': 'צפו בכל הקטגוריות', 'cat.parts': 'חלקים', 'cat.browseAll': 'עיינו בהכל',
    'cat.engine': 'חלקי מנוע', 'cat.brakes': 'מערכת בלמים', 'cat.suspension': 'מתלים', 'cat.electrical': 'חשמל',
    'cat.body': 'חלקי מרכב', 'cat.transmission': 'תיבת הילוכים', 'cat.cooling': 'מערכת קירור', 'cat.filters': 'מסננים',
    'cat.exhaust': 'מערכת פליטה', 'cat.more': 'קטגוריות נוספות',
    'ai.title': 'צריכים עזרה למצוא את החלק הנכון?',
    'ai.desc': 'עוזר ה-AI שלנו יעזור לכם להתאים חלקים ולבדוק תאימות תוך שניות.',
    'ai.try': 'נסו את עוזר ה-AI',
    'feat.compat': 'תאימות חלקים', 'feat.compatD': 'התאמה מובטחת לרכב שלכם.',
    'feat.cond': 'מצבים מרובים', 'feat.condD': 'חדש, משומש, מקורי ואפטרמרקט.',
    'feat.ship': 'משלוח עולמי', 'feat.shipD': 'אנו שולחים לרוב מדינות העולם.',
    'feat.returns': 'החזרות קלות', 'feat.returnsD': 'החזרות ללא טרחה תוך 30 יום.',
    'footer.tagline': 'הזירה הגלובלית שלכם לחלקי רכב. מיליוני חלקים, אלפי ספקים, פלטפורמה אחת.',
    'footer.quickLinks': 'קישורים מהירים', 'footer.browse': 'עיון בקטלוג החלקים', 'footer.aiAssistant': 'עוזר החלקים החכם',
    'footer.track': 'מעקב אחר ההזמנה', 'footer.createAccount': 'פתיחת חשבון', 'footer.developers': 'מפתחים / API',
    'footer.support': 'תמיכה', 'footer.waSupport': 'תמיכת וואטסאפ', 'footer.liveChat': 'צ׳אט AI חי',
    'footer.privacy': 'מדיניות פרטיות', 'footer.terms': 'תנאי שימוש', 'footer.refunds': 'החזרות והחזרים',
    'footer.rights': '© {year} AutoSpareFinder. כל הזכויות שמורות.',
    'footer.api': 'API', 'footer.privacyShort': 'פרטיות', 'footer.termsShort': 'תנאים', 'footer.refundsShort': 'החזרים',
    'lang.label': 'שפה',
  },
  ar: {
    'nav.home': 'الرئيسية', 'nav.categories': 'الفئات', 'nav.catalog': 'الكتالوج',
    'nav.quote': 'طلب عرض سعر', 'nav.how': 'كيف يعمل', 'nav.about': 'من نحن', 'nav.support': 'الدعم',
    'topbar.tagline': 'ملايين القطع. موردون متعددون. القطعة المناسبة لسيارتك.',
    'topbar.trackOrder': 'تتبّع الطلب',
    'search.navPlaceholder': 'ابحث عبر VIN أو رقم OEM أو SKU أو اسم القطعة...',
    'search.allCategories': 'كل الفئات',
    'account.chatWithUs': 'تحدّث معنا', 'account.online': 'نحن متصلون',
    'account.myAccount': 'حسابي', 'account.signIn': 'تسجيل الدخول', 'account.cart': 'السلة',
    'hero.title1': 'اعثر على القطعة المناسبة.', 'hero.fast': 'سريع.', 'hero.easy': 'سهل.', 'hero.reliable': 'موثوق.',
    'hero.subtitle': 'ابحث في ملايين قطع غيار السيارات من موردين موثوقين حول العالم. أفضل الأسعار. توصيل سريع.',
    'hero.tab.vin': 'البحث عبر VIN', 'hero.tab.oem': 'رقم OEM', 'hero.tab.sku': 'SKU / رقم القطعة', 'hero.tab.vehicle': 'تفاصيل المركبة',
    'hero.vinPlaceholder': 'أدخل رقم VIN (مثال: 1HGCM82633A004352)', 'hero.partPlaceholder': 'أدخل رقم القطعة...',
    'hero.searchBtn': 'ابحث عن القطع', 'hero.safe': 'بحث آمن ومحمي 100%', 'hero.noStore': 'لا نحتفظ برقم VIN الخاص بك',
    'trust.suppliers': 'موردون موثوقون', 'trust.suppliersSub': 'أكثر من 1000 مورد موثّق',
    'trust.prices': 'أفضل الأسعار', 'trust.pricesSub': 'قارن ووفّر أكثر',
    'trust.delivery': 'توصيل سريع', 'trust.deliverySub': 'محلي ودولي',
    'trust.payments': 'مدفوعات آمنة', 'trust.paymentsSub': 'دفع آمن 100%',
    'trust.expert': 'دعم متخصص', 'trust.expertSub': 'دعم على مدار الساعة',
    'how.title': 'كيف يعمل {brand}',
    'how.step1': 'ابحث', 'how.step1d': 'اعثر على القطع عبر VIN أو OEM أو SKU أو تفاصيل المركبة.',
    'how.step2': 'قارن', 'how.step2d': 'قارن الأسعار والحالة والتوصيل والموردين.',
    'how.step3': 'اطلب أو اطلب عرض سعر', 'how.step3d': 'قدّم طلبًا أو اطلب عرض سعر إذا كان المخزون محدودًا.',
    'how.step4': 'تتبّع واستلم', 'how.step4d': 'تتبّع طلبك واحصل على الدعم في أي وقت.',
    'cat.title': 'أبرز الفئات', 'cat.viewAll': 'عرض كل الفئات', 'cat.parts': 'قطعة', 'cat.browseAll': 'تصفّح الكل',
    'cat.engine': 'قطع المحرك', 'cat.brakes': 'نظام الفرامل', 'cat.suspension': 'نظام التعليق', 'cat.electrical': 'الكهرباء',
    'cat.body': 'قطع الهيكل', 'cat.transmission': 'ناقل الحركة', 'cat.cooling': 'نظام التبريد', 'cat.filters': 'الفلاتر',
    'cat.exhaust': 'نظام العادم', 'cat.more': 'فئات أخرى',
    'ai.title': 'تحتاج مساعدة في إيجاد القطعة المناسبة؟',
    'ai.desc': 'يساعدك مساعدنا الذكي في مطابقة القطع والتحقق من التوافق في ثوانٍ.',
    'ai.try': 'جرّب المساعد الذكي',
    'feat.compat': 'توافق القطع', 'feat.compatD': 'ملاءمة مضمونة لسيارتك.',
    'feat.cond': 'حالات متعددة', 'feat.condD': 'جديد، مستعمل، OEM، بديل.',
    'feat.ship': 'شحن عالمي', 'feat.shipD': 'نشحن إلى معظم دول العالم.',
    'feat.returns': 'إرجاع سهل', 'feat.returnsD': 'إرجاع بدون متاعب خلال 30 يومًا.',
    'footer.tagline': 'سوقك العالمي لقطع غيار السيارات. ملايين القطع، آلاف الموردين، منصة واحدة.',
    'footer.quickLinks': 'روابط سريعة', 'footer.browse': 'تصفّح كتالوج القطع', 'footer.aiAssistant': 'مساعد القطع الذكي',
    'footer.track': 'تتبّع طلبك', 'footer.createAccount': 'إنشاء حساب', 'footer.developers': 'المطورون / API',
    'footer.support': 'الدعم', 'footer.waSupport': 'دعم واتساب', 'footer.liveChat': 'دردشة ذكية مباشرة',
    'footer.privacy': 'سياسة الخصوصية', 'footer.terms': 'شروط الاستخدام', 'footer.refunds': 'الإرجاع والاسترداد',
    'footer.rights': '© {year} AutoSpareFinder. جميع الحقوق محفوظة.',
    'footer.api': 'API', 'footer.privacyShort': 'الخصوصية', 'footer.termsShort': 'الشروط', 'footer.refundsShort': 'الاسترداد',
    'lang.label': 'اللغة',
  },
}

export function useI18n() {
  const [lang, setLangState] = useState(detectLang)
  const dir = RTL_LANGS.has(lang) ? 'rtl' : 'ltr'

  useEffect(() => {
    try {
      // Only set <html lang> (a11y/SEO). We deliberately do NOT set <html dir> here so we
      // never affect the app's other (Hebrew-hardcoded) pages — each consumer applies `dir`
      // to its own container (e.g. the landing root <div dir={dir}>), which is enough for the
      // rtl: Tailwind variants to work within that page.
      document.documentElement.lang = lang
      localStorage.setItem('asf-lang', lang)
    } catch { /* noop */ }
  }, [lang, dir])

  const setLang = useCallback((l) => { if (LANGS.includes(l)) setLangState(l) }, [])

  const t = useCallback((key, vars) => {
    let s = (DICT[lang] && DICT[lang][key]) ?? DICT.en[key] ?? key
    if (vars) for (const k in vars) s = s.split(`{${k}}`).join(String(vars[k]))
    return s
  }, [lang])

  return { lang, dir, setLang, t }
}
