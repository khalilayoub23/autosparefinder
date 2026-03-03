"""
DB Quality Fixer — fixes parts_catalog data against PartsCatalog model spec.

Fixes applied:
  1. manufacturer — normalize aliases (Mercedes→Mercedes-Benz, GEN→Genesis, etc.)
  2. category    — derive real part category from name using Hebrew keyword rules
                   (was incorrectly set to vehicle brand name = same as manufacturer)
  3. part_type   — map Hebrew / garbage values → OEM / Original / Aftermarket / Refurbished / NULL
  4. is_active   — set False for rows where name='(ללא שם)' (placeholder unnamed parts)
"""

import asyncio
import re
import os
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://autospare:autospare@localhost:5432/autospare")

# ---------------------------------------------------------------------------
# 1.  MANUFACTURER ALIAS MAP  (alias → canonical name)
# ---------------------------------------------------------------------------
MANUFACTURER_ALIASES = {
    "Mercedes":         "Mercedes-Benz",
    "GEN":              "Genesis",
    "ג'נסיס":           "Genesis",
    "Jaecoo":           "JAECOO",
    "Citroen":          "Citroën",
    "VW":               "Volkswagen",
    "VAZ":              "Lada",
}

# ---------------------------------------------------------------------------
# 2.  PART_TYPE MAP  (raw DB value → spec value)
# ---------------------------------------------------------------------------
PART_TYPE_MAP = {
    # Hebrew → English spec
    "מקורי":    "Original",
    "מקורימקורי": "Original",   # garbled duplicate
    "חליפי":    "Aftermarket",
    "חליפיחליפי": "Aftermarket",
    "תחליפי":   "Aftermarket",
    "משופץ":    "Refurbished",
    "משופץמשופץ": "Refurbished",
    "משומש":    "Refurbished",
    "ללא":      None,
    "unknown":  None,
    # Garbage / clearly wrong — set to NULL
    "סוג מוצר": None,
    "אחריות הפריט": None,
    "מושביץ 3": None,
}

def normalize_part_type(raw: str) -> str | None:
    """Return spec-compliant part_type or None if garbage."""
    if not raw:
        return None
    stripped = raw.strip()
    if stripped in PART_TYPE_MAP:
        return PART_TYPE_MAP[stripped]
    # Any remaining value that starts with a year or looks like a date → NULL
    if re.match(r"^\d{4}-\d{2}-\d{2}", stripped):
        return None
    # OEM / Original / Aftermarket / Refurbished already in English spec → keep
    if stripped in ("OEM", "Original", "Aftermarket", "Refurbished"):
        return stripped
    # Anything else → NULL (safer than keeping garbage)
    return None


