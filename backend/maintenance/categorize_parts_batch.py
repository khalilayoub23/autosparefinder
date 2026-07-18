#!/usr/bin/env python3
"""
Python-side batch categorizer — reads parts in 5K chunks, categorizes using
string matching in Python (fast, zero API cost, 2000+ parts/sec), then bulk-updates DB.

Combined approach (Option 2 of 3-option AI upgrade):
- This script: keyword rules — free, instant, no API, handles 90%+ of named parts
- enrich_pending_parts: Phi-3-mini via HF API — handles translation & hard edge cases
- hf_router_text: DistilBERT API — handles real-time search query normalization
"""
import asyncio, asyncpg, os, time

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
BATCH = 5000

# (category, [hebrew_substrings], [english_substrings_lowercase])
# Ordered by specificity — more specific rules first to avoid false matches.
RULES = [
    # ── Safety systems (highest priority — false negatives dangerous) ────────
    ("safety-systems",
     ["כרית אוויר", "חגורת בטיחות", "חגורה ב", "חיישן התנגשות", "מגן ראש"],
     ["airbag", "air bag", "seat belt", "seatbelt", "pretensioner", "safety belt",
      "crash sensor", "impact sensor", "curtain air", "knee airbag"]),

    # ── Brakes ───────────────────────────────────────────────────────────────
    ("brakes",
     ["בלמי", "בלם", "דיסק בלם", "רפידות", "ממסר בלם", "צנרת בלם", "קליפר",
      "כוס בלם", "מיכל נוזל בלם"],
     ["brake pad", "brake disc", "brake rotor", "brake caliper", "brake hose",
      "brake line", "brake cylinder", "brake fluid reservoir", "abs sensor",
      "wheel speed sensor", "park brake", "parking brake", "handbrake",
      "brake shoe", "drum brake", "disc brake"]),

    # ── Engine ───────────────────────────────────────────────────────────────
    ("engine",
     ["מנוע", "בוכנה", "גל ארכובה", "מצת", "שסתום", "אטם ראש", "מסנן שמן",
      "טבעת אטם", "מכסה שסתומים", "ברגי ראש", "שמן מנוע", "פלטה מנוע",
      "מחזיק מנוע", "הרכבת מנוע", "מגן תחתון", "אטם מנוע", "תוסף מנוע",
      "טיימינג", "גל הנוע", "טרבו", "מצנן שמן", "מסנן שמן"],
     ["piston", "head gasket", "crankshaft", "camshaft", "spark plug", "glow plug",
      "engine mount", "oil filter", "oil pump", "oil cap", "oil pan", "oil sump",
      "timing chain", "timing belt kit", "timing cover", "valve cover",
      "rocker arm", "connecting rod", "engine block", "cylinder head",
      "engine seal", "engine gasket", "turbocharger", "turbo", "supercharger",
      "intercooler pipe", "oil cooler", "thrust bearing", "main bearing",
      "engine bracket", "motor mount", "crankshaft seal", "camshaft seal",
      "oil pressure", "valve train", "lifter", "tappet", "pushrod",
      "flywheel bolt", "harmonic balancer", "crank pulley"]),

    # ── Fuel & Air ───────────────────────────────────────────────────────────
    ("fuel-air",
     ["משאבת דלק", "מסנן דלק", "מזרק", "מסנן אוויר", "גוף מצערת",
      "מיכל דלק", "צנרת דלק", "שסתום דלק", "נוזל דלק"],
     ["fuel pump", "fuel filter", "fuel injector", "fuel rail", "fuel tank",
      "fuel line", "fuel hose", "fuel cap", "fuel sender", "fuel pressure",
      "air filter", "air intake", "throttle body", "mass air flow", "maf sensor",
      "intake manifold", "map sensor", "idle control valve", "egr valve",
      "egr cooler", "pcv valve", "charcoal canister", "evap canister"]),

    # ── Air conditioning & Heating ───────────────────────────────────────────
    ("air-conditioning-heating",
     ["מזגן", "מדחס", "אידוי", "קונדנסור", "חימום", "מפוח", "תא נוסעים",
      "פילטר מזגן", "פילטר מבלט"],
     ["air conditioning", "air conditioner", "compressor", "evaporator",
      "condenser", "heater core", "blower motor", "hvac", "climate control",
      "a/c compressor", "ac compressor", "ac hose", "refrigerant", "cabin filter",
      "cabin air filter", "pollen filter", "blend door", "expansion valve",
      "receiver drier", "ac belt"]),

    # ── Cooling system ───────────────────────────────────────────────────────
    ("cooling",
     ["רדיאטור", "משאבת מים", "תרמוסטט", "מאוורר קירור", "נוזל קירור",
      "צנרת קירור", "מכסה רדיאטור", "כוס קירור", "צינור מים"],
     ["radiator", "water pump", "coolant", "thermostat", "cooling fan", "fan clutch",
      "intercooler", "expansion tank", "overflow tank", "coolant reservoir",
      "radiator cap", "radiator hose", "coolant hose", "water outlet",
      "water neck", "coolant sensor", "temperature sensor", "fan blade"]),

    # ── Exhaust ───────────────────────────────────────────────────────────────
    ("exhaust",
     ["פליטה", "מאיין", "ממיר קטליטי", "צינור פליטה", "ספייסר פליטה"],
     ["exhaust", "muffler", "silencer", "catalytic converter", "catalyst",
      "dpf", "particulate filter", "egr", "lambda sensor", "oxygen sensor",
      "o2 sensor", "exhaust manifold", "exhaust pipe", "exhaust gasket",
      "exhaust bracket", "exhaust hanger", "tailpipe", "downpipe"]),

    # ── Electrical & Sensors ─────────────────────────────────────────────────
    ("electrical-sensors",
     ["חיישן", "ממסר", "נתיך", "בקרה", "מצבר", "אלטרנטור", "מתנע",
      "כבל חשמל", "חישן", "מתג", "נורה", "ספק", "בוקסה", "מחבר",
      "ממסר", "לוח שקעים", "מוליך"],
     ["sensor", "relay", " fuse ", "fuse box", "fusebox", "control unit", "ecu",
      "module", "control module", "abs module", "alternator", "starter motor",
      "battery", "wire harness", "wiring harness", "connector", "socket",
      "switch", "ignition switch", "window switch", "mirror switch",
      "crankshaft sensor", "camshaft sensor", "knock sensor", "map sensor",
      "throttle position sensor", "tps", "coolant temp sensor", "speed sensor",
      "parking sensor", "reverse sensor", "horn", "relay box", "fuse link"]),

    # ── Lighting ──────────────────────────────────────────────────────────────
    ("lighting",
     ["פנס", "תאורה", "נורה", "רפלקטור", "לד", "תאורת", "פנסים",
      "פנס ערפל", "פנס ראשי", "פנס אחורי"],
     ["headlight", "headlamp", "tail light", "tail lamp", "taillight",
      "fog light", "fog lamp", "turn signal", "indicator", "daytime running",
      "drl", "brake light", "stop light", "reverse light", "backup light",
      "interior light", "dome light", " bulb ", " led ", "reflector",
      "side marker", "corner light", "flasher relay", "light assembly"]),

    # ── Wipers & Washers ─────────────────────────────────────────────────────
    ("wipers-washers",
     ["ממחק", "מגב", "שפריצר", "מיכל ממחקים", "זרוע מגב"],
     ["wiper blade", "wiper arm", "wiper motor", "wiper linkage",
      "windshield washer", "washer pump", "washer nozzle", "washer reservoir",
      "washer tank", "rear wiper", "wiper refill"]),

    # ── Suspension & Steering ────────────────────────────────────────────────
    ("suspension-steering",
     ["בולם", "קפיץ", "זרוע", "הגה", "מוט גלגול", "כדור מפרק", "צ'ופה",
      "מוט קשר", "גלגל הגה", "מנגנון הגה", "תמיכה", "מוט מייצב",
      "כרית", "מרפק", "ציר גלגל", "בוש"],
     ["shock absorber", "strut", "spring coil", "coil spring", "leaf spring",
      "control arm", "tie rod", "ball joint", "sway bar", "stabilizer bar",
      "stabilizer link", "anti-roll bar", "steering rack", "power steering",
      "steering pump", "steering column", "steering shaft", "steering boot",
      "rack boot", "cv boot", "suspension arm", "wishbone", "trailing arm",
      "subframe", "bushing", "bush ", "rubber mount", "adj. shock",
      "shock abs", "front shock", "rear shock", "strut mount", "strut bearing",
      "top mount", "front strut"]),

    # ── Wheels & Bearings ────────────────────────────────────────────────────
    ("wheels-bearings",
     ["מיסב", "נבה", "ציר הנעה", "גלגל", "חישוק", "מיסב גלגל"],
     ["wheel bearing", "hub bearing", "hub assembly", "wheel hub", "cv joint",
      "drive shaft", "half shaft", "axle shaft", "driveshaft", "prop shaft",
      "propshaft", "wheel bolt", "wheel nut", "lug nut", "wheel stud",
      "hub cap", "center cap", "abs ring", "tone ring"]),

    # ── Clutch & Drivetrain ──────────────────────────────────────────────────
    ("clutch-drivetrain",
     ["מצמד", "קלאץ", "גלגל תנופה", "דיסק מצמד", "לחצן מצמד"],
     ["clutch kit", "clutch disc", "clutch plate", "pressure plate", "flywheel",
      "dual mass flywheel", "release bearing", "throw-out bearing",
      "clutch fork", "clutch slave cylinder", "clutch master cylinder",
      "differential", "diff ", "transfer case", "propshaft", "cv joint boot",
      "driveshaft boot"]),

    # ── Gearbox & Transmission ───────────────────────────────────────────────
    ("gearbox",
     ["תיבת הילוכים", "גיר אוטומטי", "גיר ידני", "שמן גיר"],
     ["gearbox", "transmission", "gear box", "automatic transmission",
      "manual gearbox", "gear shift", "gear lever", "selector fork",
      "synchronizer", "transmission mount", "gearbox mount",
      "transmission oil", "gear oil", "atf fluid", "torque converter"]),

    # ── Belts & Chains ───────────────────────────────────────────────────────
    ("belts-chains",
     ["רצועה", "גלגלת", "שרשרת תזמון", "חגורה", "מתח רצועה", "גלגלת סרק"],
     ["timing belt", "cam belt", "serpentine belt", "poly v belt", "v-belt",
      "ribbed belt", "multi-rib belt", "drive belt", "belt kit",
      "timing chain", "timing chain kit", "chain tensioner", "chain guide",
      "tensioner pulley", "idler pulley", "belt tensioner", "accessory belt"]),

    # ── Interior & Comfort ───────────────────────────────────────────────────
    ("interior-comfort",
     ["מושב", "ריפוד", "לוח מחוונים", "ידית", "שטיח", "מחזיק",
      "כוסית", "מגש", "מסגרת פנים", "רפוד", "מנוף", "כיסוי",
      "אחיזה", "ציר דלת", "ידית דלת"],
     ["seat", "seat cover", "seat cushion", "seat back pad", "seat recliner",
      "recliner adjust", "recliner mechanism", "dashboard", "dash panel",
      "door panel", "door card", "door handle", "armrest", "headrest",
      "cup holder", "sun visor", "coat hook", "cargo cover", "floor mat",
      "carpet", "center console", "gear knob", "shift knob", "handbrake grip",
      "steering wheel cover", "pillar trim", "pillar molding", "door trim",
      "adjust knob", "dial knob", "cover reclining", "seat adjuster",
      "window regulator", "window motor", "window lifter"]),

    # ── Body & Exterior ──────────────────────────────────────────────────────
    ("body-exterior",
     ["פגוש", "כנף", "בונט", "גריל", "שמשה", "ראי", "ספוילר", "חלון",
      "דלת", "תא מטען", "סף", "גגון", "ויזר", "מכסה", "פח",
      "עצם רכב", "בטנה", "מדבקה", "לוחית", "עמוד A", "עמוד B"],
     ["bumper", "fender", "front fender", "rear fender", "wing", "hood",
      "bonnet", "trunk lid", "boot lid", "tailgate", "liftgate", "hatchback",
      "grille", "front grille", "radiator grille", "spoiler", "lip kit",
      "side skirt", "rocker panel", "windshield", "windscreen", "rear window",
      "side glass", "quarter glass", "fixed glass", "movable glass",
      "door glass", "window glass", "mirror glass", "side mirror",
      "door mirror", "rearview mirror", "mirror cover", "mirror housing",
      "pillar", "a pillar", "b pillar", "c pillar", "body panel",
      "body kit", "mud flap", "splash guard", "splash shield",
      "fender liner", "fender splash", "wheel arch", "wheel arch liner",
      "roof rail", "door sill", "step pad", "floor side rail",
      "body trim", "chrome trim", "molding", "clip set", "screw set",
      "fastener", "retainer clip", "cable hood", "hood release",
      "hood hinge", "door hinge", "door check", "door stop"]),
]


