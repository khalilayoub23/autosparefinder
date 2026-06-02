from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _normalize_text(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    folded = unicodedata.normalize("NFKD", raw)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.casefold()
    folded = re.sub(r"[^\w\u0590-\u05FF]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


@dataclass(frozen=True)
class PartSubcategory:
    id: str
    label: str
    aliases: Tuple[str, ...] = ()
    keywords: Tuple[str, ...] = ()

    @property
    def match_terms(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys((self.label, self.id, *self.aliases, *self.keywords)))

    @property
    def normalized_terms(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(_normalize_text(term) for term in self.match_terms if _normalize_text(term)))

    def serialize(self, count: int = 0) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "count": count,
            "aliases": list(self.aliases),
        }


@dataclass(frozen=True)
class PartTypeFamily:
    id: str
    label: str
    group_id: str
    group_label: str
    badge: str
    icon_key: str
    palette: Tuple[str, str]
    aliases: Tuple[str, ...] = ()
    legacy_categories: Tuple[str, ...] = ()
    keywords: Tuple[str, ...] = ()
    subcategories: Tuple[PartSubcategory, ...] = ()

    @property
    def match_terms(self) -> Tuple[str, ...]:
        sub_terms: List[str] = []
        for subcategory in self.subcategories:
            sub_terms.extend(subcategory.match_terms)
        return tuple(
            dict.fromkeys(
                (
                    self.label,
                    self.id,
                    *self.aliases,
                    *self.legacy_categories,
                    *self.keywords,
                    *sub_terms,
                )
            )
        )

    @property
    def normalized_terms(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(_normalize_text(term) for term in self.match_terms if _normalize_text(term)))

    def serialize(self, count: int = 0) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "group": self.group_label,
            "group_id": self.group_id,
            "count": count,
            "badge": self.badge,
            "icon_key": self.icon_key,
            "palette": list(self.palette),
            "aliases": list(self.aliases),
            "legacy_categories": list(self.legacy_categories),
            "subcategories": [subcategory.serialize() for subcategory in self.subcategories],
        }


