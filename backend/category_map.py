"""
category_map.py — THE SINGLE SOURCE OF TRUTH for part categorization.

Merged 2026-07-27 from what used to be five separate, mutually-inconsistent places:
  • category_map.py          — RULES / VARIANT_MAP / CATEGORY_SLUG_MAP
  • categories.py            — _HEBREW_SUPPLEMENT (~200 Hebrew phrases) +
                               guess_category_by_text() + a taxonomy-derived CATEGORY_MAP
  • db_update_agent.py       — its own CATEGORY_MAP + CATEGORY_NAME_REMAP
  • part_type_taxonomy.py    — family/subcategory labels, aliases, legacy names
  • 6 importers              — private CATEGORY_MAP dicts (several with real errors)

WHY THE MERGE WAS NEEDED — three incompatible vocabularies were in play:
  A. English slugs      → 'brakes', 'body-exterior', 'כללי'   ← what parts_catalog.category
                                                                 ACTUALLY stores, what search
                                                                 filters on, what importers write
  B. Hebrew display     → 'בלמים', 'מערכת דלק'                ← db_update_agent's map targets
  C. Title-Case labels  → 'Air Filters', 'Timing Belts' (131)  ← db_update_agent's "canonical" set
`normalize_categories` mapped raw → B but only kept branches whose target was in C. A Hebrew
target is never in an English-label set, so nearly every branch was silently DISCARDED; the only
survivors were `general|misc|other|אחר → כללי`. That is why parts flowed INTO 'כללי' and never
out. VOCABULARY A IS THE TRUTH. B is display-only (DISPLAY). C is now just VARIANT_MAP input.

MATCHING — longest keyword wins, not "whichever category is listed first".
The old code returned the first category whose keyword list hit, so a generic word in an early
category shadowed a specific phrase in a later one ('שמן'→fluids beat 'מסנן שמן'→filters;
'בולם'→suspension beat 'בולם הגה'). _FLAT_RULES is sorted by keyword length DESC (stable, so
RULES order breaks ties), and the first hit wins — longest, most-specific match always.

SIGNAL PRIORITY in categorize():
  1. existing_category already canonical           → keep it
  2. existing_category in VARIANT_MAP              → mapped canonical
  3. URL slug → CATEGORY_SLUG_MAP                  (car-parts.ie encodes category in the path)
  4. tyre/wheel size pattern                       → wheels-bearings
  5. keyword pass over name + name_he (HE/AR/EN)   → longest match wins
  6. 'כללי'                                        — the ONE catch-all, last resort

NEVER return 'general', 'service-general', 'accessories' or 'tools-equipment' as a FALLBACK.
They are real categories reachable only by a genuine keyword/slug match. The only fallback is
'כללי'. If you add a keyword, add it HERE — nowhere else.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from part_type_taxonomy import PART_TYPE_FAMILIES

# ── Character-class / pattern helpers ─────────────────────────────────────────
# RTL = Hebrew (U+0590-U+05FF) OR Arabic (U+0600-U+06FF, U+0750-U+077F).
# Both alphabets legitimately appear in `name` AND `name_he` in this catalog.
_RTL_RE = re.compile(r"[֐-׿؀-ۿݐ-ݿ]")
# Tyre / wheel sizes: "225/45r17", "P255 65R17", "18x8", "17x7.5"
# Tyre / wheel sizes: "225/45R17", "P255 65R17", "245/45ZR18", "225 35ZR 17",
# "18x8", "35X12.50R17". NOTE `[rz]{1,2}`: speed-rated tyres write TWO letters
# ("35ZR17"), and a single-letter class silently missed every ZR tyre in the
# catalog — found 2026-07-28 when an embedding benchmark filed "225 35zr 17"
# under electrical-sensors because the tyre rule never fired.
_TYRE_RE = re.compile(
    r"\b(p?\d{3}\s*[/ ]\s*\d{2}\s*[rz]{1,2}\s*\d{2}|\d{2}x\d[.\d]*)\b",
    re.IGNORECASE)

# ── Canonical categories ──────────────────────────────────────────────────────
# DERIVED from part_type_taxonomy family ids so the two can never drift apart,
# plus 'כללי' (the catch-all, which is not a taxonomy family).
CATCH_ALL = "כללי"
CANONICAL: frozenset[str] = frozenset({f.id for f in PART_TYPE_FAMILIES} | {CATCH_ALL})

# Buckets that were historically used as importer FALLBACKS and are therefore
# full of miscategorized parts. Recategorization passes scan exactly these.
# ('service-general' / 'accessories' are legitimate categories — but a part only
#  belongs there on a real keyword match, never by default.)
BAD_FALLBACK_BUCKETS: Tuple[str, ...] = (
    CATCH_ALL, "general", "service-general", "accessories", "tools-equipment",
)

# ── Display names (UI ONLY — never stored in parts_catalog.category) ──────────
# This is where the old Hebrew vocabulary (db_update_agent.CATEGORY_MAP targets)
# now lives. Storing a display name in the category column is a bug.
DISPLAY: Dict[str, Dict[str, str]] = {
    "filters":                  {"he": "מסננים",            "ar": "الفلاتر",              "en": "Filters"},
    "fluids":                   {"he": "שמנים ונוזלים",     "ar": "الزيوت والسوائل",      "en": "Fluids & Lubricants"},
    "belts-chains":             {"he": "רצועות ושרשראות",   "ar": "السيور والجنازير",     "en": "Belts & Chains"},
    "service-general":          {"he": "ערכות שירות",       "ar": "أطقم الصيانة",         "en": "Service Kits"},
    "engine":                   {"he": "מנוע",              "ar": "المحرك",               "en": "Engine"},
    "cooling":                  {"he": "מערכת קירור",       "ar": "نظام التبريد",         "en": "Cooling"},
    "fuel-air":                 {"he": "מערכת דלק ואוויר",  "ar": "الوقود والهواء",       "en": "Fuel & Air"},
    "exhaust":                  {"he": "מערכת פליטה",       "ar": "العادم",               "en": "Exhaust"},
    "clutch-drivetrain":        {"he": "מצמד והנעה",        "ar": "الكلتش ونقل الحركة",   "en": "Clutch & Drivetrain"},
    "gearbox":                  {"he": "תיבת הילוכים",      "ar": "علبة التروس",          "en": "Gearbox"},
    "brakes":                   {"he": "בלמים",             "ar": "الفرامل",              "en": "Brakes"},
    "suspension-steering":      {"he": "מתלים והיגוי",      "ar": "التعليق والتوجيه",     "en": "Suspension & Steering"},
    "wheels-bearings":          {"he": "גלגלים ומיסבים",    "ar": "العجلات والرولمان",    "en": "Wheels & Bearings"},
    "body-exterior":            {"he": "גוף הרכב",          "ar": "الهيكل الخارجي",       "en": "Body & Exterior"},
    "lighting":                 {"he": "תאורה",             "ar": "الإضاءة",              "en": "Lighting"},
    # Renamed 2026-08-02 (owner): SENSORS were merged INTO electrical, and the
    # label is plain "electrical" — nothing else. Do NOT reintroduce
    # "electronics" here: electronics (ECUs, modules, amplifiers, infotainment)
    # is a DIFFERENT thing from electrical (wiring, fuses, relays, switches),
    # and the owner approved a two-way merge, not a three-way one.
    "electrical":       {"he": "חשמל",              "ar": "الكهرباء",              "en": "Electrical"},
    "audio-electronics":{"he": "שמע ואלקטרוניקה",   "ar": "الصوتيات والإلكترونيات", "en": "Audio & Electronics"},
    "air-conditioning-heating": {"he": "מזגן וחימום",       "ar": "التكييف والتدفئة",     "en": "A/C & Heating"},
    "wipers-washers":           {"he": "שמשות ומגבים",      "ar": "المساحات والغسيل",     "en": "Wipers & Washers"},
    "safety-systems":           {"he": "מערכות בטיחות",     "ar": "أنظمة الأمان",         "en": "Safety Systems"},
    "hybrid-ev":                {"he": "היברידי וחשמלי",    "ar": "الهجين والكهربائي",    "en": "Hybrid & EV"},
    "interior-comfort":         {"he": "פנים הרכב",         "ar": "المقصورة الداخلية",    "en": "Interior & Comfort"},
    "accessories":              {"he": "אביזרים",           "ar": "الإكسسوارات",          "en": "Accessories"},
    "merchandise":              {"he": "מוצרי מיתוג",       "ar": "منتجات دعائية",        "en": "Merchandise"},
    CATCH_ALL:                  {"he": "כללי",              "ar": "عام",                  "en": "General"},
}


def display_name(category: str, lang: str = "he") -> str:
    """Human-readable category name. NEVER write this back to the DB."""
    return DISPLAY.get(category, {}).get(lang, category)


# ── URL-slug map (car-parts.ie encodes the category as the last path segment) ─
CATEGORY_SLUG_MAP: Dict[str, str] = {
    "brake-discs": "brakes", "brake-pads": "brakes", "brake-drums": "brakes",
    "brake-calipers": "brakes", "brake-hoses": "brakes",
    "brake-master-cylinder": "brakes", "wheel-cylinders": "brakes",
    "handbrake-cables": "brakes", "parking-brake": "brakes",
    "shock-absorbers": "suspension-steering", "springs": "suspension-steering",
    "control-arms": "suspension-steering", "ball-joints": "suspension-steering",
    "tie-rod-ends": "suspension-steering", "steering-rack": "suspension-steering",
    "anti-roll-bar": "suspension-steering", "suspension-bushes": "suspension-steering",
    "steering-pump": "suspension-steering", "steering-column": "suspension-steering",
    "wheel-bearings": "wheels-bearings", "wheel-hub": "wheels-bearings",
    "abs-ring": "wheels-bearings", "hub-assembly": "wheels-bearings",
    "drive-shafts": "clutch-drivetrain", "cv-joints": "clutch-drivetrain",
    "clutch-kit": "clutch-drivetrain", "flywheel": "clutch-drivetrain",
    "differential": "clutch-drivetrain", "propshaft": "clutch-drivetrain",
    "gearbox-oil": "gearbox", "manual-gearbox": "gearbox",
    "automatic-gearbox": "gearbox", "gear-shift": "gearbox",
    "transmission-mount": "gearbox", "torque-converter": "gearbox",
    "engine-oil": "fluids", "coolant": "fluids", "brake-fluid": "fluids",
    "gear-oil": "fluids", "atf": "fluids", "hydraulic-oil": "fluids",
    "oil-filter": "filters", "air-filter": "filters", "fuel-filter": "filters",
    "pollen-filter": "filters", "cabin-filter": "filters",
    "particle-filter": "filters", "dpf-filter": "filters",
    "alternator": "electrical", "starter-motor": "electrical",
    "sensors": "electrical", "lambda-sensor": "electrical",
    "abs-sensor": "electrical", "camshaft-sensor": "electrical",
    "crankshaft-sensor": "electrical", "battery": "electrical",
    "relay": "electrical", "fuse-box": "electrical",
    "ecu": "electrical", "control-module": "electrical",
    "radiator": "cooling", "thermostat": "cooling", "water-pump": "cooling",
    "cooling-fan": "cooling", "coolant-pipe": "cooling",
    "intercooler": "cooling", "expansion-tank": "cooling",
    "fuel-pump": "fuel-air", "injectors": "fuel-air", "carburettor": "fuel-air",
    "intake-manifold": "fuel-air", "throttle-body": "fuel-air",
    "fuel-tank": "fuel-air", "fuel-line": "fuel-air", "fuel-cap": "fuel-air",
    "turbocharger": "fuel-air", "egr-valve": "exhaust",
    "catalytic-converter": "exhaust", "exhaust-pipe": "exhaust",
    "muffler": "exhaust", "dpf": "exhaust", "exhaust-manifold": "exhaust",
    "exhaust-gasket": "exhaust", "exhaust-bracket": "exhaust",
    "timing-belt": "belts-chains", "timing-chain": "belts-chains",
    "cam-belt": "belts-chains", "serpentine-belt": "belts-chains",
    "belt-tensioner": "belts-chains", "idler-pulley": "belts-chains",
    "chain-tensioner": "belts-chains",
    "camshaft": "engine", "crankshaft": "engine", "pistons": "engine",
    "engine-mount": "engine", "cylinder-head-gasket": "engine",
    "spark-plug": "engine", "glow-plugs": "engine", "oil-pump": "engine",
    "oil-pan": "engine", "valve-cover": "engine",
    "headlights": "lighting", "tail-lights": "lighting", "fog-lights": "lighting",
    "bulbs": "lighting", "indicators": "lighting", "drl": "lighting",
    "reverse-light": "lighting", "dome-light": "lighting",
    "wiper-blades": "wipers-washers", "wiper-motor": "wipers-washers",
    "washer-pump": "wipers-washers", "washer-reservoir": "wipers-washers",
    "wiper-arm": "wipers-washers", "wiper-linkage": "wipers-washers",
    "bonnet": "body-exterior", "bumper": "body-exterior", "wing": "body-exterior",
    "door": "body-exterior", "boot-lid": "body-exterior", "mirror": "body-exterior",
    "windscreen": "body-exterior", "window-glass": "body-exterior",
    "tailgate": "body-exterior", "fender": "body-exterior", "grille": "body-exterior",
    "mud-flap": "body-exterior", "splash-guard": "body-exterior",
    "window-regulator": "body-exterior", "door-handle": "body-exterior",
    "mirror-glass": "body-exterior", "body-panel": "body-exterior",
    "air-conditioning": "air-conditioning-heating",
    "ac-compressor": "air-conditioning-heating",
    "heater-matrix": "air-conditioning-heating", "heater-core": "air-conditioning-heating",
    "blower-motor": "air-conditioning-heating", "evaporator": "air-conditioning-heating",
    "condenser": "air-conditioning-heating", "expansion-valve": "air-conditioning-heating",
    "receiver-drier": "air-conditioning-heating",
    "seat": "interior-comfort", "interior-trim": "interior-comfort",
    "dashboard": "interior-comfort", "door-panel": "interior-comfort",
    "floor-mat": "interior-comfort", "armrest": "interior-comfort",
    "airbag": "safety-systems", "seat-belt": "safety-systems",
    "srs": "safety-systems", "pretensioner": "safety-systems",
    "hybrid-battery": "hybrid-ev", "ev-charger": "hybrid-ev",
    "inverter": "hybrid-ev", "traction-motor": "hybrid-ev",
}

# ── VARIANT_MAP — any messy/legacy/foreign label → canonical slug ─────────────
# Hand-written entries. Taxonomy labels/aliases/legacy names are folded in
# programmatically below (_seed_variants_from_taxonomy) so they can never drift.
VARIANT_MAP: Dict[str, str] = {
    # ── catch-alls: these must normalize to כללי, never stay as a bucket ──────
    "general": CATCH_ALL, "general parts": CATCH_ALL, "auto parts": CATCH_ALL,
    "misc": CATCH_ALL, "miscellaneous": CATCH_ALL, "other": CATCH_ALL,
    "אחר": CATCH_ALL, "כללי": CATCH_ALL, "multi-category": CATCH_ALL,
    "uncategorized": CATCH_ALL, "unknown": CATCH_ALL, "n/a": CATCH_ALL,
    "service & general": CATCH_ALL,

    # ── brakes ───────────────────────────────────────────────────────────────
    "brakes": "brakes", "brake": "brakes", "brake pads": "brakes",
    "brake rotors": "brakes", "brake system": "brakes", "braking system": "brakes",
    "brake systems": "brakes", "calipers": "brakes", "brakes-clutch": "brakes",
    "disc brake": "brakes", "בלמים": "brakes", "בלם": "brakes",

    # ── body & exterior ──────────────────────────────────────────────────────
    "body": "body-exterior", "body parts": "body-exterior",
    "body hardware": "body-exterior", "auto body systems": "body-exterior",
    "doors": "body-exterior", "door": "body-exterior", "bumper": "body-exterior",
    "bumpers": "body-exterior", "fender": "body-exterior", "fenders": "body-exterior",
    "hood": "body-exterior", "hoods": "body-exterior", "auto glass": "body-exterior",
    "window": "body-exterior", "windows": "body-exterior",
    "window components": "body-exterior", "window regulators": "body-exterior",
    "mirror": "body-exterior", "mirrors": "body-exterior",
    "running board": "body-exterior", "lock set": "body-exterior",
    "safety": "body-exterior", "safety components": "body-exterior",
    "פחיין ומרכב": "body-exterior", "גוף הרכב": "body-exterior",

    # ── engine ───────────────────────────────────────────────────────────────
    "engine": "engine", "engine parts": "engine", "engine components": "engine",
    "engine systems": "engine", "engine assembly": "engine",
    "engine support": "engine", "engine mechanical": "engine",
    "engine rebuild": "engine", "engine block": "engine",
    "engine bearings": "engine", "engine fasteners": "engine",
    "engine gaskets": "engine", "cylinder head": "engine",
    "gaskets": "engine", "gasket": "engine", "gasket kit": "engine",
    "gaskets & seals": "engine", "gaskets and seals": "engine",
    "rebuild kits": "engine", "timing components": "engine",
    "vacuum system": "engine", "ignition": "engine", "engine mount": "engine",
    "crankshaft": "engine", "liner": "engine", "bolt": "engine",
    "motor": "engine", "מנוע": "engine",

    # ── cooling ──────────────────────────────────────────────────────────────
    "cooling": "cooling", "cooling system": "cooling", "cooling systems": "cooling",
    "cooling-system": "cooling",
    "cooling kits": "cooling", "engine cooling": "cooling",
    "engine oil cooling": "cooling", "radiator": "cooling", "radiators": "cooling",
    "water pump": "cooling", "water pumps": "cooling",
    "thermostat": "cooling", "thermostats": "cooling",
    "מערכת קירור": "cooling", "קירור": "cooling",

    # ── fuel & air ───────────────────────────────────────────────────────────
    "fuel": "fuel-air", "fuel system": "fuel-air", "fuel-system": "fuel-air",
    "fuel & air": "fuel-air", "fuel and air": "fuel-air",
    "air and fuel delivery": "fuel-air", "fuel delivery": "fuel-air",
    "fuel injection": "fuel-air", "fuel injector": "fuel-air",
    "fuel injectors": "fuel-air", "fuel pump": "fuel-air", "fuel pumps": "fuel-air",
    "carburetors": "fuel-air", "nozzle": "fuel-air", "pump": "fuel-air",
    "turbo system": "fuel-air", "turbocharger": "fuel-air",
    "turbocharging": "fuel-air", "turbo components": "fuel-air",
    "מערכת דלק": "fuel-air", "דלק": "fuel-air",

    # ── exhaust ──────────────────────────────────────────────────────────────
    "exhaust": "exhaust", "exhaust system": "exhaust", "engine exhaust": "exhaust",
    "emissions": "exhaust", "מערכת פליטה": "exhaust", "פליטה": "exhaust",

    # ── suspension & steering ────────────────────────────────────────────────
    "suspension": "suspension-steering", "suspension & steering": "suspension-steering",
    "suspension/brakes": "suspension-steering", "air suspension": "suspension-steering",
    "steering": "suspension-steering", "steering systems": "suspension-steering",
    "shocks & struts": "suspension-steering", "control arm": "suspension-steering",
    "control arms": "suspension-steering", "tie rods & joints": "suspension-steering",
    "היגוי": "suspension-steering", "מתלים והיגוי": "suspension-steering",

    # ── wheels & bearings ────────────────────────────────────────────────────
    "wheel": "wheels-bearings", "wheels": "wheels-bearings",
    "wheels & tires": "wheels-bearings", "wheels and tires": "wheels-bearings",
    "suspension/wheels": "wheels-bearings", "tyres": "wheels-bearings",
    "tires": "wheels-bearings", "bearings": "wheels-bearings",
    "bearing": "wheels-bearings", "wheel bearings & hubs": "wheels-bearings",
    "גלגלים": "wheels-bearings", "צמיגים": "wheels-bearings",
    "גלגלים וצמיגים": "wheels-bearings",

    # ── clutch & drivetrain ──────────────────────────────────────────────────
    "clutch": "clutch-drivetrain", "drivetrain": "clutch-drivetrain",
    "driveline & axles": "clutch-drivetrain", "driveshafts": "clutch-drivetrain",
    "transfer case": "clutch-drivetrain",

    # ── gearbox ──────────────────────────────────────────────────────────────
    "transmission": "gearbox", "transmission systems": "gearbox",
    "automatic transaxle": "gearbox", "automatic transmission": "gearbox",
    "manual transmission": "gearbox", "gearbox": "gearbox", "gear": "gearbox",
    "תיבת הילוכים": "gearbox", "גיר": "gearbox",

    # ── electrical (SENSORS merged in — owner decision 2026-08-02) ───────────
    # sensors -> electrical. ELECTRONICS is a separate destination
    # (audio-electronics) — see the block below.
    "electrical": "electrical", "electric": "electrical",
    "electrical components": "electrical",
    "auto electrical systems": "electrical", "engine control": "electrical",
    "sensor": "electrical", "sensors": "electrical",
    "solenoids": "electrical", "tpms": "electrical",
    "ignition coil": "electrical", "oxygen sensor": "electrical",
    "wiring & modules": "electrical",
    # ── audio & electronics (owner decision 2026-08-02) ──────────────────────
    # Electronics is NOT electrical and must not be folded into it, but it also
    # must not fall to the catch-all — it has its own family.
    "electronics": "audio-electronics", "audio": "audio-electronics",
    "audio & electronics": "audio-electronics", "infotainment": "audio-electronics",
    "multimedia": "audio-electronics", "stereos & audio": "audio-electronics",
    "אלקטרוניקה": "audio-electronics", "מולטימדיה": "audio-electronics",
    "cameras & gps": "audio-electronics", "batteries & power": "electrical",
    "alternators & starters": "electrical", "electrical-lighting": "electrical",
    "חשמל": "electrical", "חשמל רכב": "electrical",
    "חשמל ואלקטרוניקה": "electrical",

    # ── lighting ─────────────────────────────────────────────────────────────
    "lighting": "lighting", "lighting accessories": "lighting",
    "headlights": "lighting", "headlamp": "lighting", "tail lights": "lighting",
    "fog lights": "lighting", "תאורה": "lighting",

    # ── interior ─────────────────────────────────────────────────────────────
    "interior": "interior-comfort", "interior systems": "interior-comfort",
    "interior accessories": "interior-comfort", "seats": "interior-comfort",
    "ריפוד ופנים": "interior-comfort", "פנים הרכב": "interior-comfort",

    # ── a/c & heating ────────────────────────────────────────────────────────
    "hvac": "air-conditioning-heating", "ac": "air-conditioning-heating",
    "a/c & heating": "air-conditioning-heating", "climate": "air-conditioning-heating",
    "climate control": "air-conditioning-heating",
    "air conditioning": "air-conditioning-heating",
    "heating and air conditioning": "air-conditioning-heating",
    "a/c compressors": "air-conditioning-heating", "condensers": "air-conditioning-heating",
    "מיזוג": "air-conditioning-heating", "מזגן וחימום": "air-conditioning-heating",

    # ── wipers ───────────────────────────────────────────────────────────────
    "wiper": "wipers-washers", "wipers": "wipers-washers",
    "wipers and washers": "wipers-washers", "wipers & washers": "wipers-washers",
    "wiper blades": "wipers-washers", "מגב": "wipers-washers",
    "מגבים": "wipers-washers", "שמשות ומגבים": "wipers-washers",

    # ── filters ──────────────────────────────────────────────────────────────
    "filter": "filters", "filters": "filters", "filtration": "filters",
    "oil filters": "filters", "air filters": "filters", "engine filters": "filters",
    "engine filter": "filters", "filters-oils": "filters", "filters-oil": "filters",
    "מסננים": "filters", "סינון": "filters", "פילטרים": "filters",

    # ── fluids ───────────────────────────────────────────────────────────────
    "fluids": "fluids", "oils and fluids": "fluids", "oils & fluids": "fluids",
    "lubricants": "fluids", "שמנים ונוזלים": "fluids",

    # ── belts & chains ───────────────────────────────────────────────────────
    "belts": "belts-chains", "belt & pulley": "belts-chains",
    "belts & pulleys": "belts-chains", "belts and cooling": "belts-chains",
    "timing belts": "belts-chains", "שרשראות ורצועות": "belts-chains",
    "רצועות תזמון": "belts-chains", "רצועות ושרשראות": "belts-chains",

    # ── safety ───────────────────────────────────────────────────────────────
    "safety systems": "safety-systems", "srs": "safety-systems",
    "airbags": "safety-systems", "מערכות בטיחות": "safety-systems",

    # ── accessories / tools (real categories, never fallbacks) ───────────────
    "accessories-audio-video": "accessories", "tuning": "accessories",
    "אביזרים": "accessories",
    # 'tools-equipment' is a legacy DB value with no taxonomy family behind it
    # (148 rows). It is NOT canonical; it normalizes into accessories.
    "tools-equipment": "accessories", "tools": "accessories",
    "equipment": "accessories", "כלים וציוד": "accessories",

    # ── hybrid / EV ──────────────────────────────────────────────────────────
    "hybrid": "hybrid-ev", "ev": "hybrid-ev", "electric vehicle": "hybrid-ev",
    "היברידי וחשמלי": "hybrid-ev",
}


def _seed_variants_from_taxonomy() -> None:
    """
    Fold every part_type_taxonomy label / alias / legacy name into VARIANT_MAP so
    the taxonomy vocabulary ('Air Filters', 'Timing Belts', legacy Hebrew names)
    resolves to canonical slugs instead of being a competing third vocabulary.
    Hand-written entries above always win (setdefault).
    """
    for fam in PART_TYPE_FAMILIES:
        terms = [fam.label, fam.id, *fam.aliases, *fam.legacy_categories]
        for sub in fam.subcategories:
            terms.extend([sub.label, sub.id, *sub.aliases])
        for term in terms:
            key = (term or "").strip().lower()
            if key:
                VARIANT_MAP.setdefault(key, fam.id)


_seed_variants_from_taxonomy()


# ── Keyword RULES — (canonical, [hebrew+arabic substrings], [english substrings])
# RTL keywords are matched against name_he (and against `name` when name_he is
# empty but `name` contains RTL text — 695K parts in this catalog are like that).
# English keywords are matched against the lowercased name + URL slug.
# ORDER only breaks ties: the LONGEST matching keyword wins (see _FLAT_RULES).
RULES: List[Tuple[str, List[str], List[str]]] = [
    # ── safety systems ───────────────────────────────────────────────────────
    ("safety-systems",
     ["כרית אוויר", "כרית בטיחות", "מחשב כרית", "חגורת בטיחות", "חגורת ביטחון",
      "חגורות בטיחות", "חגורה ב",
      "חיישן התנגשות", "מגן ראש", "מגן עמוד", "מותחן חגורה",
      "وسادة هوائية", "وسادة الهواء", "حزام أمان", "حزام الأمان", "حزام مقعد",
      "كيس هوائي", "حساس الاصطدام", "شداد حزام"],
     ["airbag", "air bag", "seat belt", "seatbelt", "pretensioner", "safety belt",
      "crash sensor", "impact sensor", "curtain air", "knee airbag", "srs ",
      "roll cage", "airbag module", "airbag clock spring",
      "clock spring", "belt buckle", "belt tensioner assy"]),

    # ── merchandise ──────────────────────────────────────────────────────────
    # Branded apparel / lifestyle goods the importers sell next to real parts.
    # Placed FIRST so that on an exact length tie a merch term wins over a parts
    # term — 'חולצת פולו' must never land in a mechanical family (a Porsche polo
    # was filed as `engine`, a women's shirt as `כללי`). Specificity still rules:
    # longer parts keywords beat these, which is why 'מגן' etc. are absent here.
    ("merchandise",
     ["חולצת טריקו", "חולצת פולו", "מחזיק מפתחות", "דגם מוקטן", "מארז גרביים",
      "חולצת נשים", "חולצת גברים", "חולצת ילדים", "בגד ים", "תיק גב",
      "סווטשירט", "מכנסיים", "גרביים", "כובע מצחייה", "חולצה", "פולו",
      "מעיל", "ג'קט", "סווטשרט", "כובע", "צעיף", "כפפות צמר", "מטריה",
      "ספל", "כוס תרמית", "בקבוק מים", "עט", "מחברת", "מגנט למקרר",
      "דובון", "בובה", "משחק", "פאזל", "תיק", "ארנק", "שעון יד",
      "قميص", "قبعة", "حقيبة", "ميدالية مفاتيح", "كوب",
      ],
     ["t-shirt", "tshirt", "polo shirt", "polo", "sweatshirt", "hoodie",
      "jacket", "cap", "beanie", "scarf", "gloves wool", "umbrella",
      "keychain", "key ring", "key fob accessory", "lanyard",
      "mug", "thermal cup", "water bottle", "pen", "notebook", "fridge magnet",
      "model car", "scale model", "miniature car", "teddy", "plush", "puzzle",
      "backpack", "duffel", "wallet", "wristwatch", "sunglasses case",
      "merchandise", "lifestyle collection",
      ]),

    # ── filters (before engine/fuel/fluids so 'oil filter' ≠ engine/oil) ──────
    ("filters",
     [# 'שמן תיבת הילוכים' (gear OIL) is a legitimate fluids keyword and is
      # longer than 'מסנן שמן', so a gearbox oil FILTER was scoring as a fluid.
      # These are longer still, which puts the filter back in filters.
      "מסנן שמן תיבת הילוכים", "מסנן שמן תיבת העברה", "מחזיק מסנן שמן",
      "מסנן שמן", "מסנן אוויר", "מסנן דלק", "מסנן תא", "מסנן מזגן",
      "מסנן חלקיקים", "מסנן מנוע", "מסנן מקצועי", "מסנן",
      "פילטר אוויר", "פילטר שמן", "פילטר דלק", "פילטר מזגן", "פילטר",
      "فلتر زيت المحرك", "فلتر هواء المحرك", "فلتر تكييف الهواء",
      "فلتر الزيت", "فلتر زيت", "فلتر هواء", "فلتر الهواء", "فلتر وقود",
      "فلتر الوقود", "فلتر مقصورة", "فلتر المكيف", "فلتر"],
     ["oil filter", "air filter", "fuel filter", "pollen filter", "cabin filter",
      "cabin air filter", "particulate filter", "dpf filter", "sports air filter",
      "panel filter", "performance filter", "air box filter", "filter element",
      "filter cartridge", "filter housing", "filter insert"]),

    # ── brakes ───────────────────────────────────────────────────────────────
    ("brakes",
     ["רפידות בלמים", "דיסק בלמים", "רפידת בלם", "דיסק בלם", "צינור בלם",
      "קליפר בלם", "מיכל נוזל בלם", "ממסר בלם", "צנרת בלם", "מוביל בלם",
      "מגבר בלם", "כוס בלם", "תוף בלם", "כבל בלם", "בלם יד",
      "בלמי", "בלם", "רפידות", "רפידה", "קליפר",
      "بطانة فرامل", "بطانات الفرامل", "قرص فرامل", "أقراص الفرامل",
      "اسطوانة الفرامل", "أسطوانة الفرامل", "سائل الفرامل", "خرطوم فرامل",
      "فرامل", "فرملة", "كالبر", "فرمنة", "طنبورة"],
     ["brake pad", "brake disc", "brake rotor", "brake caliper", "brake hose",
      "brake line", "brake cylinder", "brake fluid reservoir",
      "wheel speed sensor", "park brake", "parking brake", "handbrake",
      "brake shoe", "drum brake", "disc brake", "brake drum", "brake booster",
      "brake light switch", "abs pump", "brake kit", "brake set",
      "caliper piston", "brake anchor", "brake bracket",
      "handbrake cable", "parking cable", "brake backing plate",
      "clamp brake", "clamp parking",
      "brake wear sensor", "brake pad wear", "caliper bracket",
      "caliper carrier", "brake master cyl"]),

    # ── clutch & drivetrain (before gearbox/engine) ──────────────────────────
    ("clutch-drivetrain",
     ["דיסק מצמד", "לחצן מצמד", "גלגל תנופה", "מרכז כוח", "דיפרנציאל",
      "גל הנעה", "גל הינע", "ציר קדמי", "ציר אחורי", "מצמד", "קלאץ",
      "פלנץ", "ציריה",
      "قرص الكلتش", "صحن الكلتش", "عمود الإدارة", "عمود الكردان",
      "ديفرنشيال", "كلتش", "دبرياج", "علبة التوزيع"],
     ["clutch kit", "clutch disc", "clutch plate", "pressure plate", "flywheel",
      "dual mass flywheel", "release bearing", "throw-out bearing",
      "boot clamp", "driveshaft clamp", "cv boot clamp", "gaiter clamp",
      "clutch fork", "clutch slave cylinder", "clutch master cylinder",
      "differential", "diff ", "transfer case", "propshaft", "cv joint boot",
      "driveshaft boot", "tripod", "halfshaft", "half shaft",
      "propeller shaft", "rear axle shaft", "inter-axle", "clutch cable",
      "clutch pedal", "clutch bearing", "centre bearing", "center bearing"]),

    # ── gearbox ──────────────────────────────────────────────────────────────
    ("gearbox",
     [# HARDWARE that merely CONTAINS the phrase 'שמן תיבת הילוכים' (gear oil).
      # That fluids keyword is 16 chars and outranked every hardware head, so a
      # gearbox oil pump / pan / dipstick / seal all scored as a FLUID. Measured
      # on the live catalog: of 700 rows carrying the phrase only 84 (12%) are
      # actually oil — the rule was wrong 5 times out of 6. These are longer, so
      # longest-match-wins puts the hardware back where it belongs.
      "אטם למשאבת שמן תיבת הילוכים", "כיסוי משאבת שמן תיבת הילוכים",
      "צינור למדיד שמן תיבת הילוכים", "אטם לאגן שמן תיבת הילוכים",
      "מחזיר שמן תיבת הילוכים", "משאבת שמן תיבת הילוכים",
      "אטם אגן שמן תיבת הילוכים", "מדיד שמן תיבת הילוכים",
      "אגן שמן תיבת הילוכים", "צינור שמן תיבת הילוכים",
      "לתיבת הילוכים", 'לת"ה', 'לת״ה', 'ת"ה', 'ת״ה', 'תה"ל', 'תה״ל',
      'גג"ש', 'גג״ש', 'גלגל"ש', 'גלגל״ש', 'גלג"ש', 'לגלגל"ש', "גלגל שיניים",
      'בתה"ל', 'בתה״ל', 'תה"ע', 'תה״ע', 'לתה"ע', "תיבת העברה",
      'לתה"ל', 'לתה״ל', 'תיה"ל', 'תיה״ל', 'מדתה"ל',
      "תיבת הילוכים", "קופסת גיר", "גיר אוטומטי", "גיר ידני", "שמן גיר",
      "ידית הילוכים", "תמסורת", "גיר",
      "علبة التروس", "علبة تروس", "ناقل الحركة", "فتيس", "دريكسيون آلي",
      "زيت ناقل الحركة", "عصا الفتيس"],
     ["gearbox", "transmission", "gear box", "automatic transmission",
      "manual gearbox", "gear shift", "gear lever", "selector fork",
      "synchronizer", "transmission mount", "gearbox mount",
      "transmission oil", "gear oil", "atf fluid", "torque converter",
      "transaxle", "gear knob", "gear selector", "planet gear",
      "gear assembly", "pinion gear", "sun gear", "shift cable",
      "shift linkage", "gearbox housing", "valve body"]),

    # ── suspension & steering ────────────────────────────────────────────────
    ("suspension-steering",
     ["בולם זעזועים", "בולם הגה", "מוט קישור", "מוט מייצב", "מוט קשר",
      "זרוע קדמית", "זרוע אחורית", "כדורית היגוי", "משולש תחתון",
      "משולש עליון", "מנגנון הגה", "גלגל הגה", "עמוד הגה", "מוט הגה",
      "גשר אחורי", "בית כדור", "כדור מפרק", "ציר גלגל", "מוט גלגול",
      "בסיס מדחס", "ספוג כרית", "אמורטיזר", "בושינג", "היגוי",
      "בולם", "קפיץ", "זרוע", "הגה", "מייצב", "בוש", "מרפק", "צ'ופה",
      "ممتص الصدمات", "ممتص صدمات", "مقص التعليق", "ذراع التعليق",
      "مفصل كروي", "عمود التوجيه", "بوشات التعليق", "مقص علوي",
      "مقص سفلي", "نابض", "مقود", "دريكسيون", "شاحم", "مثبت"],
     ["shock absorber", "shock-absorber", "strut", "spring coil", "coil spring",
      "leaf spring", "control arm", "tie rod", "track rod", "ball joint",
      "sway bar", "stabilizer bar", "stabilizer link", "anti-roll bar", "anti roll",
      "antiroll", "antiroll bar", "roll bar",
      "steering rack", "power steering", "steering pump", "steering column",
      "steering shaft", "steering boot", "rack boot", "cv boot",
      "suspension arm", "wishbone", "trailing arm", "subframe", "bushing",
      "bush ", "rubber mount", "strut mount", "strut bearing", "top mount",
      "front strut", "steering damper", "steering angle", "stub axle",
      "inner tie", "torsion bar", "bump stop", "axle bush",
      "strut assembly", "shock boot", "spring seat", "jounce bumper",
      "strut cartridge", "knuckle", "upright", "spindle", "air bellows",
      "air spring", "steering knuckle", "drag link", "idler arm",
      "pitman arm", "steering gear"]),

    # ── wheels & bearings ────────────────────────────────────────────────────
    ("wheels-bearings",
     [# A hubcap is 'כובע גלגל' — `כובע` alone is the merchandise word for a hat,
      # so the two-word form must be present to out-rank it.
      "כובע גלגל", "כובעי גלגלים", "כובע לגלגל", "כיסוי גלגל",
      "מיסב גלגל", "מסבב גלגל", "בית גלגל", "ציר הנעה", "חישוק",
      "ספייסר", "מיסב", "נבה", "גלגל", "צמיג",
      "بلي عجلة", "رولمان بلي", "محور العجلة", "جنط", "إطار",
      "بيرينج", "رولمان", "محور"],
     ["wheel bearing", "hub bearing", "hub assembly", "wheel hub", "cv joint",
      "drive shaft", "axle shaft", "driveshaft", "prop shaft",
      "wheel bolt", "wheel nut", "lug nut", "wheel stud",
      "hub cap", "center cap", "abs ring", "tone ring", "wheel arch",
      " tyre", " tire", "r15", "r16", "r17", "r18", "r19", "r20", "r21",
      "alloy wheel", "steel wheel", "wheel rim", "rim ",
      "rear axle", "front axle", "spindle assembly", "wheel spacer",
      "wheel weight", "valve stem", "tpms sensor"]),

    # ── exhaust ──────────────────────────────────────────────────────────────
    ("exhaust",
     ["ממיר קטליטי", "צינור פליטה", "מכלול פליטה", "ספייסר פליטה",
      "מגן חום", "פליטה", "אגזוז", "מפלט", "מאיין", "מנבר", "קטליטי",
      "المحول الحفاز", "كاتليزاتور", "ماسورة العادم", "محبس العادم",
      "بايب العادم", "شكمان", "عادم", "درع حراري"],
     ["exhaust", "muffler", "silencer", "catalytic converter", "catalyst",
      "dpf", "lambda sensor", "oxygen sensor",
      "o2 sensor", "exhaust manifold", "exhaust pipe", "exhaust gasket",
      "exhaust bracket", "exhaust hanger", "tailpipe", "downpipe",
      "flex pipe", "resonator", "manifold gasket", "heat shield", "heatshield",
      "heat wrap", "exhaust clamp", "exhaust support", "egr valve",
      "egr pipe", "scr system", "adblue", "def tank", "soot sensor"]),

    # ── cooling ──────────────────────────────────────────────────────────────
    ("cooling",
     [# Gearbox oil COOLER + its plumbing — hardware, not the oil itself.
      # Same 16-char 'שמן תיבת הילוכים' collision as the gearbox block above.
      "צינור מים למצנן שמן תיבת הילוכים", "צינור למצנן שמן תיבת הילוכים",
      "תושבת למצנן שמן תיבת הילוכים", "אטם טבעת למצנן שמן תיבת הילוכים",
      "צינור קרור שמן תיבת הילוכים", "צינור קירור שמן תיבת הילוכים",
      "מצנן שמן תיבת הילוכים",
      "מכסה רדיאטור", "משאבת מים", "מאוורר קירור", "נוזל קירור",
      "צנרת קירור", "צינור קירור", "כוס התפשטות", "צינור מים",
      "רדיאטור", "תרמוסטט", "כוס קירור", "מצנן", "קירור",
      "مضخة الماء", "مضخة المياه", "سائل التبريد", "خزان التمدد",
      "خرطوم الرديتر", "مروحة التبريد", "رديتر", "ترموستات", "مبرد"],
     ["radiator", "water pump", "coolant", "thermostat", "cooling fan", "fan clutch",
      "intercooler", "expansion tank", "overflow tank", "coolant reservoir",
      "radiator cap", "radiator hose", "coolant hose", "water outlet",
      "water neck", "coolant sensor", "temperature sensor", "fan blade",
      "oil cooler", "coolant flange", "heater pipe", "heater hose",
      "hose clamp", "radiator clamp", "coolant clamp", "pipe clamp",
      "clamp hose", "clamp piping", "clamp heater", "clamp radiator",
      "clamp water", "clamp coolant",
      "coolant pipe", "water jacket", "cooling tube", "fan shroud",
      "radiator support", "coolant flush",
      # 'line' alone is most often a fluid line on these catalogs
      "water line", "coolant line", "cooler line"]),

    # ── a/c & heating ────────────────────────────────────────────────────────
    ("air-conditioning-heating",
     ["קומפרסור מזגן", "רדיאטור מזגן", "אידיי מזגן", "פילטר מזגן",
      "בורג לקומפרסור", "שסתום מיזוג", "מנוע מפוח", "קונדנסור",
      "קומפרסור", "מדחס", "מזגן", "מאייד", "אידוי", "מחמם", "חימום", "מפוח",
      "ضاغط التكييف", "كمبريسور مكيف", "مروحة المقصورة", "مبخر المكيف",
      "مكثف المكيف", "غاز التكييف", "مكيف", "مبخر", "دفاية"],
     ["air conditioning", "air conditioner", "a/c ", "ac compressor",
      "compressor clutch", "evaporator", "condenser", "heater core",
      "blower motor", "hvac", "climate control", "ac hose", "refrigerant",
      "blend door", "expansion valve", "receiver drier", "dryer",
      "refrigerant line clamp", "refrigerant clamp", "ac line clamp",
      "heater control", "hvac actuator", "air duct", "vent",
      "cabin blower", "heater blower", "ac pipe", "heater matrix",
      "ac condenser", "compressor oil"]),

    # ── belts & chains ───────────────────────────────────────────────────────
    ("belts-chains",
     ["שרשרת תזמון", "שרשרת תיזמון", "רצועת תזמון", "מותח שרשרת",
      "מדריך שרשרת", "מתח רצועה", "גלגלת סרק", "מותחן",
      "רצועה", "שרשרת", "גלגלת", "חגורה",
      "سير التوقيت", "جنزير التوقيت", "شداد السير", "بكرة السير",
      "سير المكينة", "سيور", "سير", "جنزير"],
     ["timing belt", "cam belt", "serpentine belt", "poly v belt", "v-belt",
      "ribbed belt", "multi-rib belt", "drive belt", "belt kit",
      "timing chain", "timing chain kit", "chain tensioner", "chain guide",
      "tensioner pulley", "idler pulley", "belt tensioner", "accessory belt",
      "v belt", "fan belt", "belt adjuster", "timing kit", "timing cover gasket"]),

    # ── lighting ─────────────────────────────────────────────────────────────
    ("lighting",
     ["פנס ערפל", "פנס ראשי", "פנס קדמי", "פנס אחורי", "פנס לוחית",
      "פנס דלת", "נורת ערפל", "תאורת", "רפלקטור",
      "פנסים", "פנס", "תאורה", "נורה", "לד",
      "مصباح أمامي", "مصباح خلفي", "مصباح ضباب", "لمبة", "مصابيح",
      "مصباح", "فانوس", "إضاءة", "عاكس", "نور", "ضوء"],
     ["headlight", "headlamp", "tail light", "tail lamp", "taillight",
      "fog light", "fog lamp", "turn signal", "indicator", "daytime running",
      "drl", "brake light", "stop light", "reverse light", "backup light",
      "interior light", "dome light", " bulb ", " led ", "reflector",
      "side marker", "corner light", "flasher relay", "light assembly",
      "number plate light", "license plate light", "sealed beam",
      "xenon", "hid lamp", "halogen lamp", "parking light",
      "headlight washer", "lamp housing", "lens light"]),

    # ── wipers & washers ─────────────────────────────────────────────────────
    ("wipers-washers",
     ["מיכל ממחקים", "זרוע מגב", "מנוע מגב", "מגב אחורי", "מגב קדמי",
      "וישר אחורי", "שפריצר", "ממחק", "מגב", "וישר",
      "مساحات الزجاج", "ماسحة الزجاج", "ذراع المساحة", "موتور المساحات",
      "مساحات", "مساحة", "مرشة", "رشاش الزجاج"],
     ["wiper blade", "wiper arm", "wiper motor", "wiper linkage",
      "windshield washer", "washer pump", "washer nozzle", "washer reservoir",
      "washer tank", "rear wiper", "wiper refill", "windscreen washer",
      "wiper pivot", "wiper drive", "washer hose", "washer jet"]),

    # ── fuel & air ───────────────────────────────────────────────────────────
    ("fuel-air",
     ["משאבת דלק", "מזרק דלק", "מיכל דלק", "צנרת דלק", "צינור דלק",
      "שסתום דלק", "שפופרת דלק", "אוגן דלק", "גוף מצערת", "מצנן ביניים",
      "מזרק", "מצערת", "טרבו", "טורבו",
      # סל"ד = סיבובים לדקה (RPM). 'מייצב סל"ד' is an IDLE-SPEED stabilizer
      # (idle air control), NOT a suspension anti-roll bar — but the bare rule
      # 'מייצב' filed all of them under suspension-steering. These are longer,
      # so longest-match-wins routes them here. Both gershayim forms (" and ״).
      'מייצב סל"ד', "מייצב סל״ד", 'מייצב סל"ד', 'למייצב סל"ד', "למייצב סל״ד",
      "مضخة الوقود", "خزان الوقود", "حاقن الوقود", "رشاش الوقود",
      "فتيلة الوقود", "بخاخ الوقود", "سكشن الوقود", "بوابة الهواء",
      "حاقن", "تيربو", "شاحن توربيني"],
     ["fuel pump", "fuel injector", "fuel rail", "fuel tank",
      "fuel line", "fuel hose", "fuel cap", "fuel sender", "fuel pressure",
      # Catalog names also appear in REVERSED word order ('Hose Fuel Emission'),
      # where the bare 'hose' rule steals them into cooling. Measured on a live
      # 40k sample: 452 rows. Same class as 'absorber assembly shock'.
      "hose fuel", "hose fuel emission", "line fuel", "pipe fuel", "tube fuel",
      "air intake", "throttle body", "mass air flow", "maf sensor",
      "intake manifold", "map sensor", "idle control valve", "egr cooler",
      "pcv valve", "charcoal canister", "evap canister", "turbocharger",
      "turbo ", "supercharger", "intercooler pipe", "secondary air pump",
      "carburetor", "carburettor", "intake plenum", "plenum chamber",
      "vacuum hose", "vacuum pipe", "air guide", "air box",
      "fuel clamp", "fuel hose clamp", "injector clamp", "intake clamp",
      "clamp fuel", "clamp evaporation", "clamp breather", "clamp vacuum",
      "clamp air", "clamp purge",
      "fuel filler", "fuel neck", "vapor canister", "injection pump",
      "common rail", "high pressure pump", "air resonator"]),

    # ── engine ───────────────────────────────────────────────────────────────
    ("engine",
     ["אטם ראש מנוע", "מבודד תושבת מנוע", "תושבת מנוע", "מחזיק מנוע",
      "מדיד שמן", "מוט מדידת שמן", "מיכל שמן מנוע", 'מחז"ש', 'מחז״ש',
      # An oil SEAL, not a fluid — same part as 'אטם שמן', which is already
      # engine. The rule used to be keyed ONLY on the abbreviation מחז"ש; once
      # the names were cured to the full form the rule stopped matching and
      # 402 seals fell to `fluids` on the word שמן. When curing data, every
      # rule keyed on the OLD form needs the NEW form too.
      "מחזיר שמן", "מחזירי שמן", "מחזיר שמן גל",
      "מכסה שסתומים", "טבעת לפיסטון", "אטם גל ארכובה", "אטם גל הנוע",
      "גל ארכובה", "גל הנוע", "מיסב ראשי", "מיסב מוט", "ראש גליל",
      "בלוק מנוע", "כיסוי מנוע", "פלטה מנוע", "מצנן שמן",
      "ברגי ראש", "אטם מנוע", "אטם ראש", "טבעת אטם", "תיבת מפה",
      "טיימינג", "בוכנה", "שסתום", "מנוע", "מצת", "אטם", "ספון",
      "غاسكيت رأس المحرك", "وجه المكينة", "كتلة المحرك", "عمود المرفق",
      "عمود الكامات", "مضخة الزيت", "حلقات المكبس", "كرسي المكينة",
      "غطاء المحرك الداخلي", "بوجيه", "بوجي", "صمام", "مكبس",
      "محرك", "مكينة", "تايمينج", "كامة", "جوان"],
     ["piston", "head gasket", "crankshaft", "camshaft", "spark plug",
      "glow plug", "cylinder head", "engine mount", "oil pump", "oil cap",
      "oil pan", "oil sump", "timing cover", "valve cover", "rocker arm",
      "connecting rod", "engine block", "engine seal", "engine gasket",
      "engine bracket", "motor mount", "crankshaft seal", "camshaft seal",
      "clamp oil", "clamp engine",
      "oil pressure", "valve train", "lifter", "tappet", "pushrod",
      "flywheel bolt", "harmonic balancer", "crank pulley", "rocker cover",
      "crankcase", "distributor", "ignition coil", "sump", "oil dipstick",
      "thrust bearing", "main bearing", "o ring", "o-ring",
      "cylinder block", "engine cover", "oil separator",
      "valve stem seal", "valve spring", "cam follower",
      "sump gasket", "rocker gasket", "engine gasket set",
      "cylinder liner", "piston ring", "big end bearing", "oil strainer",
      "vvt actuator", "camshaft adjuster", "balance shaft"]),

    # ── audio & electronics (owner split 2026-08-02) ─────────────────────────
    # Declared BEFORE electrical so that when both could match, the more
    # specific entertainment term is the one considered. These keywords used to
    # live in the electrical block; sharing them between two families is what
    # pulled 1,565 freshly-split parts back into electrical.
    ("audio-electronics",
     ["רדיו", "רמקול", "רמקולים", "מגבר", "מסך", "מסך מגע", "מולטימדיה",
      "מערכת שמע", "ניווט", "אנטנה", "אנטנת רדיו", "מצלמת רוורס",
      "راديو", "سماعة", "مكبر صوت", "شاشة", "هوائي", "ملاحة"],
     ["stereo", "speaker", "subwoofer", "amplifier", " amp ", "head unit",
      "infotainment", "multimedia", "navigation", "sat nav", "satnav",
      "antenna", "aerial", "radio", "dashcam", "dash cam",
      "touchscreen", "touch screen", "lcd", "monitor",
      "head-up display", "heads up display", "display screen",
      "bluetooth", "usb hub", "aux input", "cd changer", "dvd player"]),

    # ── electrical & sensors ─────────────────────────────────────────────────
    ("electrical",
     ["חיישן טמפרטורה", "חיישן מהירות", "חיישן חניה", "חיישן לחץ",
      # 'טמפרטורה' alone had no rule, so temperature actuators/controllers
      # scored nothing and would have dropped to כללי.
      "מפעיל טמפרטורה", "בקרת טמפרטורה", "מודול טמפרטורה", "מתג טמפרטורה",
      "בורר טמפרטורה", "מד טמפרטורה",
      # Bare fallback for forms with no preceding word ('מודול RS03-טמפרטורה').
      # 9 chars, so every compound above still outranks it.
      "טמפרטורה",
      # A window SWITCH stays electrical; longer than the 'חלונות חשמל'
      # body-exterior rule, so longest-match-wins keeps it here.
      "מתג ראשי חלונות חשמל", "מתג ראשי חלונות חשמליים", "מתג ראשי חלונות",
      "יחידת בקרה", "מנוע חשמלי", "מפסק אורות", "לוח שקעים", "כבל חשמל",
      "לוח מחשב", "אלטרנטור", "מצבר", "מתנע", "חיישן", "ממסר", "נתיך",
      "בקרה", "מחשב", "מפסק", "בוקסה", "מחבר", "מוליך", "מערכת", "קרן",
      "מתג", "ספק",
      "وحدة التحكم", "كمبيوتر السيارة", "حساس الحرارة", "حساس السرعة",
      "حساس الركن", "دينمو", "مارش", "بطارية", "ريلي", "فيوز",
      "حساس", "مستشعر", "ماتور", "سلك", "ضفيرة أسلاك", "كونتاكت"],
     ["sensor", "relay", " fuse ", "fuse box", "fusebox", "control unit", "ecu",
      "module", "control module", "abs module", "alternator", "starter motor",
      "battery", "wire harness", "wiring harness", "connector", "socket",
      "switch", "ignition switch", "window switch", "mirror switch",
      # A window SWITCH is electrical; the regulator/motor/lifter is door
      # hardware (body-exterior). These are longer than the plain
      # 'חלונות חשמל' body rule, so longest-match-wins keeps switches here.
      "מתג חלונות חשמל", "מתגי חלונות חשמל", "פאנל מתגים חלונות חשמל",
      "מתג חלון חשמל", "פאנל מתגים",
      "crankshaft sensor", "camshaft sensor", "knock sensor",
      "throttle position sensor", "tps", "coolant temp sensor", "speed sensor",
      "parking sensor", "reverse sensor", "horn", "relay box", "fuse link",
      "central locking", "solenoid", "actuator",
      # 'housing' is a hand-written ENGINE keyword (thermostat housing etc.), and
      # a hand-written rule always beats a learned one regardless of length — so
      # the learned `relay housing` could never win. These need to be
      # hand-written to take effect.
      "relay housing", "fuse housing", "fusebox housing", "connector housing",
      "abs sensor", "lambda probe",
      "immobilizer", "spark plug wire", "harness",
      # Audio/infotainment terms MOVED to the audio-electronics block below
      # (owner split 2026-08-02). Leaving them here is what silently dragged
      # 1,565 already-split parts back into electrical: two families claiming
      # the same keyword means whichever rule wins the match decides, and the
      # split loses. A keyword belongs to exactly ONE family.
      "instrument cluster", "digital cluster",
      "door actuator", "lock actuator", "window actuator",
      "sunroof motor", "seat motor", "mirror motor",
      "alarm", "immobiliser", "transponder", "key fob",
      # Supplier catalogs TRUNCATE names ('Key Master Immobiliz'), so the full
      # spellings never match and the row falls to the physical-key rule in
      # body-exterior. Match the stem instead — same class as 'Cover-cushio'.
      "immobiliz", "immobilis", "key master immobiliz", "master immobiliz",
      "abs control", "traction control", "stability control",
      "reversing camera", "rear camera", "gps module", "bluetooth module",
      "voltage regulator", "starter solenoid", "earth cable", "ground strap",
      # Compounds: the word-boundary rule (correctly) stops 'meter'/'speaker'
      # matching inside these, so they need to be keywords themselves. Measured
      # 2026-07-28 — without them "Speedometer" and "Loudspeaker" fell to 'כללי'.
      "speedometer", "odometer", "tachometer", "voltmeter", "ammeter",
      "loudspeaker", "subwoofer", "tweeter",
      # CLAMPS. 21,998 catalog parts carry "clamp" and 1,454 were mis-filed as
      # `lighting` because 'lamp' matched inside 'c-lamp'. The word-boundary rule
      # stops that, but a clamp is a real part and must land somewhere: a bare
      # "Clamp" is a cable/wiring clamp (owner-confirmed 2026-07-28). Clamps that
      # carry context are handled by the longer compounds in the other
      # categories, which win on keyword length.
      "clamp", "cable clamp", "wire clamp", "wiring clamp", "battery clamp",
      "clamp wiring", "clamp wire", "clamp cable", "clamp harness",
      "post clamp", "battery post clamp", "earth clamp", "terminal clamp",
      # High-frequency in the real stuck population (2026-07-27)
      "electr", "wire lead", "cable lead", "junction", "busbar", "bus bar",
      "grommet wire", "conduit", "loom", "pigtail"]),

    # ── interior comfort ─────────────────────────────────────────────────────
    ("interior-comfort",
     ["פנל ידית הילוכים", "מסוף קונסולה", "שטיח תא מטען", "שטיח ריצפה",
      "כיסוי דוושה", "וילון גלילה", "ריפוד תקרה", "ריפוד כסא", "ריפוד דלת",
      "כפתור חלון", "מגן ממחק", "מסגרת פנים", "לוח מחוונים", "כסא נהג",
      "דשבורט", "דשבורד", "לוח שעונים", "קונסולה",
      "כסא נוסע", "מגש אחורי", "ידית דלת", "ציר דלת", "אחסון",
      "מושב", "ריפוד", "רפוד", "שטיח", "כסא", "מגש", "פנל", "כפתור",
      "מחזיק", "כוסית", "אחיזה", "מנוף", "ידית",
      "طقم فرش المقاعد", "لوحة العدادات", "عجلة القيادة", "بانيل الباب",
      "سقف الكابين", "تريم داخلي", "مسند الذراع", "صندوق القفازات",
      "مقعد", "سجادة", "فرش", "تريم"],
     ["seat", "seat cover", "seat cushion", "seat back pad", "seat recliner",
      "recliner adjust", "recliner mechanism", "dashboard", "dash panel",
      "door panel", "door card", "armrest", "headrest",
      "cup holder", "sun visor", "visor assy", "coat hook", "cargo cover",
      "floor mat", "carpet", "center console", "gear knob", "shift knob",
      "handbrake grip", "parking brake grip",
      "steering wheel cover", "pillar trim", "pillar molding", "door trim",
      "adjust knob", "dial knob", "instrument panel",
      "glove box", "grab handle", "ceiling", "headliner", "overhead",
      "roof lining", "interior trim", "trim cover", "trim panel",
      "gaiter", "handbrake gaiter", "gear gaiter", "gear boot",
      "rear parcel shelf", "package tray", "rear shelf",
      "pedal pad", "footrest", "seat rail", "seat frame", "seat heater",
      # 'bar' must not match inside "Lumbar", so lumbar needs its own keyword.
      "lumbar", "reclining", "recliner", "headrest guide"]),

    # ── body & exterior ──────────────────────────────────────────────────────
    ("body-exterior",
     ["מסגרת חלחון", "צוואר לוחית", "מחבר דלת", "מנעול דלת", "כיסוי פגוש",
      "תושבת פגוש", "עמוד אמצעי", "זכוכית חלון", "קורה תחתונה",
      "קורה עליונה", "ספוילר אחורי", "ספוילר קדמי", "פגוש קדמי",
      "פגוש אחורי", "כנף קדמית", "כנף אחורית", "מכסה מנוע", "גריל קדמי",
      "אמות שלדה", "מגן רצפה", "מגן סורג", "פינה לפגוש", "פס קישוט",
      "כבל פתיחה", "מגן בטן", "מגן רוח", "מגן בוץ", "סמל יצרן",
      "חלון גג", "גג שמש",
      "רשת נוי", "תא מטען", "עמוד A", "עמוד B", "עמוד C",
      "ביטנה", "בטנה", "עצם רכב", "מדבקה", "לוחית", "ספוילר",
      "פגוש", "גריל", "שמשה", "חלון", "דלת", "כנף", "בונט", "גגון",
      "ויזר", "מכסה", "תפס", "ציר", "ראי", "פח", "סף",
      "مصد أمامي", "مصد خلفي", "غطاء المحرك", "زجاج أمامي", "زجاج خلفي",
      "مقبض الباب", "قفل الباب", "مرآة جانبية", "رفراف", "شبك أمامي",
      "هيكل خارجي", "صدام", "مصد", "كبوت", "شنطة", "جناح", "باب",
      "مرآة", "زجاج", "شبك", "درابزين"],
     ["bumper", "fender", "front fender", "rear fender", "wing ", "hood",
      "bonnet", "trunk lid", "boot lid", "tailgate", "liftgate", "hatchback",
      "grille", "front grille", "radiator grille", "spoiler", "lip kit",
      "side skirt", "rocker panel", "windshield", "windscreen", "rear window",
      "side glass", "quarter glass", "fixed glass", "movable glass",
      "door glass", "window glass", "mirror glass", "side mirror",
      "door mirror", "rearview mirror", "mirror cover", "mirror housing",
      "pillar", "a pillar", "b pillar", "c pillar", "body panel",
      "body kit", "mud flap", "splash guard", "splash shield",
      "fender liner", "fender splash", "wheel arch liner",
      "roof rail", "door sill", "step pad", "floor side rail",
      "body trim", "chrome trim", "molding", "cowl", "apron",
      "sunroof", "door lock", "tailgate lock", "door seal", "window seal",
      "central lock", "towbar", "tow bar", "side trim", "quarter panel",
      "hood hinge", "door hinge", "door check", "door stop",
      "cable hood", "hood release", "door handle", "clamp door",
      "hinge", "clip", "bracket body", "reinf", "reinforcement",
      "cross member", "crossmember", "x-member", "xmember",
      "rear cross", "cowl panel", "apron panel",
      "door striker", "latch", "door latch", "lock cylinder",
      "door frame", "window frame", "trim plate", "sight shield",
      "sound absorber", "sound deadening", "heat mat",
      "window regulator", "window motor", "window lifter",
      # Hebrew power-window terms had NO rule at all — only the English forms
      # existed, so every Hebrew-named window-lift part fell through to כללי.
      # 'חלונות חשמל' is also what the abbreviation ח"ח expands to.
      "חלונות חשמל", "חלון חשמל", "מרים חלון", "מגביה חלון",
      "מנגנון חלון", "מנוע חלון", "מרים שמשה",
      " mirror ", "outer mirror", "inner mirror",
      "side window", "rear glass", "front glass",
      "seal door", "seal body", "grommet", "rubber seal",
      "badge", "emblem", "decal", "sticker",
      "fog trim", "bumper bracket", "impact bar",
      "running board", "skid plate",
      "cargo liner", "trunk liner", "boot liner", "wheel arch"]),

    # ── fluids ───────────────────────────────────────────────────────────────
    ("fluids",
     ["שמן גיר אוטומטי", "שמן תיבת הילוכים", "שמן מנוע", "שמן גיר", "נוזל בלם", "שמן הידראולי", "אנטיפריז", "שמן",
      "زيت المحرك", "زيت الفرامل", "مضاد التجمد", "شحم", "زيت"],
     ["engine oil", "hydraulic oil", "brake fluid", "coolant fluid",
      "antifreeze", "atf ", "grease", "lubricant", "additive",
      "power steering fluid", "differential oil", "washer fluid"]),

    # ── hybrid & EV ──────────────────────────────────────────────────────────
    ("hybrid-ev",
     ["סוללה היברידית", "מטען חשמלי", "ממיר מתח", "מנוע חשמלי ראשי",
      "بطارية هجينة", "شاحن كهربائي", "محول الجهد"],
     ["hybrid battery", "traction battery", "hv battery", "ev charger",
      "charging port", "charging cable", "inverter", "dc-dc converter",
      "traction motor", "battery cooling", "hybrid inverter",
      "onboard charger", "battery module", "high voltage cable"]),

    # ── service kits (a REAL category — never a fallback) ────────────────────
    ("service-general",
     ["ערכת שירות", "ערכת תחזוקה", "ערכת אטמים",
      "طقم صيانة", "مجموعة الخدمة", "طقم جوانات"],
     ["service kit", "maintenance kit", "drain plug", "sump plug",
      "gasket set", "seal kit", "repair kit", "overhaul kit"]),

    # ── accessories (a REAL category — never a fallback) ─────────────────────
    # Tools/equipment fold in here: 'tools-equipment' is NOT a part_type_taxonomy
    # family, so it is not canonical and must never be returned (see VARIANT_MAP).
    ("accessories",
     ["מטען לרכב", "מחזיק טלפון", "כיסוי רכב", "מפתח ברגים", "ג'ק הרמה",
      "شاحن السيارة", "حامل الهاتف", "غطاء السيارة", "مفتاح ربط", "جك رفع"],
     ["phone holder", "car charger", "car cover", "roof box",
      "bike carrier", "dash cam", "air freshener", "steering lock",
      "socket wrench", "torque wrench", "jack stand", "tyre lever",
      "diagnostic tool", "obd scanner", "puller tool",
      # Merchandise the IL/EU suppliers list alongside parts: workwear and
      # printed documentation. Not vehicle parts, but not 'unknown' either.
      "trousers", "safety vest", "hi-vis", "hi vis", "overall", "workwear",
      "work glove", "safety boot", "helmet", "coverall",
      "owner s manual", "owners manual", "owner manual", "drivers manual",
      "driver s manual", "service manual", "handbook", "user guide"]),

    # ═════════════════════════════════════════════════════════════════════════
    # GENERIC HEAD-NOUNS — lowest-precedence tier.
    #
    # Everything above is a specific multi-word phrase. This tier holds the bare
    # nouns ("brake", "wheel", "belt", "gasket", "door") that a catalog name is
    # often reduced to: "Brake Kit Front", "Valve Assy Control", "absorber
    # assembly shock". Token analysis of 24,845 real unmatched rows (2026-07-27)
    # showed these bare words were the single largest gap — 'engine' appeared
    # 765×, 'door' 739×, 'belt' 505×, 'brake' 338× with NO rule to catch them.
    #
    # This tier is only safe because matching is LONGEST-KEYWORD-WINS: a specific
    # phrase always outranks the generic. Under the old first-category-wins order
    # these words would have hijacked everything, which is why they were absent.
    # ═════════════════════════════════════════════════════════════════════════
    ("brakes", ["בלמים"], ["brake", "caliper", "rotor", "abs"]),
    ("filters", ["מסננת"], ["filter"]),
    ("belts-chains", ["מותחן", "גלגלת"], ["belt", "chain", "pulley", "tensioner"]),
    ("wheels-bearings", ["חישוקים"], ["wheel", "bearing", "hub", "axle", "rim"]),
    ("suspension-steering",
     ["מתלה", "בולמים"],
     ["steering", "suspension", "shock", "damper", "strut", "wishbone",
      "stabiliser", "stabilizer", "linkage", "spring"]),
    ("clutch-drivetrain", ["מצמדים"], ["clutch", "driveshaft", "propshaft", "cv ", "shaft"]),
    ("gearbox", ["הילוכים"], ["gearbox", "transmission", "gearshift", "gear"]),
    ("exhaust", ["פליטות"], ["exhaust", "manifold", "muffler", "catalyst"]),
    ("cooling", ["מצננים", "צינור", "צנרת"], ["radiator", "coolant", "thermostat", "hose", "pipe"]),
    ("air-conditioning-heating", ["מזגנים"], ["heater", "blower", "compressor"]),
    ("lighting", ["פנסים", "מנורה"], ["lamp", "light", "bulb", "headlamp"]),
    ("wipers-washers", ["מגבים"], ["wiper", "washer nozzle", "washer pump"]),
    ("fuel-air",
     ["מזרקים", "מצערות"],
     ["fuel", "injector", "throttle", "turbo", "intake", "carb"]),
    ("engine",
     ["אטמים", "מנועים"],
     ["engine", "gasket", "seal", "piston", "valve", "cylinder", "camshaft",
      "crankshaft", "oil pan", "sump", "tappet", "o ring", "plug", "motor",
      "head", "housing", "flange", "mount"]),
    # NOTE: bare fasteners (bolt/screw/nut/stud/washer/shim/בורג) are deliberately
    # NOT listed. They have no category on their own, and a wrong category is
    # worse than 'כללי'. In context they still classify: "Bolt Cylinder Head"
    # matches the longer 'cylinder head' and lands in engine correctly.
    ("electrical",
     ["חיישנים", "מתגים", "כבל", "כבלים", "צמת חוטים"],
     ["sensor", "wire", "wiring", "lead", "cable", "electric", "electronic",
      "starter", "ignition", "instrument", "gauge", "meter", "unit",
      "controller", "circuit", "terminal", "contact", "coil"]),
    ("interior-comfort",
     ["ריפודים", "כריות", "מושבים", "קונסולה"],
     # "cushio"/"bolster" are real truncated forms in the OEM catalogs.
     ["seat", "cushion", "cushio", "squab", "bolster", "console", "trim panel",
      "carpet", "mat", "upholstery", "armrest", "sunvisor", "pad", "lining",
      # High-frequency in the real stuck population (2026-07-27)
      "ashtray", "blind", "sunshade", "sun shade", "curtain", "cup tray",
      "assist grip", "shift boot", "knee pad", "scuff plate", "kick panel"]),
    ("body-exterior",
     ["מגן", "מגנים", "פחים", "כיסויים", "ידיות", "צירים"],
     ["door", "bonnet", "bumper", "fender", "wing panel", "panel", "body",
      "floor", "roof", "pillar", "sill", "glass", "window", "mirror",
      "grille", "handle", "hinge", "lock", "latch", "striker", "key insert",
      "key master", "key blank", "blanking", "standard key", "finisher",
      "garnish", "moulding", "cover panel", "bracket", "support panel",
      "shield", "guard", "protector", "flap", "trim", "cowl", "apron",
      "reinforcement", "member", "rail", "bar", "step", "label", "emblem",
      "cover", "covering", "plate", "frame", "gaiter",
      # Measured 2026-07-27 in the real stuck population — categorizable words
      # that had no rule, so the LLM was being asked about them needlessly.
      # 'checkarm'/'check arm' is a DOOR CHECK (the LLM guessed brakes).
      "checkarm", "check arm", "door check arm", "garnish", "moulding",
      "weatherstrip", "weather strip", "insulator", "insulation", "sound proof",
      "bezel", "escutcheon", "finish panel", "protector", "deflector",
      "wheelhouse", "wheel house", "dash panel outer", "quarter", "rocker",
      "hood insulator", "under cover", "undercover", "splash", "lid"]),
    ("fluids", ["שמנים", "נוזלים"], ["oil", "fluid", "coolant", "lubricant"]),
    ("accessories", ["כלי עבודה", "כלים"], []),
]


# ── Flattened, longest-first keyword index ────────────────────────────────────
# Built once at import. Sorting by keyword length DESC (stable — RULES order
# breaks ties) makes the LONGEST match win, which removes the entire class of
# bug where a generic word in an early category shadowed a specific phrase in a
# later one ('שמן' vs 'מסנן שמן', 'בולם' vs 'בולם הגה', 'brake' vs 'brake fluid').
# Entries: (keyword, category, is_rtl, compiled_or_None)
#
# SHORT RTL KEYWORDS NEED A WORD BOUNDARY. Hebrew/Arabic are written without
# case and our match is a plain substring, so a 2-3 letter keyword fires inside
# unrelated longer words: 'לד' (LED) matched inside 'לדשבורט' (= "for the
# dashboard") and filed a dashboard bracket under `lighting`. Verified against
# live rows 2026-07-27.
#
# Hebrew glues one-letter prefixes (ה ו ב ל מ ש כ) onto nouns, so a naive \b
# would also drop the legitimate 'הפנס' / 'לפנס'. The pattern therefore allows
# an optional single-letter prefix BEFORE the keyword and forbids another RTL
# letter AFTER it: 'לפנס' matches, 'לדשבורט' does not.
_RTL_SHORT_MAX = 3
_RTL_PREFIXES = "הוbבלמשכ"  # Hebrew one-letter proclitics (b kept: latin lookalike)
_FLAT_RULES: List[Tuple[str, str, bool, Optional[re.Pattern]]] = []


def _rtl_boundary_pattern(kw: str) -> re.Pattern:
    return re.compile(
        r"(?:^|[^֐-׿؀-ۿ])[" + _RTL_PREFIXES + r"]?" + re.escape(kw) + r"(?![֐-׿؀-ۿ])"
    )


# ── LEARNED keywords ──────────────────────────────────────────────────────────
# Tokens taught to the matcher by the LLM assist (db_cleanup_agent task3b) and
# persisted in the `category_learned_keywords` table. The LLM is a HELPER that
# guides the keyword worker out of a dead end — not the thing that classifies
# the catalog. Every keyword it teaches makes the deterministic matcher able to
# handle that shape of name on its own, for free, forever after.
#
# Safety properties:
#   • a learned token is IGNORED if a hand-written rule already covers it
#     (`_covered_by_handwritten`) — the LLM can never override curated rules;
#   • learned entries are appended AFTER the hand-written ones, so on an equal-
#     length tie the hand-written rule wins (the length sort is stable);
#   • only canonical, non-catch-all categories are accepted.
LEARNED: Dict[str, str] = {}

_HANDWRITTEN_COUNT = 0


def _covered_by_handwritten(token: str) -> bool:
    """True if a hand-written rule already fires on this token."""
    t = token.lower()
    for kw, _cat, is_rtl, pat in _FLAT_RULES[:_HANDWRITTEN_COUNT]:
        if is_rtl:
            if pat.search(t) if pat is not None else (kw in t):
                return True
        elif pat.search(t) if pat is not None else (kw in t):
            return True
    return False


def _latin_boundary_pattern(kw: str) -> Optional[re.Pattern]:
    """
    Compile a Latin keyword with correct word-boundary semantics.

    TWO separate concerns, and an earlier attempt conflated them (2026-07-28):

    1. LEADING boundary — always required. Plain substring matching has no notion
       of word start, so a keyword matched mid-word:
           'rim '     matched inside "T[rim] R Rr Seat Back"  -> wheels-bearings
           'roll bar' matched inside "Anti[roll Bar] Blade"   -> safety-systems
       Same class as the Hebrew 'לד' inside 'לדשבורט' bug, never applied to Latin.

    2. TRAILING boundary — required ONLY when the keyword was written with a
       trailing space. That padding is deliberate and load-bearing: `'cv '`,
       `'rim '`, `' led '`, `'diff '`, `'bush '`, `'turbo '`, `'srs '`, `'a/c '`,
       `'atf '`, `'wing '`, `' fuse '`, `' bulb '`, `' mirror '`. Stripping it (as
       a first attempt did via `re.escape(kw.strip())`) makes `'cv '` match
       "CVT" — a gearbox filed as clutch-drivetrain — and `' led '` match "ledge".

    The END is left OPEN for unpadded keywords so ordinary inflections still
    match: 'brake' in 'brakes', 'filter' in 'filters', 'wheel' in 'wheels'.
    """
    core = kw.strip()
    if not core:
        return None
    pat = r"(?<![a-z0-9])" + re.escape(core)
    if kw.endswith(" "):          # padding was intentional -> close the end too
        pat += r"(?![a-z0-9])"
    return re.compile(pat)


def _build_flat_rules() -> None:
    global _HANDWRITTEN_COUNT
    _FLAT_RULES.clear()
    for cat, rtl_kws, en_kws in RULES:
        for kw in rtl_kws:
            if not kw:
                continue
            pat = _rtl_boundary_pattern(kw) if len(kw) <= _RTL_SHORT_MAX else None
            _FLAT_RULES.append((kw, cat, True, pat))
        for kw in en_kws:
            if kw:
                _FLAT_RULES.append((kw.lower(), cat, False, _latin_boundary_pattern(kw.lower())))
    _FLAT_RULES.sort(key=lambda e: len(e[0]), reverse=True)
    _HANDWRITTEN_COUNT = len(_FLAT_RULES)

    # Learned tokens appended after the hand-written block, then the whole list
    # is re-sorted by length. Sort stability keeps hand-written first on a tie.
    for token, cat in LEARNED.items():
        is_rtl = bool(_RTL_RE.search(token))
        if is_rtl:
            pat = _rtl_boundary_pattern(token) if len(token) <= _RTL_SHORT_MAX else None
        else:
            pat = _latin_boundary_pattern(token)
        _FLAT_RULES.append((token, cat, is_rtl, pat))
    _FLAT_RULES.sort(key=lambda e: len(e[0]), reverse=True)


_build_flat_rules()


def register_learned_keywords(pairs, rebuild: bool = True) -> int:
    """
    Teach the matcher token→category pairs discovered by the LLM assist.
    Returns how many were actually accepted. Rejects anything non-canonical,
    anything targeting the catch-all, and anything a hand-written rule already
    handles (so curated rules can never be shadowed by a learned one).
    """
    accepted = 0
    for token, cat in pairs:
        t = (token or "").strip().lower()
        if len(t) < 3 or cat not in CANONICAL or cat == CATCH_ALL:
            continue
        if t in LEARNED:
            continue
        if _covered_by_handwritten(t):
            continue
        LEARNED[t] = cat
        accepted += 1
    if accepted and rebuild:
        _build_flat_rules()
    return accepted


def learned_stats() -> Dict[str, int]:
    """{'learned': n, 'handwritten': n, 'total': n} — for logging/observability."""
    return {
        "learned": len(LEARNED),
        "handwritten": _HANDWRITTEN_COUNT,
        "total": len(_FLAT_RULES),
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _norm_text(s: str) -> str:
    return re.sub(r"[-_/]+", " ", (s or "").lower()).strip()


def _slug_from_url(url: str) -> str:
    if not url:
        return ""
    seg = url.rstrip("/").split("/")[-1]
    if not seg or re.match(r"^p-?\d", seg) or re.match(r"^\d+$", seg):
        return ""
    return seg


def _keyword_match(text_lat: str, text_rtl: str) -> Optional[str]:
    """Longest-keyword-wins pass over _FLAT_RULES. None if nothing matches."""
    for kw, cat, is_rtl, pat in _FLAT_RULES:
        if is_rtl:
            if not text_rtl:
                continue
            if pat.search(text_rtl) if pat is not None else (kw in text_rtl):
                return cat
        elif text_lat:
            if pat.search(text_lat) if pat is not None else (kw in text_lat):
                return cat
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def is_canonical(category: Optional[str]) -> bool:
    """True if `category` is a value that may legally be stored in the DB."""
    return (category or "").strip() in CANONICAL


def normalize_category_label(raw: Optional[str]) -> Optional[str]:
    """
    Map ANY category label (English slug, Hebrew display name, Title-Case
    taxonomy label, importer variant) → canonical slug.
    Returns None when `raw` is already canonical or is unmappable — matching the
    old db_update_agent._normalize_category() contract ("None = leave it alone").
    """
    s = (raw or "").strip()
    if not s or s in CANONICAL:
        return None
    return VARIANT_MAP.get(s.lower())


def categorize(
    name: str = "",
    name_he: str = "",
    url: str = "",
    existing_category: str = "",
    extra: str = "",
) -> str:
    """
    Return a canonical category slug. ALWAYS returns a value.
    'כללי' is the only fallback — never 'general'/'service-general'/'accessories'.
    """
    ec = (existing_category or "").strip()
    if ec:
        low = ec.lower()
        # An existing canonical value is trusted — EXCEPT the historical fallback
        # buckets, which are exactly the ones we need to re-classify.
        if low in CANONICAL and low not in BAD_FALLBACK_BUCKETS:
            return low
        if low in VARIANT_MAP:
            mapped = VARIANT_MAP[low]
            if mapped not in BAD_FALLBACK_BUCKETS:
                return mapped

    # URL slug → canonical (car-parts.ie encodes the category in the path)
    slug = _slug_from_url(url)
    if slug:
        slug_clean = slug.lower().replace("_", "-")
        if slug_clean in CATEGORY_SLUG_MAP:
            return CATEGORY_SLUG_MAP[slug_clean]

    text_lat = " ".join([_norm_text(slug), _norm_text(name)])

    # RTL text: prefer name_he, but fall back to `name` when name_he is empty and
    # `name` itself holds Hebrew/Arabic. 695,018 'general' parts in this catalog
    # have their Hebrew in `name` with an EMPTY name_he — without this they can
    # never match a Hebrew rule and land in כללי forever.
    text_rtl = name_he or ""
    if not text_rtl and _RTL_RE.search(name or ""):
        text_rtl = name or ""

    # Tyre / wheel size pattern ("225/45R17", "18x8")
    if _TYRE_RE.search(text_lat):
        return "wheels-bearings"

    hit = _keyword_match(text_lat, text_rtl)
    if hit:
        return hit

    # ── LAST RESORT: description / specifications ────────────────────────────
    # Only consulted when the NAME yields nothing, so a rich description can
    # never override a good name match (that would let a passing mention in the
    # blurb decide the category).
    #
    # Measured on the real stuck population 2026-07-28: 87.3% of parts sitting in
    # 'כללי' carry `specifications`, and feeding it rescues 15.4% of them — the
    # cases where the NAME is simply not a part name:
    #     'SsangYong Rexton'  -> specs "עוצר דלת אח' R"  -> body-exterior
    #     'OEM Part'          -> specs "רצועה K/1568 למזגן" -> belts-chains
    #     'Daewoo Leganza - Melling M515' -> desc "Oil Pump" -> engine
    # Those names are the vehicle or a placeholder; the real part is in the blob.
    if extra:
        x_lat = _norm_text(extra)
        x_rtl = extra if _RTL_RE.search(extra) else ""
        if _TYRE_RE.search(x_lat):
            return "wheels-bearings"
        hit = _keyword_match(x_lat, x_rtl)
        if hit:
            return hit

    return CATCH_ALL


def categorize_on_ingest(name: str = "", name_he: str = "", url: str = "",
                         extra: str = "") -> str:
    """
    Categorize a part at IMPORT time. Always returns a canonical slug.
    Every importer must call this instead of returning a hard-coded fallback.

    `extra` is an optional description / specifications blob, used ONLY when the
    name yields no match — many supplier rows carry the vehicle name (or literally
    "OEM Part") as the name and the real part description in the blob.
    """
    return categorize(name=name, name_he=name_he, url=url, extra=extra)


def categorize_slug(slug: str) -> str:
    """Categorize a car-parts.ie URL slug. Returns a canonical slug or 'כללי'."""
    if not slug:
        return CATCH_ALL
    slug_clean = slug.lower().replace("_", "-")
    if slug_clean in CATEGORY_SLUG_MAP:
        return CATEGORY_SLUG_MAP[slug_clean]
    if slug_clean in VARIANT_MAP:
        return VARIANT_MAP[slug_clean]

    slug_text = slug_clean.replace("-", " ")
    hit = _keyword_match(slug_text, "")
    if hit:
        return hit

    for word, cat in (
        ("brake", "brakes"), ("shock", "suspension-steering"),
        ("spring", "suspension-steering"), ("steering", "suspension-steering"),
        ("bearing", "wheels-bearings"), ("clutch", "clutch-drivetrain"),
        ("gear", "gearbox"), ("filter", "filters"), ("engine", "engine"),
        ("exhaust", "exhaust"), ("fuel", "fuel-air"), ("cool", "cooling"),
        ("electric", "electrical"), ("sensor", "electrical"),
        ("light", "lighting"), ("wiper", "wipers-washers"),
        ("body", "body-exterior"), ("air-con", "air-conditioning-heating"),
        ("interior", "interior-comfort"), ("belt", "belts-chains"),
        ("chain", "belts-chains"), ("mirror", "body-exterior"),
        ("window", "body-exterior"), ("door", "body-exterior"),
    ):
        if word in slug_clean:
            return cat
    return CATCH_ALL


def guess_category_by_text(text: str) -> Optional[str]:
    """
    COMPAT SHIM for the former categories.guess_category_by_text().
    Same contract: returns a canonical slug, or None when nothing matches (so
    existing `guess_category_by_text(x) or <fallback>` call sites keep working —
    though those fallbacks should now be CATCH_ALL, never 'general').
    Backed by the merged RULES, so it can no longer disagree with categorize().
    """
    if not text:
        return None
    text_lat = _norm_text(text)
    text_rtl = text if _RTL_RE.search(text) else ""
    if _TYRE_RE.search(text_lat):
        return "wheels-bearings"
    return _keyword_match(text_lat, text_rtl)


__all__ = [
    "CANONICAL", "CATCH_ALL", "BAD_FALLBACK_BUCKETS", "DISPLAY",
    "CATEGORY_SLUG_MAP", "VARIANT_MAP", "RULES",
    "categorize", "categorize_on_ingest", "categorize_slug",
    "guess_category_by_text", "normalize_category_label",
    "is_canonical", "display_name",
    "LEARNED", "register_learned_keywords", "learned_stats",
]
