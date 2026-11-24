// Localised labels for the stored category slugs. SOURCE OF TRUTH: backend/category_map.DISPLAY (the column holds
// English slugs + 'כללי'; Hebrew/Arabic/English names are display-only and must never be shown as the raw slug).
// Keep in sync with category_map.DISPLAY - tests/categoryLabel.test.js pins the keys.
export const CATEGORY_DISPLAY = {
  filters: { he: 'מסננים', ar: 'الفلاتر', en: 'Filters' },
  fluids: { he: 'שמנים ונוזלים', ar: 'الزيوت والسوائل', en: 'Fluids & Lubricants' },
  'belts-chains': { he: 'רצועות ושרשראות', ar: 'السيور والجنازير', en: 'Belts & Chains' },
  'service-general': { he: 'ערכות שירות', ar: 'أطقم الصيانة', en: 'Service Kits' },
  engine: { he: 'מנוע', ar: 'المحرك', en: 'Engine' },
  cooling: { he: 'מערכת קירור', ar: 'نظام التبريد', en: 'Cooling' },
  'fuel-air': { he: 'מערכת דלק ואוויר', ar: 'الوقود والهواء', en: 'Fuel & Air' },
  exhaust: { he: 'מערכת פליטה', ar: 'العادم', en: 'Exhaust' },
  'clutch-drivetrain': { he: 'מצמד והנעה', ar: 'الكلتش ونقل الحركة', en: 'Clutch & Drivetrain' },
  gearbox: { he: 'תיבת הילוכים', ar: 'علبة التروس', en: 'Gearbox' },
  brakes: { he: 'בלמים', ar: 'الفرامل', en: 'Brakes' },
  'suspension-steering': { he: 'מתלים והיגוי', ar: 'التعليق والتوجيه', en: 'Suspension & Steering' },
  'wheels-bearings': { he: 'גלגלים ומיסבים', ar: 'العجلات والرولمان', en: 'Wheels & Bearings' },
  'body-exterior': { he: 'גוף הרכב', ar: 'الهيكل الخارجي', en: 'Body & Exterior' },
  lighting: { he: 'תאורה', ar: 'الإضاءة', en: 'Lighting' },
  electrical: { he: 'חשמל', ar: 'الكهرباء', en: 'Electrical' },
  'audio-electronics': { he: 'שמע ואלקטרוניקה', ar: 'الصوتيات والإلكترونيات', en: 'Audio & Electronics' },
  'air-conditioning-heating': { he: 'מזגן וחימום', ar: 'التكييف والتدفئة', en: 'A/C & Heating' },
  'wipers-washers': { he: 'שמשות ומגבים', ar: 'المساحات والغسيل', en: 'Wipers & Washers' },
  'safety-systems': { he: 'מערכות בטיחות', ar: 'أنظمة الأمان', en: 'Safety Systems' },
  'hybrid-ev': { he: 'היברידי וחשמלי', ar: 'الهجين والكهربائي', en: 'Hybrid & EV' },
  'interior-comfort': { he: 'פנים הרכב', ar: 'المقصورة الداخلية', en: 'Interior & Comfort' },
  accessories: { he: 'אביזרים', ar: 'الإكسسوارات', en: 'Accessories' },
  merchandise: { he: 'מוצרי מיתוג', ar: 'منتجات دعائية', en: 'Merchandise' },
  fasteners: { he: 'ברגים ומחברים', ar: 'البراغي والمثبتات', en: 'Fasteners & Hardware' },
  'כללי': { he: 'כללי', ar: 'عام', en: 'General' },
}

const FALLBACK = { he: 'כללי', ar: 'عام', en: 'General' }
const HAS_HEBREW_OR_ARABIC = /[֐-׿؀-ۿ]/

/**
 * Label for a stored category value. Known slug -> localised name; value already in Hebrew/Arabic -> shown as is;
 * a slug-looking value we do not know (ASCII, hyphenated) -> the generic label - NEVER the raw English slug.
 */
export function categoryLabel(value, lang = 'he') {
  const v = String(value ?? '').trim()
  const l = ['he', 'ar', 'en'].includes(lang) ? lang : 'he'
  if (!v) return FALLBACK[l]
  const known = CATEGORY_DISPLAY[v] || CATEGORY_DISPLAY[v.toLowerCase()]
  if (known) return known[l]
  if (HAS_HEBREW_OR_ARABIC.test(v)) return v
  return FALLBACK[l]
}