PART_TYPE_FAMILIES: Tuple[PartTypeFamily, ...] = (
    PartTypeFamily(
        id="filters",
        label="Filters",
        group_id="maintenance",
        group_label="Maintenance",
        badge="FLT",
        icon_key="filter",
        palette=("#d97706", "#f59e0b"),
        aliases=("filters", "filter", "פילטרים", "מסננים", "סינון"),
        keywords=("oil filter", "air filter", "cabin filter", "fuel filter", "filter", "מסנן", "פילטר"),
        subcategories=(
            PartSubcategory("air-filters", "Air Filters", aliases=("air filter", "מסנן אוויר")),
            PartSubcategory("oil-filters", "Oil Filters", aliases=("oil filter", "מסנן שמן")),
            PartSubcategory("fuel-filters", "Fuel Filters", aliases=("fuel filter", "מסנן דלק")),
            PartSubcategory("cabin-air-filters", "Cabin Air Filters", aliases=("cabin filter", "מסנן מזגן")),
        ),
    ),
    PartTypeFamily(
        id="fluids",
        label="Fluids & Lubricants",
        group_id="maintenance",
        group_label="Maintenance",
        badge="FLD",
        icon_key="fluid",
        palette=("#0284c7", "#38bdf8"),
        aliases=("fluids", "lubricants", "oil", "liquids", "שמנים ונוזלים"),
        keywords=("oil", "coolant", "antifreeze", "atf", "brake fluid", "washer fluid", "שמן", "נוזל", "קירור"),
        subcategories=(
            PartSubcategory("motor-oils", "Motor Oils", aliases=("engine oil", "שמן מנוע")),
            PartSubcategory("transmission-fluids", "Transmission Fluids", aliases=("atf", "שמן גיר")),
            PartSubcategory("coolants-antifreeze", "Coolants & Antifreeze", aliases=("coolant", "נוזל קירור")),
            PartSubcategory("brake-fluids", "Brake Fluids", aliases=("brake fluid", "נוזל בלמים")),
        ),
    ),
    PartTypeFamily(
        id="belts-chains",
        label="Belts & Chains",
        group_id="maintenance",
        group_label="Maintenance",
        badge="BLT",
        icon_key="belt",
        palette=("#7c3aed", "#a78bfa"),
        aliases=("belts", "chains", "timing", "timing kit", "שרשראות ורצועות", "רצועות ושרשראות"),
        legacy_categories=("שרשראות ורצועות",),
        keywords=("belt", "chain", "timing", "idler", "tensioner", "רצועה", "שרשרת", "מותחן"),
        subcategories=(
            PartSubcategory("timing-belts", "Timing Belts", aliases=("timing belt", "רצועת טיימינג")),
            PartSubcategory("timing-chains", "Timing Chains", aliases=("timing chain", "שרשרת טיימינג")),
            PartSubcategory("tensioners-idlers", "Tensioners & Idlers", aliases=("tensioner", "מותחן")),
        ),
    ),
    PartTypeFamily(
        id="service-general",
        label="Service & General",
        group_id="maintenance",
        group_label="Maintenance",
        badge="GEN",
        icon_key="service",
        palette=("#475569", "#94a3b8"),
        aliases=("general", "service", "maintenance", "misc", "טיפול וכללי", "כללי"),
        legacy_categories=("כללי",),
        keywords=("service", "maintenance", "repair kit", "kit", "general", "bolt", "nut", "screw", "clip", "stud", "setscrew", "o-ring", "seal", "gasket", "retainer", "shim", "washer", "כללי", "ערכת תיקון", "ברגים", "אטמים"),
        subcategories=(
            PartSubcategory("auto-detailing", "Auto Detailing", aliases=("detailing", "טיפוח")),
            PartSubcategory("repair-manuals", "Repair Manuals", aliases=("manual", "ספר רכב")),
            PartSubcategory("service-kits", "Service Kits", aliases=("service kit", "ערכת טיפול")),
        ),
    ),
    PartTypeFamily(
        id="engine",
        label="Engine",
        group_id="parts",
        group_label="Parts",
        badge="ENG",
        icon_key="engine",
        palette=("#dc2626", "#fb7185"),
        aliases=("engine", "motor", "מנוע"),
        legacy_categories=("מנוע",),
        keywords=("engine", "motor", "gasket", "valve", "camshaft", "piston", "אטם", "שסתום", "בוכנה"),
        subcategories=(
            PartSubcategory("gaskets-seals", "Gaskets & Seals", aliases=("gasket", "אטם")),
            PartSubcategory("engine-components", "Engine Components", aliases=("piston", "valve", "רכיבי מנוע")),
            PartSubcategory("turbo-superchargers", "Turbo & Superchargers", aliases=("turbo", "supercharger", "טורבו")),
        ),
    ),
    PartTypeFamily(
        id="cooling",
        label="Engine Cooling",
        group_id="parts",
        group_label="Parts",
        badge="CLG",
        icon_key="cooling",
        palette=("#0f766e", "#2dd4bf"),
        aliases=("cooling", "radiator", "thermostat", "water pump", "קירור מנוע"),
        keywords=("radiator", "thermostat", "water pump", "cooling", "fan", "רדיאטור", "תרמוסטט", "משאבת מים", "מאוורר"),
        subcategories=(
            PartSubcategory("radiators", "Radiators", aliases=("radiator", "רדיאטור")),
            PartSubcategory("water-pumps", "Water Pumps", aliases=("water pump", "משאבת מים")),
            PartSubcategory("thermostats", "Thermostats", aliases=("thermostat", "תרמוסטט")),
            PartSubcategory("cooling-fans", "Cooling Fans", aliases=("fan", "מאוורר")),
        ),
    ),
    PartTypeFamily(
        id="fuel-air",
        label="Fuel & Air",
        group_id="parts",
        group_label="Parts",
        badge="FUEL",
        icon_key="fuel",
        palette=("#2563eb", "#60a5fa"),
        aliases=("fuel", "intake", "injection", "fuel system", "דלק ויניקה"),
        legacy_categories=("דלק",),
        keywords=("fuel", "injector", "pump", "intake", "throttle", "hose", "pipe", "tube", "elbow", "induct", "turbo hose", "דלק", "מזרק", "משאבה", "יניקה", "מצערת", "צינור"),
        subcategories=(
            PartSubcategory("air-intake", "Air Intake", aliases=("intake", "יניקה")),
            PartSubcategory("fuel-delivery", "Fuel Delivery", aliases=("fuel pump", "משאבת דלק")),
            PartSubcategory("injectors", "Injectors", aliases=("injector", "מזרק")),
            PartSubcategory("throttle-body", "Throttle Body", aliases=("throttle", "מצערת")),
        ),
    ),
    PartTypeFamily(
        id="exhaust",
        label="Exhaust",
        group_id="parts",
        group_label="Parts",
        badge="EXH",
        icon_key="exhaust",
        palette=("#7c2d12", "#fb923c"),
        aliases=("exhaust", "emissions", "egr", "dpf", "פליטה", "פליטה ו-EGR"),
        keywords=("exhaust", "muffler", "catalytic", "lambda", "egr", "dpf", "פליטה", "אגזוז", "קטליטי", "חיישן חמצן"),
        subcategories=(
            PartSubcategory("mufflers", "Mufflers", aliases=("muffler", "דוד אגזוז")),
            PartSubcategory("catalytic-converters", "Catalytic Converters", aliases=("catalytic", "קטליטי")),
            PartSubcategory("egr-components", "EGR Components", aliases=("egr", "שסתום egr")),
            PartSubcategory("oxygen-sensors", "Oxygen Sensors", aliases=("lambda", "חיישן חמצן")),
        ),
    ),
    PartTypeFamily(
        id="clutch-drivetrain",
        label="Driveline & Axles",
        group_id="performance",
        group_label="Performance",
        badge="DRV",
        icon_key="drivetrain",
        palette=("#9333ea", "#c084fc"),
        aliases=("clutch", "driveshaft", "cv joint", "axle", "קלאץ' והנעה"),
        keywords=("clutch", "flywheel", "axle", "drive shaft", "propshaft", "cv joint", "final drive", "driveline", "קלאץ", "גל הינע", "ציריה", "פעמון"),
        subcategories=(
            PartSubcategory("clutch-kits", "Clutch Kits", aliases=("clutch kit", "ערכת קלאץ")),
            PartSubcategory("cv-axles", "CV Axles", aliases=("cv axle", "ציריה")),
            PartSubcategory("driveshafts", "Driveshafts", aliases=("driveshaft", "גל הינע")),
            PartSubcategory("flywheels", "Flywheels", aliases=("flywheel", "גלגל תנופה")),
        ),
    ),
    PartTypeFamily(
        id="gearbox",
        label="Transmission",
        group_id="parts",
        group_label="Parts",
        badge="GBX",
        icon_key="gearbox",
        palette=("#4f46e5", "#818cf8"),
        aliases=("gearbox", "transmission", "gear", "differential", "גיר", "גיר ותמסורת"),
        keywords=("gearbox", "transmission", "gear", "differential", "seal kit", "גיר", "תמסורת", "דיפרנציאל"),
        subcategories=(
            PartSubcategory("automatic-transmission", "Automatic Transmission", aliases=("automatic gearbox", "גיר אוטומטי")),
            PartSubcategory("manual-transmission", "Manual Transmission", aliases=("manual gearbox", "גיר ידני")),
            PartSubcategory("differentials", "Differentials", aliases=("differential", "דיפרנציאל")),
        ),
    ),
    PartTypeFamily(
        id="brakes",
        label="Brakes",
        group_id="parts",
        group_label="Parts",
        badge="BRK",
        icon_key="brake",
        palette=("#b91c1c", "#f87171"),
        aliases=("brakes", "brake", "בלמים"),
        legacy_categories=("בלמים",),
        keywords=("brake", "disc", "rotor", "pad", "caliper", "בלם", "דיסק", "רפידה", "קאליפר"),
        subcategories=(
            PartSubcategory("brake-pads", "Brake Pads", aliases=("pads", "רפידות")),
            PartSubcategory("brake-rotors", "Brake Rotors", aliases=("rotor", "צלחת בלם")),
            PartSubcategory("calipers", "Calipers", aliases=("caliper", "קאליפר")),
            PartSubcategory("brake-hydraulics", "Brake Hydraulics", aliases=("booster", "משאבת בלם")),
        ),
    ),
    PartTypeFamily(
        id="suspension-steering",
        label="Suspension & Steering",
        group_id="parts",
        group_label="Parts",
        badge="SUS",
        icon_key="suspension",
        palette=("#0f766e", "#34d399"),
        aliases=("suspension", "steering", "chassis", "מתלה והיגוי", "מתלה", "היגוי"),
        legacy_categories=("מתלה", "היגוי"),
        keywords=("suspension", "steering", "shock", "strut", "arm", "tie rod", "rack", "בולם", "תפוח", "זרוע", "מסרק", "הגה"),
        subcategories=(
            PartSubcategory("shocks-struts", "Shocks & Struts", aliases=("shock", "strut", "בולם")),
            PartSubcategory("control-arms", "Control Arms", aliases=("control arm", "זרוע")),
            PartSubcategory("steering-racks", "Steering Racks", aliases=("rack", "מסרק")),
            PartSubcategory("tie-rods-joints", "Tie Rods & Joints", aliases=("tie rod", "תפוח הגה")),
        ),
    ),
    PartTypeFamily(
        id="wheels-bearings",
        label="Wheels & Tires",
        group_id="wheels",
        group_label="Wheels",
        badge="WHL",
        icon_key="wheel",
        palette=("#374151", "#9ca3af"),
        aliases=("wheels", "tires", "wheel bearing", "hub", "גלגלים ומיסבים", "גלגלים וצמיגים"),
        legacy_categories=("גלגלים וצמיגים",),
        keywords=("wheel", "tire", "tyre", "rim", "hub", "bearing", "גלגל", "צמיג", "ג'נט", "מיסב"),
        subcategories=(
            PartSubcategory("custom-wheels", "Custom Wheels", aliases=("custom wheel", "ג'נטים")),
            PartSubcategory("tires", "Tires", aliases=("tire", "צמיג")),
            PartSubcategory("wheel-covers", "Wheel Covers", aliases=("wheel cover", "טסה")),
            PartSubcategory("lug-nuts-locks", "Lug Nuts & Locks", aliases=("lug nut", "אומי גלגל")),
            PartSubcategory("wheel-spacers", "Wheel Spacers", aliases=("spacer", "ספייסר")),
            PartSubcategory("wheel-bearings-hubs", "Wheel Bearings & Hubs", aliases=("hub", "מיסב גלגל")),
        ),
    ),
    PartTypeFamily(
        id="body-exterior",
        label="Body Parts",
        group_id="body-parts",
        group_label="Body Parts",
        badge="BDY",
        icon_key="body",
        palette=("#0891b2", "#67e8f9"),
        aliases=("body", "bodywork", "exterior", "bumper", "מרכב וחוץ", "גוף הרכב", "פחיין ומרכב"),
        legacy_categories=("פחיין ומרכב",),
        keywords=("body", "bumper", "grille", "door", "hood", "mirror", "fender", "moulding", "finisher", "bezel", "treadplate", "casing", "cover", "undertray", "heatshield", "insulator", "reinforcement", "badge", "panel", "trim panel", "מרכב", "פגוש", "גריל", "מראה", "כנף", "תושבת", "בית"),
        subcategories=(
            PartSubcategory("bumpers", "Bumpers", aliases=("bumper", "פגוש")),
            PartSubcategory("fenders", "Fenders", aliases=("fender", "כנף")),
            PartSubcategory("hoods", "Hoods", aliases=("hood", "מכסה מנוע")),
            PartSubcategory("mirrors", "Mirrors", aliases=("mirror", "מראה")),
            PartSubcategory("grilles", "Grilles", aliases=("grille", "גריל")),
            PartSubcategory("doors", "Doors", aliases=("door", "דלת")),
            PartSubcategory("quarter-panels", "Quarter Panels", aliases=("quarter panel", "כנף אחורית")),
            PartSubcategory("running-boards", "Running Boards", aliases=("running board", "מדרגה")),
            PartSubcategory("roof-racks", "Roof Racks", aliases=("roof rack", "גגון")),
            PartSubcategory("spoilers", "Spoilers", aliases=("spoiler", "ספוילר")),
        ),
    ),
    PartTypeFamily(
        id="lighting",
        label="Lighting",
        group_id="lighting",
        group_label="Lighting",
        badge="LGT",
        icon_key="lighting",
        palette=("#ca8a04", "#fde047"),
        aliases=("lighting", "lights", "lamp", "headlight", "תאורה"),
        legacy_categories=("תאורה",),
        keywords=("light", "lamp", "headlight", "tail light", "fog", "bulb", "תאורה", "פנס", "נורה"),
        subcategories=(
            PartSubcategory("headlights", "Headlights", aliases=("headlight", "פנס קדמי")),
            PartSubcategory("tail-lights", "Tail Lights", aliases=("tail light", "פנס אחורי")),
            PartSubcategory("fog-lights", "Fog Lights", aliases=("fog light", "פנס ערפל")),
            PartSubcategory("led-lights", "LED Lights", aliases=("led", "לד")),
            PartSubcategory("signal-lights", "Signal Lights", aliases=("turn signal", "איתות")),
            PartSubcategory("car-bulbs", "Car Bulbs", aliases=("bulb", "נורה")),
            PartSubcategory("emergency-warning-lighting", "Emergency & Warning Lighting", aliases=("warning light", "תאורת חירום")),
        ),
    ),
    PartTypeFamily(
        id="electrical-sensors",
        label="Audio & Electronics",
        group_id="audio-electronics",
        group_label="Audio & Electronics",
        badge="ELE",
        icon_key="electrical",
        palette=("#1d4ed8", "#93c5fd"),
        aliases=("electrical", "electronics", "sensors", "wiring", "חשמל וחיישנים", "חשמל ואלקטרוניקה"),
        legacy_categories=("חשמל רכב",),
        keywords=("sensor", "switch", "starter", "alternator", "ignition", "relay", "module", "harness", "wiring loom", "connector", "receiver", "amplifier", "ecu", "control unit", "חשמל", "חיישן", "אלטרנטור", "סטרטר", "ממסר", "צמה", "חיווט"),
        subcategories=(
            PartSubcategory("sensors", "Sensors", aliases=("sensor", "חיישן")),
            PartSubcategory("alternators-starters", "Alternators & Starters", aliases=("alternator", "starter", "אלטרנטור", "סטרטר")),
            PartSubcategory("batteries-power", "Batteries & Power", aliases=("battery", "מצבר")),
            PartSubcategory("wiring-modules", "Wiring & Modules", aliases=("wiring", "module", "צמה", "מודול")),
            PartSubcategory("tpms-sensors", "TPMS Sensors", aliases=("tpms", "חיישן לחץ אוויר")),
            PartSubcategory("cameras-gps", "Cameras & GPS", aliases=("camera", "gps", "מצלמה", "ניווט")),
            PartSubcategory("stereos-audio", "Stereos & Audio", aliases=("stereo", "speaker", "מערכת שמע")),
            PartSubcategory("bluetooth-connectivity", "Bluetooth & Connectivity", aliases=("bluetooth", "usb", "aux")),
        ),
    ),
    PartTypeFamily(
        id="air-conditioning-heating",
        label="A/C & Heating",
        group_id="interior",
        group_label="Interior",
        badge="AC",
        icon_key="climate",
        palette=("#0369a1", "#7dd3fc"),
        aliases=("air conditioning", "ac", "hvac", "heating", "מיזוג וחימום", "מזגן וחימום"),
        legacy_categories=("מיזוג",),
        keywords=("ac", "a/c", "compressor", "condenser", "blower", "heater", "climate", "מזגן", "מדחס", "מעבה", "חימום"),
        subcategories=(
            PartSubcategory("ac-compressors", "A/C Compressors", aliases=("compressor", "מדחס")),
            PartSubcategory("condensers", "Condensers", aliases=("condenser", "מעבה")),
            PartSubcategory("evaporators-heater-core", "Evaporators & Heater Core", aliases=("evaporator", "heater core", "מאייד")),
            PartSubcategory("blower-motors", "Blower Motors", aliases=("blower", "מפוח")),
            PartSubcategory("hvac-controls", "HVAC Controls", aliases=("hvac control", "פיקוד מזגן")),
        ),
    ),
    PartTypeFamily(
        id="wipers-washers",
        label="Wipers & Washers",
        group_id="exterior",
        group_label="Exterior",
        badge="WPR",
        icon_key="wiper",
        palette=("#0f766e", "#99f6e4"),
        aliases=("wipers", "washer", "windscreen", "מגבים וניקוי שמשות", "שמשות ומגבים", "מגבים"),
        legacy_categories=("מגבים",),
        keywords=("wiper", "washer", "windscreen", "מגב", "מתז", "זרוע מגב"),
        subcategories=(
            PartSubcategory("wiper-blades", "Wiper Blades", aliases=("wiper blade", "להב מגב")),
            PartSubcategory("washer-pumps", "Washer Pumps", aliases=("washer pump", "משאבת מתז")),
            PartSubcategory("washer-nozzles", "Washer Nozzles", aliases=("washer nozzle", "דיזה")),
            PartSubcategory("window-regulators", "Window Regulators", aliases=("window regulator", "מווסת חלון")),
            PartSubcategory("auto-glass", "Auto Glass", aliases=("windshield", "glass", "שמשה")),
        ),
    ),
    PartTypeFamily(
        id="interior-comfort",
        label="Interior",
        group_id="interior",
        group_label="Interior",
        badge="INT",
        icon_key="interior",
        palette=("#7c3aed", "#ddd6fe"),
        aliases=("interior", "comfort", "seat", "trim", "פנים ונוחות", "פנים הרכב", "ריפוד ופנים"),
        legacy_categories=("ריפוד ופנים",),
        keywords=("interior", "seat", "headrest", "squab", "cushion", "bolster", "armrest", "headlining", "sunvisor", "glovebox", "console", "dashboard", "trim", "window switch", "ריפוד", "מושב", "דשבורד", "פנים", "ידית", "משענת"),
        subcategories=(
            PartSubcategory("floor-mats", "Floor Mats", aliases=("floor mat", "שטיח")),
            PartSubcategory("seat-covers", "Seat Covers", aliases=("seat cover", "כיסוי מושב")),
            PartSubcategory("seats", "Seats", aliases=("seat", "מושב")),
            PartSubcategory("dash-covers", "Dash Covers", aliases=("dash cover", "כיסוי דש")),
            PartSubcategory("steering-wheels", "Steering Wheels", aliases=("steering wheel", "הגה")),
            PartSubcategory("shift-knobs", "Shift Knobs", aliases=("shift knob", "ידית הילוכים")),
            PartSubcategory("sun-shades", "Sun Shades", aliases=("sun shade", "צלון")),
            PartSubcategory("car-organizers", "Car Organizers", aliases=("organizer", "ארגונית")),
            PartSubcategory("pedals", "Pedals", aliases=("pedal", "דוושה")),
        ),
    ),
    PartTypeFamily(
        id="accessories",
        label="Automotive Tools",
        group_id="automotive-tools",
        group_label="Automotive Tools",
        badge="ACC",
        icon_key="accessories",
        palette=("#475569", "#cbd5e1"),
        aliases=("accessories", "general", "care", "tools", "אביזרים וכללי", "כלי עבודה ואביזרים"),
        legacy_categories=("כללי",),
        keywords=("accessory", "cover", "mat", "cleaner", "tool", "אביזר", "כיסוי", "שטיח", "ניקוי", "כלי"),
        subcategories=(
            PartSubcategory("diagnostic-testing-tools", "Diagnostic & Testing Tools", aliases=("diagnostic", "scanner", "דיאגנוסטיקה")),
            PartSubcategory("engine-service-tools", "Engine Service Tools", aliases=("engine tool", "כלי מנוע")),
            PartSubcategory("oil-change-tools", "Oil Change Tools", aliases=("oil change", "כלי שמן")),
            PartSubcategory("jacks-lifts-stands", "Jacks, Lifts & Stands", aliases=("jack", "lift", "ג'ק")),
            PartSubcategory("lockout-kits", "Lockout Kits", aliases=("lockout", "קיט פריצה")),
            PartSubcategory("ac-tools-equipment", "A/C Tools & Equipment", aliases=("ac tools", "ציוד מזגן")),
            PartSubcategory("service-carts", "Service Carts", aliases=("service cart", "עגלת שירות")),
            PartSubcategory("paint-body-tools", "Automotive Paint & Body Tools", aliases=("paint", "body tool", "כלי פחחות")),
            PartSubcategory("ev-charging", "EV Charging", aliases=("ev", "charging", "טעינה")),
        ),
    ),
)