# ---------------------------------------------------------------------------
# 3.  CATEGORY KEYWORD RULES (Hebrew part names → category)
#     Ordered by specificity — first match wins.
# ---------------------------------------------------------------------------
CATEGORY_RULES = [
    # --- Brakes ---
    ("בלמים", [
        "בלם", "ברקס", "רפד", "דיסק", "צלחת בלם", "קליפר", "calipr",
        "brake", "disc", "pad", "rotor", "handbrake", "אבוס בלמים",
        "נעל בלם", "כונן בלמים", "צלחת בלמים", "צלחת הבלם",
        "כוס בלם",   # brake cup / cylinder
    ]),
    # --- Engine ---
    ("מנוע", [
        "מנוע", "בוכנ", "גלגל תנופה", "גל זנק", "גל ארכובה",
        "שסתום", "ראש צילינדר", "ראש מנוע", "מצמד", "גית", "טרבו", "טורבו",
        "מצת",  # spark plug / glow plug
        "קייל לשסתום", "סט קייל",  # valve stem seals
        "ממשק ראש",  # head gasket area
        "engine", "piston", "crankshaft", "camshaft", "valve", "timing",
        "turbo", "cylinder", "flywheel", "clutch", "glow plug", "spark plug",
        "intercool", "אינטרקולר", "אינטרקולט",
        "סיגמנט", "סגמנט",      # piston ring segment
        "לגרים",                 # main / connecting rod bearings
        "מע' סתמים", "סתמי",    # valve assembly / valve set
        "תא שריפה",              # combustion chamber
        "כיסוי מנוע", "כיסוי למניפולד",  # engine/manifold cover
        "מהלך",                  # stroke mechanism / engine component
        "ראש המנוע",
        "גל זיזים", "תותב לגל זיז",   # camshaft / cam follower
        "מתנע מושלם",            # complete starter motor assembly
        "סתם סעפת יניקה", "סתם יניקה",  # intake manifold valve
        "כונס אויר",             # air intake
        "שסתומים יניקה",        # intake valves set
        "ספק הזנקה",             # injection pump
    ]),
    # --- Gearbox / Transmission ---
    ("תיבת הילוכים", [
        "גיר", "תיבת הילוכים", "הילוך", "תמסורת",
        "ממיר מומנט", "ממסרת",
        "טורקונ", "טורקנ", "torque conv",
        "automatic", "transmiss",
        "gearbox", "differential", "דיפרנציאל",
        "מזלג לאוטומט",  # selector fork for automatic
        "תיבת גיר",
        "גשר לתיבת",       # gearbox bridge/mount
        "מזלג רוורס",      # reverse selector fork
        "לשונית למצב חניה",  # park pawl / park mode tab
        "קיש לשיפוץ תיבת",  # gearbox rebuild kit
        "קרונה לתהל",     # crown gear for transfer case
        "שיבה לתה'ל", "שיבה לתהל",  # shim for transfer case
        "תהל",             # transfer case (תיבת העברה)
        "סינכרון",         # synchromesh ring
        "כרית גיר",        # gear knob cushion
    ]),
    # --- Suspension & Steering ---
    ("מתלים והגה", [
        "בולם", "קפיץ", "זרוע", "מתלה", "ציריה", "סרגל",
        "מוט איזון", "מוט מיצב", "מוט הגה", "מוט פיתול",
        "נגד הגה", "הגה", "גלגל הגה",
        "חוליה",  # suspension link
        "נשם",   # boot / protective cover for axle/steering
        "גומיה למוט", "גומי מוט",
        "ג'וינט", "ג'וינ",  # joint
        "suspension", "shock", "spring", "strut", "control arm",
        "steering", "tie rod", "ball joint", "cv joint", "bearing",
        "sway bar", "stabilizer",
        "מיסב", "מחוונת",
        "מוט מייצב",       # stabilizer bar
        "מבודד רעש",       # noise isolator / bushing
        "סופג זעזועים",    # shock absorber (alternate)
        "ערסל",            # engine / suspension cradle
        "תמך",             # support bracket (suspension side)
        "תושבת גמישה",     # flexible mount
        "גומי מסילה",      # guide rail rubber
        "מתאם למוט",       # rod adapter
        "פין כדורי",       # ball pin
    ]),
    # --- Exhaust ---
    ("מערכת פליטה", [
        "אגזוז", "צינור פליטה", "צינור אגזוז", "קטליזטור", "מפלט",
        "סעפת פליטה", "סעפת קד",
        "ממיר קטליטי",   # catalytic converter
        "exhaust", "muffler", "catalytic", "manifold", "dpf", "egr",
        "סעפת ומ",       # manifold and converter assembly
    ]),
    # --- Cooling ---
    ("קירור", [
        "רדיאטור", "ראדיאטור", "מאוורר",  # ראדיאטור = alternate spelling
        "משאבת מים", "תרמוסטט",
        "מצנן",  # intercooler / cooler
        "ניפל מים", "ניפל למש",
        "קירור", "coolant", "radiator", "fan", "thermostat", "water pump",
        "intercooler",
        "בית טרמוסטט",     # thermostat housing
        "פלאנש מים",       # water flange / coolant neck
    ]),
    # --- Electrical ---
    ("חשמל", [
        "חיישן", "סטרטר", "מחולל", "אלטרנטור", "מצבר", "נורה", "נורת",
        "פיוז", "ממסר", "לוח שעונים", "מד מהירות", "חיל\"א", "alternator",
        "צמה",  # wiring harness
        "כבל חשמל", "כבל ח",
        "יחידה אלקטר", "יח' אלקטר", "יחידת",
        "אלקטרונ",  # electronic unit
        "בוקסה",  # electrical box / fuse box
        "hcu", "pcm", "bcm", "tcm",  # control modules
        "sensor", "starter", "battery", "bulb", "fuse", "relay",
        "abs", "ecu", "control unit", "actuator", "switch", "מתג",
        "מד", "מחוון", "logger", "module", "מודול",
        "כבל גז",  # throttle cable
        "שקע חשמל", "שקע לחוט", "שקע חיבור",   # electrical socket/connector
        "חוטי הצתה", "כבל הצתה",              # ignition wires
        "צמת הארקה", "הארקה",                 # ground strap/cable
        "נתיך",                                # fuse (Hebrew)
        "סרוומוטור",                           # servo motor
        "מסך מגע",                             # touch screen / infotainment
        "מחשב רכב",                            # car computer
        "יחידת בקרה",                          # control unit
        "ממשק תצוגה",
        "רב מודד",                             # multimeter / diagnostic
        "צמת חוטים", "קליפס לצמת",           # wiring harness + clips
        "מחבר חשמלי", "מחבר חוט",            # electrical connector
        "בית נתיכים",                          # fuse box housing
        "קופסא לרדיו", "רדיו",               # radio housing / radio
        "כבל לאנטנה", "מוט אנטנה",           # antenna cable / mast
        "גונב זרם",                           # circuit tester
        "תושבת להצתה",                        # ignition bracket
        "שקעUSB", "שקע usb",                 # USB socket
        "סוללה למעריכת ecall", "ecall",       # eCall backup battery
        "מנעול שלט", "מנעול (שלט",           # remote key lock
        "יחידה הידראולית",                    # hydraulic unit (ABS/ESP)
        "חיישן SBA", "sba",                   # seat belt alert sensor
        "פנל כיסוי למתח גבוה",               # HV cover panel (EV/hybrid)
    ]),
    # --- Fuel system ---
    ("מערכת דלק", [
        "משאבת דלק", "מזרק", "מסנן דלק", "מיכל דלק", "רגולטור לחץ",
        "injector", "fuel pump", "fuel filter", "carburet",
        "דלק", "בנזין", "throttle", "מצערת",
        "פלאנש לקרבורטור",  # carburetor flange
        "דוושת גז", "כבל לדוושת גז", "דוושת",  # gas pedal & cable
        "כבל הפעלה",                            # actuation cable (throttle/choke)
        "אינג'קטור", "אינגקטור",             # injector (Hebrew transliteration)
        "פידל גז",                            # throttle pedal
        "כבל אקסלרטור",                       # accelerator cable
        "ספק דלק",                            # fuel supply
    ]),
    # --- Filters & Oils ---
    ("מסננים ושמנים", [
        "מסנן", "פילטר", "שמן", "גריז", "נוזל",
        "filter", "oil", "grease", "fluid", "lubric",
        "רשת נפה", "רשת פלדה",
    ]),
    # --- Body / Exterior ---
    ("גוף ואקסטריור", [
        "פגוש", "כנף", "דלת", "מכסה מנוע", "גג", "ספוילר",
        "מראה", "ידית", "מגן", "פח", "תא מטען", "חלון", "שמשה",
        "גריל",  # grille
        "כיסוי וו", "וו גרירה",  # tow hook cover
        "לוחית רישוי", "תושבת למספר", "תושבת לוחית",  # licence plate
        "פס קישוט", "פס הדבקה",  # trim strips
        "כיסוי סטרייקר", "סטרייקר",  # striker cover
        "דופן",  # side panel
        "קורה",  # beam/rail
        "bumper", "fender", "hood", "door", "mirror", "panel",
        "spoiler", "body", "roof", "trunk", "window", "glass",
        "windshield", "grille",
        "סמל קד", "סמל אח", "סמל המ",  # front/rear/manufacturer badge
        "emblm", "emblem", "badge",
        "קישוט פינה", "קישוט זנב",  # corner trim, rear trim decoration
        "פלסטיק סף",   # door sill plastic
        "כיסוי פח",    # panel cover (exterior)
        "דפנה",        # body side panel
        "אחיזה",       # grip handle (exterior)
        "בית מספר",    # number plate housing
        "פס מרזב",     # rain gutter strip
        "סורג",        # grille bar / guard rail
        "קורת שילדה",  # chassis rail
        "קישוט סף",    # sill trim strip
        "פקק למרכב",   # body plug / grommet
        "כיסוי ארגז",  # trunk box cover (pickup)
        "כיסוי מרכב",  # underbody cover
        "סמל חזית", "EMELBME",  # front emblem
        "צופר",        # horn
    ]),
    # --- Lights ---
    ("תאורה", [
        "פנס", "מנורה", "נורה פנס", "פנס ראשי", "פנס אחורי",
        "דיודה", "led", "headlight", "taillight", "lamp", "light",
        "עדשה פנס", "מחזיר אור",
    ]),
    # --- Wipers ---
    ("מגבים", [
        "מגב", "זרוע מגב", "מנוע מגב",
        "wiper", "washer", "מתזים",  # מתזים = washers/sprayers
        "מיכל מתזים",
    ]),
    # --- Wheels & Tyres ---
    ("גלגלים וצמיגים", [
        "חישוק", "גלגל", "צמיג",
        "נבה",  # wheel hub
        "כפה לג'נט",  # wheel cap
        "tire", "wheel", "rim", "tyre", "hub",
        "nut", "אום",
        "רינגים",    # rings (wheel spacer rings / trim rings)
        "רולר נסיעה",  # running roller (drive/idler roller)
        "ג'אנט", "ג'נט",    # alloy wheel rim (jante)
        "ג'ק",              # car jack
        "טבור",             # wheel hub center
        "משקולת איזון",     # wheel balance weight
    ]),
    # --- AC & Heating ---
    ("מיזוג ומערכת חימום", [
        "מזגן", "מיזוג", "קומפרסור", "חימום", "תנור",
        "יחידת חום", "יוניט חום",  # heater unit
        "ac", "air conditioning", "compressor", "heater", "hvac",
        "condenser", "evaporator",
        "לוח פיקוד לבקרת חימ",  # heating control panel
    ]),
    # --- Interior ---
    ("פנים הרכב", [
        "מושב", "ריפוד", "שטיח", "כרית", "תקרה", "לוח מחוונים",
        "קונסולה",  # console
        "משענת",    # armrest / headrest
        "כיסוי פנימי", "כיסוי פלסטיק", "כיסוי קונסולה",
        "כיסוי דשבורד", "דשבורד", "דשבורט",
        "סוכך שמש",  # sun visor
        "תקרת הרכב",
        "seat", "interior", "carpet", "dashboard", "console",
        "airbag", "כרית אוויר",
        "חגורת בטיחות", "חגורת בטח", "עוגן חגורת",  # seatbelt / belt anchor
        "בטנה",         # lining / padding (interior panel)
        "חיפוי",        # interior lining / trim covering
        "כיסוי עמוד",   # pillar cover
        "מסילת כסא",    # seat rail / seat track
        "כיסוי רמקול",  # speaker cover/grille
        "תא חפצים",     # glove compartment
        "תפס לסך",      # sun visor clip
        "פלסטיק סף פנ", # inner sill plastic
        "כיסוי סף",     # sill cover
        "חיפוי תקרה",   # headliner
        "ידית חגורה",   # belt handle
        "מחזיק כוס",    # cup holder
        "מחזיק",        # holder / mount (generic interior piece)
        "כיסוי מרכזי",  # center console cover
        "מאפרה",         # ashtray
        "ספוג לכיסא",    # seat foam/cushion
        "רמקול",         # speaker
        "תא אחסון",      # storage compartment
        "גומי משקוף",    # door/window seal rubber
        "פלסטיק מוביל לחגורת",  # belt guide plastic
        "תריס אוורור",    # air vent blind / shutter
        "לוח מגע לשליטה", "לוח שליטה",  # control touch panel
        "סך שמש", "סוכך שמש",  # sun screen / sun shade
        "תא כפפות מכלל",  # glove box assembly
    ]),
    # --- Seals, Gaskets, Hardware ---
    ("אטמים וחומרים", [
        "אטם", "חוגר", "ברג", "צינור",
        "טבעת",  # ring / o-ring
        "טבעת אטימה",
        "seal", "gasket", "bolt", "nut", "screw", "clamp", "hose",
        "o-ring", "washer",
        "מרווח",         # spacer / shim
        "שיבה מרווח",    # shim spacer
        "מרווח לדפרניצ", # differential spacer
        "בורג",          # bolt/screw (Hebrew)
        "אום בורג",      # nut and bolt
        "ניקוי",         # cleaning material
        "חומר שטיפה",    # cleaning fluid
        "חומר הדבקה",    # adhesive / sealant
        "קליפס", "קלפסים",  # clips / fasteners
        "שייבה",         # washer/shim (alternate spelling)
        "שיבה נחושת",    # copper washer/shim
        "שיבה נעילה",    # locking shim
        "חבק",           # clamp / hose clamp
        "ניפל",          # nipple connector
        "סגר",           # closure / clip
        "פקק לקרטר",    # oil sump plug
        "פקק",           # cap / plug (generic)
        "גומיה לכיסוי",  # cover rubber seal
        "גומית אטימה",   # sealing rubber
        "SSAP EUGAB", "EFARGA",  # gasket / spacer (catalog codes)
    ]),
    # --- Chains & Belts ---
    ("שרשראות ורצועות", [
        "שרשרת", "רצועה", "גמארה",
        "מותח",   # tensioner (belt/chain tensioner)
        "פולי",   # pulley
        "belt", "chain", "timing belt", "tensioner", "pulley",
        "רצועת תזמון",  # timing belt
        "כיסוי רצועת",  # timing belt cover
        "סט רצועות",    # belt kit
        "שיבה נעילה לטימינג",  # timing lock shim
    ]),
    # --- Axle / Drive ---
    ("סרן והינע", [
        "פלאנג", "גל הינע", "גל הנע", "חיבור", "כרדן",
        "cv", "driveshaft",
        "halfshaft", "axle shaft", "propshaft",
        "תמסורת אוט",  # automatic gearbox assembly → could fit here too, but gearbox wins first
        "בורג לדריישפט",  # driveshaft bolt
        "דרישפט",         # driveshaft (transliteration)
    ]),
    # --- Tools & Workshop supplies ---
    ("כלים וציוד", [
        "כלי עבודה",   # tools
        "ג'ק",          # jack (listed here only if not wheels context)
        "מד לחץ",       # pressure gauge
        "טיפול",        # service kit (e.g. טיפול 1000 ל)
        "ספר נהג",      # driver manual
        "תקליטור",      # diagnostic CD
        "כפפות",        # work gloves
        "חומר שטיפה",  # duplicate of hardware — intentional for priority
    ]),
]