def categorize_part(name: str, name_he: str) -> str | None:
    n = (name or "").lower()
    nh = (name_he or "")
    for cat, he_kws, en_kws in RULES:
        if any(kw in nh for kw in he_kws):
            return cat
        if any(kw in n for kw in en_kws):
            return cat
    return None


async def main():
    conn = await asyncpg.connect(DB, statement_cache_size=0)
    t0 = time.monotonic()

    total = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE is_active AND category IN ('כללי','accessories')"
    )
    print(f"[catbatch] {total:,} parts to categorize", flush=True)

    categorized = 0
    unchanged = 0
    batch_num = 0
    empty_streak = 0
    max_batches = int(total / BATCH * 1.1) + 200

    while batch_num < max_batches:
        # LOCK-SAFE: claim a batch of UNLOCKED 'כללי' rows in one transaction with
        # FOR UPDATE SKIP LOCKED — so we never block on (and never get blocked by) the
        # concurrent harvester / db_update_agent writes on parts_catalog. Rows another
        # writer holds are simply skipped and picked up on a later pass. lock_timeout
        # is a belt-and-suspenders fail-fast if we ever do wait on a lock.
        updates: dict[str, list] = {}
        unmatched_ids = []
        n = 0
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL lock_timeout = '4s'")
                rows = await conn.fetch(
                    "SELECT id, name, name_he FROM parts_catalog "
                    "WHERE is_active AND category IN ('כללי','accessories') "
                    "LIMIT $1 FOR UPDATE SKIP LOCKED",
                    BATCH,
                )
                n = len(rows)
                for r in rows:
                    cat = categorize_part(r['name'] or '', r['name_he'] or '')
                    if cat:
                        updates.setdefault(cat, []).append(r['id'])
                    else:
                        unmatched_ids.append(r['id'])
                for cat, ids in updates.items():
                    await conn.execute(
                        "UPDATE parts_catalog SET category=$1, updated_at=NOW() WHERE id=ANY($2::uuid[])",
                        cat, ids)
                    categorized += len(ids)
                if unmatched_ids:
                    await conn.execute(
                        "UPDATE parts_catalog SET category='general', updated_at=NOW() WHERE id=ANY($1::uuid[])",
                        unmatched_ids)
                    unchanged += len(unmatched_ids)
        except (asyncpg.exceptions.LockNotAvailableError, asyncpg.exceptions.QueryCanceledError):
            await asyncio.sleep(3)   # contention — back off and retry
            continue

        # 0 rows = either done, OR every remaining row is momentarily locked by another
        # writer. Distinguish: if the real count is still high, wait and retry; else stop.
        if n == 0:
            empty_streak += 1
            still = await conn.fetchval(
                "SELECT COUNT(*) FROM parts_catalog WHERE is_active AND category IN ('כללי','accessories')")
            if still > 500 and empty_streak < 40:
                print(f"  (all remaining {still:,} locked by other writers — waiting 15s)", flush=True)
                await asyncio.sleep(15)
                continue
            break
        empty_streak = 0
        batch_num += 1

        elapsed = time.monotonic() - t0
        if batch_num % 20 == 0 or batch_num <= 5:
            print(f"  batch {batch_num}: categorized={categorized:,} moved_to_general={unchanged:,} [{elapsed:.0f}s]",
                  flush=True)

    remaining = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE is_active AND category IN ('כללי','accessories')"
    )
    elapsed = time.monotonic() - t0
    print(f"[catbatch] DONE: {categorized:,} categorized, {unchanged:,} general, {remaining:,} remaining ({elapsed:.0f}s)",
          flush=True)
    await conn.close()


asyncio.run(main())