PART_TYPE_FAMILY_BY_ID: Dict[str, PartTypeFamily] = {family.id: family for family in PART_TYPE_FAMILIES}
PART_SUBCATEGORY_BY_ID: Dict[str, Tuple[PartTypeFamily, PartSubcategory]] = {}
for _family in PART_TYPE_FAMILIES:
    for _subcategory in _family.subcategories:
        PART_SUBCATEGORY_BY_ID[_subcategory.id] = (_family, _subcategory)


def get_part_type_groups(family_counts: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    groups: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for family in PART_TYPE_FAMILIES:
        if family.group_id in seen:
            continue
        seen.add(family.group_id)
        group_count = 0
        if family_counts:
            group_count = sum(
                int(family_counts.get(candidate.id, 0))
                for candidate in PART_TYPE_FAMILIES
                if candidate.group_id == family.group_id
            )
        groups.append({"id": family.group_id, "label": family.group_label, "count": group_count})
    groups.sort(key=lambda item: (-int(item.get("count", 0)), item["label"]))
    return groups


def resolve_part_type_family(value: Optional[str]) -> Optional[PartTypeFamily]:
    normalized = _normalize_text(value)
    if not normalized:
        return None

    family = PART_TYPE_FAMILY_BY_ID.get(normalized)
    if family:
        return family

    if normalized in PART_SUBCATEGORY_BY_ID:
        return PART_SUBCATEGORY_BY_ID[normalized][0]

    for family in PART_TYPE_FAMILIES:
        if normalized in family.normalized_terms:
            return family
    for family in PART_TYPE_FAMILIES:
        if any(normalized in term or term in normalized for term in family.normalized_terms):
            return family
    return None


def classify_part_type_family(
    category: Optional[str],
    part_type: Optional[str],
    name: Optional[str],
    name_he: Optional[str],
    description: Optional[str],
) -> Optional[PartTypeFamily]:
    accessory_family = PART_TYPE_FAMILY_BY_ID["accessories"]
    service_family = PART_TYPE_FAMILY_BY_ID["service-general"]
    generic_terms = {
        _normalize_text("כללי"),
        _normalize_text("general"),
        _normalize_text("misc"),
        _normalize_text("miscellaneous"),
        _normalize_text("service"),
    }
    normalized_category = _normalize_text(category)
    normalized_part_type = _normalize_text(part_type)
    exact_candidates = [
        normalized_category,
        normalized_part_type,
    ]
    haystack = " | ".join(
        filter(
            None,
            [
                "" if normalized_category in generic_terms else normalized_category,
                normalized_part_type,
                _normalize_text(name),
                _normalize_text(name_he),
                _normalize_text(description),
            ],
        )
    )

    for family in PART_TYPE_FAMILIES:
        normalized_terms = family.normalized_terms
        if any(
            candidate and candidate not in generic_terms and any(candidate == term or candidate in term or term in candidate for term in normalized_terms)
            for candidate in exact_candidates
        ):
            return family

    best_family: Optional[PartTypeFamily] = None
    best_score = 0
    for family in PART_TYPE_FAMILIES:
        score = 0
        for term in family.normalized_terms:
            if term and term in haystack:
                score += max(1, len(term.split()))
        if score > best_score:
            best_family = family
            best_score = score

    if best_family:
        return best_family

    if normalized_category in generic_terms:
        if any(term and term in haystack for term in accessory_family.normalized_terms):
            return accessory_family
        if any(term and term in haystack for term in service_family.normalized_terms):
            return service_family
        # כללי / general / misc with no other context → service-general family
        return service_family
    return None


def classify_part_subcategory(
    category: Optional[str],
    part_type: Optional[str],
    name: Optional[str],
    name_he: Optional[str],
    description: Optional[str],
) -> Optional[Tuple[PartTypeFamily, PartSubcategory]]:
    normalized_category = _normalize_text(category)
    normalized_part_type = _normalize_text(part_type)
    haystack = " | ".join(
        filter(
            None,
            [
                normalized_category,
                normalized_part_type,
                _normalize_text(name),
                _normalize_text(name_he),
                _normalize_text(description),
            ],
        )
    )

    exact_candidates = [normalized_category, normalized_part_type]
    for family in PART_TYPE_FAMILIES:
        for subcategory in family.subcategories:
            normalized_terms = subcategory.normalized_terms
            if any(
                candidate and any(candidate == term or candidate in term or term in candidate for term in normalized_terms)
                for candidate in exact_candidates
            ):
                return family, subcategory

    best_match: Optional[Tuple[PartTypeFamily, PartSubcategory]] = None
    best_score = 0
    for family in PART_TYPE_FAMILIES:
        for subcategory in family.subcategories:
            score = 0
            for term in subcategory.normalized_terms:
                if term and term in haystack:
                    score += max(1, len(term.split()))
            if score > best_score:
                best_match = (family, subcategory)
                best_score = score

    return best_match



def build_part_type_sql_clause(
    category_value: Optional[str],
    params: Dict[str, Any],
    prefix: str = "cat",
) -> Optional[str]:
    family = resolve_part_type_family(category_value)
    if not family:
        return None

    sql_terms: List[str] = []
    for term in family.match_terms:
        clean = term.strip()
        if clean and clean not in sql_terms:
            sql_terms.append(clean)

    text_clauses: List[str] = []
    supplier_part_type_clauses: List[str] = []
    for idx, term in enumerate(sql_terms):
        key = f"{prefix}_{idx}"
        params[key] = f"%{term}%"
        text_clauses.extend(
            [
                f"pc.category ILIKE :{key}",
                f"COALESCE(pc.part_type, '') ILIKE :{key}",
                f"pc.name ILIKE :{key}",
                f"COALESCE(pc.name_he, '') ILIKE :{key}",
                f"COALESCE(pc.description, '') ILIKE :{key}",
            ]
        )
        supplier_part_type_clauses.append(f"sp2.part_type ILIKE :{key}")

    joined_text = " OR ".join(text_clauses)
    joined_supplier = " OR ".join(supplier_part_type_clauses)
    return (
        "("
        f"{joined_text}"
        " OR EXISTS ("
        "     SELECT 1 FROM supplier_parts sp2"
        "     WHERE sp2.part_id = pc.id"
        f"       AND ({joined_supplier})"
        " )"
        ")"
    )


def iter_part_type_families() -> Iterable[PartTypeFamily]:
    return PART_TYPE_FAMILIES


def iter_part_subcategories() -> Iterable[Tuple[PartTypeFamily, PartSubcategory]]:
    for family in PART_TYPE_FAMILIES:
        for subcategory in family.subcategories:
            yield family, subcategory