def classify_category(name: str) -> str:
    """
    Return best-fit category for a part based on its Hebrew/English name.
    Falls back to 'כללי' if no pattern matches.
    """
    if not name or name == "(ללא שם)":
        return "כללי"
    lower = name.lower()
    for category, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw.lower() in lower:
                return category
    return "כללי"


# ---------------------------------------------------------------------------
# FIX RUNNER
# ---------------------------------------------------------------------------
async def fix_all():
    engine = create_async_engine(DATABASE_URL, pool_size=5)
    print("=" * 60)
    print("DB Quality Fixer — starting")
    print("=" * 60)

    async with engine.begin() as conn:

        # ---- Fix 1: manufacturer aliases --------------------------------
        print("\n[1/4] Normalizing manufacturer aliases...")
        total_mfr = 0
        for alias, canonical in MANUFACTURER_ALIASES.items():
            r = await conn.execute(
                text("UPDATE parts_catalog SET manufacturer = :c WHERE manufacturer = :a"),
                {"c": canonical, "a": alias},
            )
            if r.rowcount:
                print(f"    {alias!r:20s} → {canonical!r}  ({r.rowcount:,} rows)")
                total_mfr += r.rowcount
        print(f"  Manufacturer fixes total: {total_mfr:,}")

        # ---- Fix 2: part_type mapping -----------------------------------
        print("\n[2/4] Normalizing part_type values...")
        total_pt = 0

        # Get all distinct raw values
        rows = (await conn.execute(
            text("SELECT DISTINCT part_type FROM parts_catalog")
        )).fetchall()
        distinct_types = [row[0] for row in rows if row[0] is not None]

        for raw in distinct_types:
            mapped = normalize_part_type(raw)
            if mapped != raw:  # needs change
                if mapped is None:
                    r = await conn.execute(
                        text("UPDATE parts_catalog SET part_type = NULL WHERE part_type = :r"),
                        {"r": raw},
                    )
                else:
                    r = await conn.execute(
                        text("UPDATE parts_catalog SET part_type = :m WHERE part_type = :r"),
                        {"m": mapped, "r": raw},
                    )
                print(f"    {repr(raw[:40]):45s} → {repr(mapped)}  ({r.rowcount:,} rows)")
                total_pt += r.rowcount
        print(f"  Part_type fixes total: {total_pt:,}")

        # ---- Fix 3: category from name ----------------------------------
        print("\n[3/4] Classifying category from part name (this may take a minute)...")

        # Fetch all part id+name in batches
        BATCH = 5000
        offset = 0
        updated = 0
        category_counts: dict[str, int] = {}

        while True:
            rows = (await conn.execute(
                text("SELECT id, name FROM parts_catalog ORDER BY id OFFSET :o LIMIT :l"),
                {"o": offset, "l": BATCH},
            )).fetchall()
            if not rows:
                break

            # Build category for each row
            updates: list[dict] = []
            for row_id, name in rows:
                cat = classify_category(name or "")
                category_counts[cat] = category_counts.get(cat, 0) + 1
                updates.append({"id": str(row_id), "cat": cat})

            # Bulk update using unnest trick
            await conn.execute(
                text("""
                    UPDATE parts_catalog p
                    SET category = u.cat
                    FROM (SELECT UNNEST(CAST(:ids AS UUID[])) AS id,
                                 UNNEST(CAST(:cats AS TEXT[])) AS cat) u
                    WHERE p.id = u.id
                """),
                {
                    "ids": [u["id"] for u in updates],
                    "cats": [u["cat"] for u in updates],
                },
            )
            updated += len(rows)
            offset += BATCH
            if updated % 50000 == 0:
                print(f"    ... {updated:,} processed")

        print(f"  Category classification done: {updated:,} rows")
        print("  Category distribution:")
        for cat, cnt in sorted(category_counts.items(), key=lambda x: -x[1]):
            print(f"    {cat:<30} {cnt:>8,}")

        # ---- Fix 4: deactivate unnamed parts ----------------------------
        print("\n[4/4] Flagging unnamed parts (name='(ללא שם)')...")
        r = await conn.execute(
            text("UPDATE parts_catalog SET is_active = false WHERE name = '(ללא שם)'")
        )
        print(f"  Deactivated {r.rowcount:,} unnamed parts")

    print("\n" + "=" * 60)
    print("All fixes committed.")
    print("=" * 60)

    # Summary check
    print("\nPost-fix verification:")
    async with engine.connect() as conn:
        r = await conn.execute(text("""
            SELECT
                (SELECT COUNT(DISTINCT category) FROM parts_catalog WHERE is_active) AS categories,
                (SELECT COUNT(DISTINCT manufacturer) FROM parts_catalog WHERE is_active) AS manufacturers,
                (SELECT COUNT(DISTINCT part_type) FROM parts_catalog WHERE is_active) AS part_types,
                (SELECT COUNT(*) FROM parts_catalog WHERE is_active = false) AS inactive
        """))
        row = r.fetchone()
        print(f"  Distinct categories:    {row[0]}")
        print(f"  Distinct manufacturers: {row[1]}")
        print(f"  Distinct part_types:    {row[2]}")
        print(f"  Inactive (unnamed):     {row[3]:,}")

        r = await conn.execute(text(
            "SELECT part_type, COUNT(*) FROM parts_catalog GROUP BY part_type ORDER BY COUNT(*) DESC"
        ))
        print("\n  part_type breakdown:")
        for row in r.fetchall():
            print(f"    {repr(row[0]):20s}  {row[1]:>8,}")

        r = await conn.execute(text(
            "SELECT category, COUNT(*) FROM parts_catalog WHERE is_active GROUP BY category ORDER BY COUNT(*) DESC LIMIT 20"
        ))
        print("\n  Top categories:")
        for row in r.fetchall():
            print(f"    {row[0]:<30}  {row[1]:>8,}")


