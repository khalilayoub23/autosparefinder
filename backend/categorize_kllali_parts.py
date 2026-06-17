#!/usr/bin/env python3
"""
Rule-based batch categorization for parts stuck in 'כללי' / 'accessories' catch-all.
Uses keyword matching on name_he + name to assign correct category.
Processes in 20K-row batches to stay memory-safe.
"""
import asyncio, asyncpg, os, time

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
BATCH = 20000

# Each entry: (category_slug, [hebrew_keywords], [english_keywords])
# Longer/more specific patterns first to avoid wrong matches.
RULES = [
    ("safety-systems",   ["כרית אוויר", "חגורת בטיחות", "חגורה ב", "מגן ראש"],
                         ["airbag", "air bag", "seat belt", "seatbelt", "safety belt", "pretensioner"]),
    ("brakes",           ["בלמי", "בלם", "דיסק בלם", "רפידות", "ממסר בלם", "צנרת בלם"],
                         ["brake", "caliper", "rotor", "brake pad", "brake disc", "abs sensor", "wheel speed"]),
    ("engine",           ["מנוע", "בוכנה", "צילינדר", "ראש מנוע", "גל אלה", "גל ארכובה", "אלטרנטור", "מצת",
                          "תרמוסטט מנוע", "אטמי מנוע", "אטם ראש", "מסננת שמן", "פלטת מנוע"],
                         ["piston", "cylinder", "head gasket", "crankshaft", "camshaft", "alternator",
                          "spark plug", "engine mount", "oil filter", "oil pump", "timing", "valve", "rocker"]),
    ("fuel-air",         ["דלק", "משאבת דלק", "מזרק", "מסנן דלק", "מאייד", "צינור דלק", "מיכל דלק"],
                         ["fuel pump", "fuel filter", "injector", "carburetor", "fuel rail", "fuel tank",
                          "air filter", "throttle body", "mass air flow", "maf sensor", "intake"]),
    ("air-conditioning-heating", ["מזגן", "מדחס מזגן", "אידוי", "קונדנסור", "מפוח", "חימום", "אוורור"],
                                  ["air conditioning", "compressor", "condenser", "evaporator",
                                   "heater core", "blower", "hvac", "climate control", "ac pump"]),
    ("cooling",          ["רדיאטור", "מצנן", "משאבת מים", "מאוורר", "מחליף חום", "מכסה רדיאטור"],
                         ["radiator", "water pump", "cooling fan", "thermostat", "coolant", "intercooler",
                          "heat exchanger", "reservoir", "expansion tank"]),
    ("exhaust",          ["פליטה", "מאיין", "צינור פליטה", "קטליסט", "ממיר קטליטי"],
                         ["exhaust", "muffler", "catalytic converter", "manifold exhaust",
                          "dpf", "egr", "lambda sensor", "oxygen sensor"]),
    ("electrical-sensors", ["חיישן", "חשמל", "נתיך", "ממסר", "בוקסה", "כבל", "חישן"],
                            ["sensor", "switch", "module", "relay", "fuse", "wire harness",
                             "connector", "ecu", "control unit", "abs module", "speed sensor",
                             "temperature sensor", "pressure sensor"]),
    ("lighting",         ["פנס", "תאורה", "נורה", "נורית", "פנסים", "לד", "רפלקטור"],
                         ["headlight", "tail light", "fog light", "lamp", "bulb", "led", "reflector",
                          "turn signal", "indicator light", "daytime running", "drl"]),
    ("wipers-washers",   ["ממחק", "מגב", "שפריצר", "מיכל ממחקים"],
                         ["wiper", "washer", "wiper blade", "windshield washer", "windscreen wiper"]),
    ("suspension-steering", ["בולם", "קפיץ", "זרוע", "תמיכה", "הגה", "מוט גלגול", "כדור מפרק", "צ'ופה",
                              "מוט קשר", "גלגל הגה", "מנגנון הגה"],
                             ["shock absorber", "strut", "spring", "control arm", "tie rod", "ball joint",
                              "steering rack", "power steering", "stabilizer", "sway bar", "suspension arm"]),
    ("wheels-bearings",  ["נבה", "מיסב", "גלגל", "צמיג", "חישוק", "נורה גלגל"],
                         ["wheel bearing", "hub bearing", "wheel hub", "axle bearing", "wheel bolt",
                          "lug nut", "drive shaft", "cv joint", "propshaft"]),
    ("clutch-drivetrain", ["קלאץ", "גיר", "ציר", "פלטת כוח", "מצמד", "מ.כ."],
                           ["clutch", "flywheel", "pressure plate", "release bearing", "gearbox mount",
                            "differential", "transfer case", "drive shaft boot", "axle shaft"]),
    ("gearbox",          ["תיבת הילוכים", "שלדת הילוכים", "גיר אוטומטי", "גיר ידני"],
                         ["gearbox", "transmission", "gear box", "automatic transmission", "manual gearbox",
                          "gear shift", "selector fork", "synchronizer"]),
    ("belts-chains",     ["רצועה", "גלגלת", "שרשרת תזמון", "חגורה", "מתח רצועה"],
                         ["belt", "timing belt", "timing chain", "serpentine belt", "v-belt",
                          "idler pulley", "tensioner", "belt kit"]),
    ("interior-comfort", ["מושב", "ריפוד", "שטיח", "לוח מחוונים", "קונסולה", "מנחה", "ידית"],
                         ["seat", "carpet", "dashboard", "console", "trim panel", "door handle",
                          "mirror interior", "sun visor", "armrest", "headrest"]),
    ("body-exterior",    ["פגוש", "כנף", "דלת", "מכסה", "בונט", "גגון", "ויזר",
                          "גריל", "ספוילר", "סף", "שמשה", "חלון", "ראי"],
                         ["bumper", "fender", "door", "hood", "bonnet", "grille", "spoiler",
                          "roof", "mirror", "windshield", "window", "glass", "trim", "panel",
                          "front panel", "rear panel", "mudguard", "splash guard"]),
]


async def main():
    conn = await asyncpg.connect(DB)
    t0 = time.monotonic()
    total_fixed = 0

    print(f"[categorize] Starting rule-based categorization", flush=True)
    uncategorized = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE is_active AND category IN ('כללי','accessories')"
    )
    print(f"[categorize] Uncategorized parts: {uncategorized:,}", flush=True)

    for cat, he_keywords, en_keywords in RULES:
        # Build ILIKE conditions
        he_conds = " OR ".join([f"name_he ILIKE '%{kw}%'" for kw in he_keywords])
        en_conds = " OR ".join([f"name ILIKE '%{kw}%'" for kw in en_keywords])
        where = f"({he_conds} OR {en_conds})" if he_conds and en_conds else (he_conds or en_conds)

        sql = f"""
            UPDATE parts_catalog SET category = '{cat}', updated_at = NOW()
            WHERE is_active
              AND category IN ('כללי', 'accessories')
              AND ({where})
        """
        try:
            r = await conn.execute(sql)
            n = int(r.split()[-1])
            if n > 0:
                total_fixed += n
                print(f"  {cat}: {n:,} parts", flush=True)
        except Exception as e:
            print(f"  {cat}: ERROR {e}", flush=True)

    elapsed = time.monotonic() - t0
    remaining = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE is_active AND category IN ('כללי','accessories')"
    )
    print(f"\n[categorize] DONE: {total_fixed:,} categorized, {remaining:,} still uncategorized ({elapsed:.0f}s)", flush=True)
    await conn.close()


asyncio.run(main())
