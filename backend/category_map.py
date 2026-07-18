"""
category_map.py — SINGLE SOURCE OF TRUTH for part categorization (2026-07-13, goal G6).

Every part must land in a correct canonical category, not `general`. Three signals,
tried in order of reliability:
  1. VARIANT_MAP  — normalize an existing messy/duplicate category label
                    ("Brakes"→brakes, "Oil Filters"→filters, "Doors"→body-exterior).
  2. URL slug     — car-parts.ie encodes the category as the last URL path segment
                    (/car-parts/vw/passat-3b2/oil-filter). Fed into the keyword pass.
  3. Keyword pass — RULES matched against "slug + name + name_he", ordered
                    specific→generic so a wrong (over-broad) rule can't fire first.
                    A wrong category is worse than `general`, so ambiguous single
                    words are deliberately NOT rules (require a disambiguating word).

Used by the backfill (recategorize_backlog.py) AND the import pipeline (categorize on
ingest) so the catalog never drifts uncategorized again.
"""
import re

# The 22 canonical lowercase-slug categories actually used in parts_catalog.category.
CANONICAL = {
    "body-exterior", "service-general", "electrical-sensors", "interior-comfort",
    "engine", "air-conditioning-heating", "suspension-steering", "brakes",
    "wheels-bearings", "fuel-air", "lighting", "cooling", "exhaust",
    "clutch-drivetrain", "gearbox", "wipers-washers", "belts-chains",
    "safety-systems", "filters", "fluids", "accessories", "hybrid-ev",
}

# Messy/duplicate existing labels → canonical. Keys are lowercased+stripped.
VARIANT_MAP = {
    "brakes": "brakes", "brake pads": "brakes", "brake rotors": "brakes",
    "calipers": "brakes", "brakes-clutch": "brakes",
    "body parts": "body-exterior", "body": "body-exterior", "doors": "body-exterior",
    "bumpers": "body-exterior", "fenders": "body-exterior", "hoods": "body-exterior",
    "auto glass": "body-exterior", "window regulators": "body-exterior",
    "engine parts": "engine", "engine": "engine", "engine components": "engine",
    "engine cooling": "cooling", "cooling system": "cooling", "cooling": "cooling",
    "radiators": "cooling", "water pumps": "cooling", "thermostats": "cooling",
    "service & general": "service-general", "general parts": "service-general",
    "auto parts": "service-general",
    "electrical": "electrical-sensors", "electronics": "electrical-sensors",
    "sensors": "electrical-sensors", "wiring & modules": "electrical-sensors",
    "audio & electronics": "electrical-sensors", "cameras & gps": "electrical-sensors",
    "batteries & power": "electrical-sensors", "alternators & starters": "electrical-sensors",
    "electrical-lighting": "electrical-sensors",
    "lighting": "lighting", "headlights": "lighting", "tail lights": "lighting",
    "fog lights": "lighting",
    "suspension": "suspension-steering", "suspension & steering": "suspension-steering",
    "steering": "suspension-steering", "shocks & struts": "suspension-steering",
    "control arms": "suspension-steering", "tie rods & joints": "suspension-steering",
    "interior": "interior-comfort", "seats": "interior-comfort",
    "fuel system": "fuel-air", "fuel & air": "fuel-air", "fuel delivery": "fuel-air",
    "fuel-system": "fuel-air",
    "filters": "filters", "oil filters": "filters", "air filters": "filters",
    "filters-oils": "filters", "filters-oil": "filters",
    "transmission": "gearbox", "drivetrain": "clutch-drivetrain",
    "driveline & axles": "clutch-drivetrain", "driveshafts": "clutch-drivetrain",
    "a/c & heating": "air-conditioning-heating", "a/c compressors": "air-conditioning-heating",
    "condensers": "air-conditioning-heating",
    "wipers & washers": "wipers-washers", "wiper blades": "wipers-washers",
    "wheels & tires": "wheels-bearings", "wheel bearings & hubs": "wheels-bearings",
    "exhaust": "exhaust",
    "safety": "safety-systems", "gaskets & seals": "engine",
    "tools & accessories": "tools-equipment", "tools-equipment": "tools-equipment",
    "timing belts": "belts-chains",
}