# ---------------------------------------------------------------------------
# FAST RE-CLASSIFIER FOR 'כללי' PARTS ONLY
# ---------------------------------------------------------------------------
async def reclassify_kal():
    """Re-run category classification only on parts currently in 'כללי'.
    Much faster than full fix_all() — only processes ~68K rows."""
    engine = create_async_engine(DATABASE_URL, pool_size=5)
    print("=" * 60)
    print("Re-classifying 'כללי' parts with expanded keyword rules")
    print("=" * 60)

    async with engine.begin() as conn:
        BATCH = 5000
        offset = 0
        updated_cat = 0
        escaped_kal = 0
        category_counts: dict[str, int] = {}

        while True:
            rows = (await conn.execute(
                text("SELECT id, name FROM parts_catalog WHERE category='כללי' ORDER BY id OFFSET :o LIMIT :l"),
                {"o": offset, "l": BATCH},
            )).fetchall()
            if not rows:
                break

            updates = []
            for row_id, name in rows:
                cat = classify_category(name or "")
                category_counts[cat] = category_counts.get(cat, 0) + 1
                updates.append({"id": str(row_id), "cat": cat})
                if cat != "כללי":
                    escaped_kal += 1

            await conn.execute(
                text("""
                    UPDATE parts_catalog p
                    SET category = u.cat
                    FROM (SELECT UNNEST(CAST(:ids AS UUID[])) AS id,
                                 UNNEST(CAST(:cats AS TEXT[])) AS cat) u
                    WHERE p.id = u.id
                """),
                {
                    "ids": [u["id"] for u in updates],
                    "cats": [u["cat"] for u in updates],
                },
            )
            updated_cat += len(rows)
            offset += BATCH
            print(f"  ... {updated_cat:,} processed, {escaped_kal:,} re-classified so far")

        print(f"\nDone: {updated_cat:,} כללי parts processed")
        print(f"  Re-classified away from כללי: {escaped_kal:,}")
        print(f"  Remaining in כללי: {updated_cat - escaped_kal:,}")
        print("  Distribution of re-classified:")
        for cat, cnt in sorted(category_counts.items(), key=lambda x: -x[1]):
            if cat != "כללי":
                print(f"    {cat:<30} +{cnt:>6,}")


if __name__ == "__main__":
    import sys
    if "--kal" in sys.argv:
        asyncio.run(reclassify_kal())
    else:
        asyncio.run(fix_all())
