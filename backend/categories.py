from __future__ import annotations

CATEGORY_MAP = {
    'בלמים': {
        'he': ['בלם', 'בלמים', 'רפידה', 'רפידות', 'דיסק', 'דיסקים', 'קליפר', 'קליפרים', 'תוף', 'תופים', 'בוסטר'],
        'en': ['brake', 'caliper', 'rotor', 'pad', 'drum', 'booster']
    },
    'מתלה': {
        'he': ['בולם', 'בולמים', 'קפיץ', 'קפיצים', 'זרוע', 'זרועות', 'מסב', 'מסבים', 'מייצב', 'מתלה', 'שטרוט', 'בושינג', 'בושינגים'],
        'en': ['suspension', 'shock', 'strut', 'spring', 'arm', 'bushing', 'bearing', 'anti-roll']
    },
    'היגוי': {
        'he': ['הגה', 'גיר הגה', 'מוט הגה', 'פולסה'],
        'en': ['steering', 'rack', 'tie rod', 'ball joint', 'power steering']
    },
    'מנוע': {
        'he': ['מנוע', 'בוכנה', 'בוכנות', 'שסתום', 'שסתומים', 'גל ארכובה', 'גל זיזים'],
        'en': ['engine', 'piston', 'valve', 'crankshaft', 'camshaft', 'block']
    },
    'קירור': {
        'he': ['רדיאטור', 'קירור', 'תרמוסטט', 'משאבת מים', 'מאוורר', 'מאווררים', 'נוזל קירור'],
        'en': ['radiator', 'water pump', 'thermostat', 'cooling fan', 'coolant']
    },
    'מערכת דלק': {
        'he': ['משאבת דלק', 'מזרק', 'מזרקים', 'מיכל דלק', 'ריילי', 'שנורקל'],
        'en': ['fuel pump', 'injector', 'fuel tank', 'fuel rail']
    },
    'מערכת אוויר': {
        'he': ['מסנן אוויר', 'צינור אוויר', 'גוף מיתון'],
        'en': ['air filter', 'air intake', 'maf', 'throttle body']
    },
    'טורבו': {
        'he': ['טורבו', 'אינטרקולר', 'סופרשארג׳ר'],
        'en': ['turbo', 'turbocharger', 'supercharger', 'intercooler', 'boost']
    },
    'פליטה': {
        'he': ['פליטה', 'מפלט', 'קטליזטור', 'EGR'],
        'en': ['exhaust', 'muffler', 'catalytic', 'dpf', 'egr', 'scr']
    },
    'תיבת הילוכים וציר': {
        'he': ['תיבת הילוכים', 'גיר', 'ציר', 'דיפרנציאל'],
        'en': ['transmission', 'gearbox', 'driveshaft', 'differential', 'cv joint']
    },
    'מצמד': {
        'he': ['מצמד', 'גלגל תנופה', 'מסב שחרור', 'מזלג'],
        'en': ['clutch', 'flywheel', 'release bearing']
    },
    'רצועות תזמון': {
        'he': ['רצועה', 'רצועות', 'שרשרת תזמון', 'גלגלת', 'גלגלות', 'מותחן'],
        'en': ['timing belt', 'timing chain', 'tensioner', 'pulley']
    },
    'הצתה': {
        'he': ['מצת', 'מצתים', 'סליל הצתה', 'מפלג'],
        'en': ['spark plug', 'ignition coil', 'distributor', 'plug wire']
    },
    'סינון': {
        'he': ['מסנן שמן', 'מסנן דלק', 'מסנן מזגן'],
        'en': ['oil filter', 'fuel filter', 'cabin filter', 'pollen filter']
    },
    'חשמל ואלקטרוניקה': {
        'he': ['אלטרנטור', 'מצת הנעה', 'ECU', 'מחשב', 'ממסר', 'פיוז', 'צמת'],
        'en': ['alternator', 'starter motor', 'ecu', 'relay', 'fuse', 'wiring']
    },
    'חיישנים': {
        'he': ['חיישן', 'חיישנים', 'סנסור', 'סנסורים'],
        'en': ['sensor', 'o2 sensor', 'abs sensor', 'map sensor', 'speed sensor']
    },
    'מצבר': {
        'he': ['מצבר', 'מצברים', 'סוללה', 'בטריה'],
        'en': ['battery', 'battery management']
    },
    'תאורה': {
        'he': ['פנס', 'פנסים', 'נורה', 'נורות', 'תאורה', 'אינדיקטור'],
        'en': ['headlight', 'tail light', 'bulb', 'indicator', 'fog light']
    },
    'מזגן וחימום': {
        'he': ['מזגן', 'קומפרסור', 'קונדנסר', 'אידיידור', 'תנור', 'מפוח'],
        'en': ['ac', 'air conditioning', 'compressor', 'condenser', 'evaporator', 'heater', 'blower']
    },
    'גוף הרכב': {
        'he': ['פגוש', 'פגושים', 'כנף', 'כנפיים', 'דלת', 'דלתות', 'מכסה', 'גריל', 'סף', 'קישוט', 'קישוטים', 'ידית'],
        'en': ['bumper', 'fender', 'door', 'hood', 'grille', 'sill', 'trim']
    },
    'שמשות ומגבים': {
        'he': ['שמשה', 'שמשות', 'מגב', 'מגבים', 'חלון', 'חלונות', 'זכוכית', 'מווסת'],
        'en': ['windscreen', 'windshield', 'wiper', 'window', 'glass', 'regulator']
    },
    'פנים הרכב': {
        'he': ['דשבורד', 'מושב', 'מושבים', 'שטיח', 'שטיחים', 'קונסולה', 'ידית'],
        'en': ['dashboard', 'seat', 'carpet', 'console', 'door handle', 'interior']
    },
    'גלגלים וצמיגים': {
        'he': ['גלגל', 'גלגלים', 'חישוק', 'חישוקים', 'ג׳נט', 'צמיג', 'צמיגים'],
        'en': ['wheel', 'rim', 'tyre', 'tire', 'tpms', 'lug nut']
    },
    'אטמים וצינורות': {
        'he': ['אטם', 'אטמים', 'גיממה', 'אוילים'],
        'en': ['gasket', 'seal', 'o-ring', 'head gasket']
    },
    'מערכת בטיחות': {
        'he': ['איירבג', 'כרית אוויר', 'חגורה', 'חגורות'],
        'en': ['airbag', 'seatbelt', 'crash sensor']
    },
    'מערכת היברידית וחשמלי': {
        'he': ['היברידי', 'חשמלי', 'סוללה גדולה', 'PDU'],
        'en': ['hybrid', 'electric motor', 'inverter', 'pdu', 'ev', 'charging cable']
    },
    'שמנים ונוזלים': {
        'he': ['שמן', 'שמנים', 'גריז', 'נוזל בלמים'],
        'en': ['engine oil', 'grease', 'brake fluid', 'atf', 'lubricant']
    },
    'כלי עבודה ואביזרים': {
        'he': ['כלי', 'כלים', 'ציוד', 'אביזר', 'אביזרים', 'טיפוח'],
        'en': ['tools', 'accessories', 'car care', 'detailing']
    },
}


def _is_hebrew_word(value: str) -> bool:
    return any('\u0590' <= c <= '\u05ff' for c in value)


def guess_category_by_text(text: str) -> str | None:
    if not text:
        return None
    blob = text.strip().casefold()

    for category, langs in CATEGORY_MAP.items():
        keywords = langs.get('he', []) + langs.get('en', [])
        for keyword in keywords:
            kw = str(keyword).strip().casefold()
            if not kw:
                continue

            # Phrase keywords require all token stems to be present.
            tokens = [t for t in kw.split() if t]
            if len(tokens) > 1:
                stems = []
                for token in tokens:
                    if len(token) >= 4:
                        stems.append(token[:3] if _is_hebrew_word(token) else token[:5])
                    else:
                        stems.append(token)
                if stems and all(stem in blob for stem in stems):
                    return category
                continue

            if len(kw) >= 4:
                stem = kw[:3] if _is_hebrew_word(kw) else kw[:5]
                if stem in blob:
                    return category
            else:
                if kw in blob:
                    return category

    return None


__all__ = ['CATEGORY_MAP', 'guess_category_by_text']