# Keyword RULES — (category, [substrings]). Matched against lowercased
# "slug name name_he" (dashes→spaces). ORDER = specific→generic; first hit wins.
RULES = [
    # ── safety (first: false negatives are dangerous) ──
    ("safety-systems", ["airbag", "air bag", "seat belt", "seatbelt", "pretensioner",
        "seat-belt", "crash sensor", "impact sensor", "curtain air", "knee airbag",
        "srs ", "כרית אוויר", "חגורת בטיחות"]),
    # ── filters (before engine/fuel so 'oil filter' → filters not engine) ──
    ("filters", ["oil filter", "air filter", "fuel filter", "pollen filter",
        "cabin filter", "sports air filter", "particulate filter", "מסנן", "פילטר"]),
    # ── brakes ──
    ("brakes", ["brake", "abs pump", "abs ring", "caliper", "handbrake", "brake shoe",
        "brake disc", "brake pad", "brake drum", "brake booster", "brake fluid",
        "brake light switch", "rotor", "בלם", "רפידות", "דיסק בלם"]),
    # ── clutch & drivetrain (before engine; 'clutch','cv','propshaft','differential') ──
    ("clutch-drivetrain", ["clutch", "cv joint", "cv boot", "propshaft", "prop shaft",
        "drive shaft", "driveshaft", "half shaft", "differential", "flywheel",
        "slave cylinder", "pressure plate", "tripod", "מצמד", "פלנץ"]),
    # ── gearbox / transmission ──
    ("gearbox", ["gearbox", "transmission", "transaxle", "gear lever", "shift",
        "gear selector", "transmission fluid", "gear knob", "תיבת הילוכים", "גיר"]),
    # ── suspension & steering ──
    ("suspension-steering", ["shock absorber", "shock-absorber", "strut", "control arm",
        "tie rod", "track rod", "ball joint", "stabilizer", "anti-roll", "anti roll",
        "sway bar", "steering rack", "steering column", "power steering", "steering damper",
        "steering angle", "wishbone", "axle bush", "stub axle", "inner tie", "suspension",
        "coil spring", "leaf spring", "bump stop", "torsion bar", "מוט הגה", "בולם", "מייצב"]),
    # ── wheels & bearings ──
    ("wheels-bearings", ["wheel bearing", "hub bearing", "wheel hub", "wheel nut",
        "lug nut", "wheel stud", "wheel bolt", "hub cap", "center cap", "abs ring",
        "tone ring", "wheel arch", "tyre", " tire", "r15", "r16", "r17", "r18", "r19",
        "מיסב", "גלגל", "צמיג", "חישוק"]),
    # ── exhaust ──
    ("exhaust", ["exhaust", "silencer", "muffler", "tailpipe", "catalytic", "cat converter",
        "flex pipe", "downpipe", "resonator", "manifold gasket", "אגזוז", "מפלט"]),
    # ── cooling ──
    ("cooling", ["radiator", "water pump", "thermostat", "coolant", "expansion tank",
        "intercooler", "cooling fan", "radiator fan", "fan clutch", "oil cooler",
        "coolant flange", "מצנן", "רדיאטור", "משאבת מים"]),
    # ── air-conditioning & heating ──
    ("air-conditioning-heating", ["air conditioning", "a/c ", "ac compressor",
        "compressor clutch", "condenser", "evaporator", "blower motor", "heater core",
        "expansion valve", "receiver drier", "dryer", "climate control", "מזגן", "מאייד",
        "מדחס"]),
    # ── belts & chains ──
    ("belts-chains", ["timing belt", "timing chain", "v-belt", "v belt", "serpentine",
        "drive belt", "fan belt", "tensioner", "belt pulley", "chain tensioner",
        "רצועת", "שרשרת תזמון", "רצועה"]),
    # ── lighting ──
    ("lighting", ["headlight", "headlamp", "tail light", "tail lamp", "taillight",
        "fog light", "fog lamp", "turn signal", "indicator", "daytime running", "drl",
        "reverse light", "reverse-light", "backup light", "dome light", "dome lamp",
        " bulb", " led ", "reflector", "side marker", "corner light", "stop light",
        "brake light", "number plate light", "license plate light", "פנס", "נורה", "תאורה"]),
    # ── wipers & washers ──
    ("wipers-washers", ["wiper", "washer pump", "washer nozzle", "windscreen washer",
        "washer fluid", "washer bottle", "windshield wiper", "מגב", "מתז"]),
    # ── fuel & air ──
    ("fuel-air", ["fuel pump", "fuel pressure", "fuel injector", "injector", "carburetor",
        "throttle body", "throttle sensor", "intake manifold", "fuel tank", "fuel rail",
        "fuel line", "fuel cap", "fuel sender", "turbocharger", "turbo ", "supercharger",
        "secondary air pump", "map sensor", "maf ", "mass air", "משאבת דלק", "מזרק"]),
    # ── engine ──
    ("engine", ["piston", "head gasket", "crankshaft", "camshaft", "spark plug",
        "glow plug", "cylinder head", "engine mount", "engine oil", "oil pump", "oil pan",
        "oil dipstick", "valve cover", "rocker cover", "crankcase", "distributor",
        "ignition coil", "timing", "engine gasket", "valve", "connecting rod", "oil seal",
        "cam seal", "crank seal", "engine block", "sump", "מנוע", "בוכנה", "גל ארכובה",
        "מצת", "שסתום"]),
    # ── electrical & sensors (after engine so 'crankshaft sensor' → engine? keep sensor generic here) ──
    ("electrical-sensors", ["sensor", "relay", " fuse", "fuse box", "control unit", "ecu",
        "module", "alternator", "starter", "battery", "wiring", "wire harness", "harness",
        "connector", "socket", "switch", "ignition switch", "central locking", "horn",
        "aerial", "antenna", "solenoid", "actuator", "abs sensor", "lambda sensor",
        "o2 sensor", "parking sensor", "camera", "immobilizer", "spark plug wire",
        "חיישן", "ממסר", "מצבר", "אלטרנטור", "מתנע", "מתג", "צמת חוטים"]),
    # ── interior comfort ──
    ("interior-comfort", ["seat", "armrest", "backrest", "headrest", "dashboard", "dash panel",
        "door panel", "door card", "door handle", "cup holder", "sun visor", "visor",
        "cargo cover", "floor mat", "carpet", "center console", "console", "cushion",
        "gear knob", "shift knob", "pillar trim", "door trim", "seat adjust", "seat recliner",
        "window regulator", "window crank", "window motor", "window lifter", "instrument cluster",
        "instrument panel", "glove box", "grab handle", "מושב", "ריפוד", "לוח מחוונים", "ידית"]),
    # ── body & exterior ──
    ("body-exterior", ["bumper", "fender", "wing ", "hood", "bonnet", "trunk lid", "boot lid",
        "tailgate", "liftgate", "grille", "grill", "spoiler", "side skirt", "rocker panel",
        "windshield", "windscreen", "window glass", "door glass", "quarter glass",
        "mirror", "wing mirror", "door mirror", "rearview", "pillar", "body panel", "body kit",
        "mud flap", "splash", "fender liner", "wheel arch cover", "roof rail", "door sill",
        "molding", "moulding", "cowl", "apron", "sunroof", "door lock", "tailgate lock",
        "door seal", "window seal", "central lock", "towbar", "tow bar", "chrome trim",
        "side trim", "quarter panel", "rocker", "פגוש", "כנף", "דלת", "שמשה", "ראי", "מכסה מנוע"]),
    # ── fluids ──
    ("fluids", ["engine oil", "gear oil", "hydraulic oil", "brake fluid", "coolant fluid",
        "antifreeze", "atf ", "grease", "lubricant", "additive", "שמן מנוע", "נוזל בלם"]),
    # ── service-general (consumables / generic service items) ──
    ("service-general", ["service kit", "maintenance kit", "drain plug", "sump plug",
        "gasket set", "seal kit", "repair kit", "bolt set", "screw set", "clip set"]),
]


def _norm_text(s: str) -> str:
    return re.sub(r"[-_/]+", " ", (s or "").lower()).strip()


def _slug_from_url(url: str) -> str:
    if not url:
        return ""
    seg = url.rstrip("/").split("/")[-1]
    if not seg or re.match(r"^p-?\d", seg) or re.match(r"^\d+$", seg):
        return ""
    return seg


def categorize(name: str = "", name_he: str = "", url: str = "",
               existing_category: str = "") -> str | None:
    """Return a canonical category slug, or None if genuinely undecidable (→ leave general)."""
    # 1. normalize an existing messy label
    ec = (existing_category or "").strip()
    if ec:
        low = ec.lower()
        if low in CANONICAL:
            return low
        if low in VARIANT_MAP:
            return VARIANT_MAP[low]
    # 2+3. keyword pass over slug + name + name_he (Hebrew kept as-is)
    slug = _slug_from_url(url)
    text_lat = " ".join([_norm_text(slug), _norm_text(name)])
    text_he = name_he or ""
    for cat, kws in RULES:
        for kw in kws:
            if re.search(r"[֐-׿]", kw):   # Hebrew keyword
                if kw in text_he:
                    return cat
            elif kw in text_lat:
                return cat
    return None
