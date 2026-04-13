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

    @property
    def match_terms(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys((self.label, self.id, *self.aliases, *self.legacy_categories, *self.keywords)))

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
        }


PART_TYPE_FAMILIES: Tuple[PartTypeFamily, ...] = (
    PartTypeFamily(
        id="filters",
        label="פילטרים",
        group_id="maintenance",
        group_label="טיפול ותחזוקה",
        badge="FLT",
        icon_key="filter",
        palette=("#d97706", "#f59e0b"),
        aliases=("filters", "filter", "מסננים"),
        keywords=("oil filter", "air filter", "cabin filter", "fuel filter", "filter", "מסנן", "פילטר"),
    ),
    PartTypeFamily(
        id="fluids",
        label="שמנים ונוזלים",
        group_id="maintenance",
        group_label="טיפול ותחזוקה",
        badge="FLD",
        icon_key="fluid",
        palette=("#0284c7", "#38bdf8"),
        aliases=("fluids", "lubricants", "oil", "liquids"),
        keywords=("oil", "coolant", "antifreeze", "atf", "brake fluid", "washer fluid", "שמן", "נוזל", "קירור"),
    ),
    PartTypeFamily(
        id="belts-chains",
        label="רצועות ושרשראות",
        group_id="maintenance",
        group_label="טיפול ותחזוקה",
        badge="BLT",
        icon_key="belt",
        palette=("#7c3aed", "#a78bfa"),
        aliases=("belts", "chains", "timing", "timing kit"),
        legacy_categories=("שרשראות ורצועות",),
        keywords=("belt", "chain", "timing", "idler", "tensioner", "רצועה", "שרשרת", "מותחן"),
    ),
    PartTypeFamily(
        id="service-general",
        label="טיפול וכללי",
        group_id="maintenance",
        group_label="טיפול ותחזוקה",
        badge="GEN",
        icon_key="service",
        palette=("#475569", "#94a3b8"),
        aliases=("general", "service", "maintenance", "misc"),
        legacy_categories=("כללי",),
        keywords=("service", "maintenance", "repair kit", "kit", "general", "כללי", "ערכת תיקון"),
    ),
    PartTypeFamily(
        id="engine",
        label="מנוע",
        group_id="powertrain",
        group_label="מנוע והנעה",
        badge="ENG",
        icon_key="engine",
        palette=("#dc2626", "#fb7185"),
        aliases=("engine", "motor"),
        legacy_categories=("מנוע",),
        keywords=("engine", "motor", "gasket", "valve", "camshaft", "piston", "אטם", "שסתום", "בוכנה"),
    ),
    PartTypeFamily(
        id="cooling",
        label="קירור מנוע",
        group_id="powertrain",
        group_label="מנוע והנעה",
        badge="CLG",
        icon_key="cooling",
        palette=("#0f766e", "#2dd4bf"),
        aliases=("cooling", "radiator", "thermostat", "water pump"),
        keywords=("radiator", "thermostat", "water pump", "cooling", "fan", "רדיאטור", "תרמוסטט", "משאבת מים", "מאוורר"),
    ),
    PartTypeFamily(
        id="fuel-air",
        label="דלק ויניקה",
        group_id="powertrain",
        group_label="מנוע והנעה",
        badge="FUEL",
        icon_key="fuel",
        palette=("#2563eb", "#60a5fa"),
        aliases=("fuel", "intake", "injection", "fuel system"),
        legacy_categories=("דלק",),
        keywords=("fuel", "injector", "pump", "intake", "throttle", "turbo hose", "דלק", "מזרק", "משאבה", "יניקה", "מצערת"),
    ),
    PartTypeFamily(
        id="exhaust",
        label="פליטה ו-EGR",
        group_id="powertrain",
        group_label="מנוע והנעה",
        badge="EXH",
        icon_key="exhaust",
        palette=("#7c2d12", "#fb923c"),
        aliases=("exhaust", "emissions", "egr", "dpf"),
        keywords=("exhaust", "muffler", "catalytic", "lambda", "egr", "dpf", "פליטה", "אגזוז", "קטליטי", "חיישן חמצן"),
    ),
    PartTypeFamily(
        id="clutch-drivetrain",
        label="קלאץ' והנעה",
        group_id="powertrain",
        group_label="מנוע והנעה",
        badge="DRV",
        icon_key="drivetrain",
        palette=("#9333ea", "#c084fc"),
        aliases=("clutch", "driveshaft", "cv joint", "axle"),
        keywords=("clutch", "flywheel", "axle", "drive shaft", "cv joint", "קלאץ", "גל הינע", "ציריה", "פעמון"),
    ),
    PartTypeFamily(
        id="gearbox",
        label="גיר ותמסורת",
        group_id="powertrain",
        group_label="מנוע והנעה",
        badge="GBX",
        icon_key="gearbox",
        palette=("#4f46e5", "#818cf8"),
        aliases=("gearbox", "transmission", "gear", "differential"),
        keywords=("gearbox", "transmission", "gear", "differential", "seal kit", "גיר", "תמסורת", "דיפרנציאל"),
    ),
    PartTypeFamily(
        id="brakes",
        label="בלמים",
        group_id="chassis",
        group_label="שלדה ובטיחות",
        badge="BRK",
        icon_key="brake",
        palette=("#b91c1c", "#f87171"),
        aliases=("brakes", "brake"),
        legacy_categories=("בלמים",),
        keywords=("brake", "disc", "rotor", "pad", "caliper", "בלם", "דיסק", "רפידה", "קאליפר"),
    ),
    PartTypeFamily(
        id="suspension-steering",
        label="מתלה והיגוי",
        group_id="chassis",
        group_label="שלדה ובטיחות",
        badge="SUS",
        icon_key="suspension",
        palette=("#0f766e", "#34d399"),
        aliases=("suspension", "steering", "chassis"),
        legacy_categories=("מתלה", "היגוי"),
        keywords=("suspension", "steering", "shock", "strut", "arm", "tie rod", "rack", "בולם", "תפוח", "זרוע", "מסרק", "הגה"),
    ),
    PartTypeFamily(
        id="wheels-bearings",
        label="גלגלים ומיסבים",
        group_id="chassis",
        group_label="שלדה ובטיחות",
        badge="WHL",
        icon_key="wheel",
        palette=("#374151", "#9ca3af"),
        aliases=("wheels", "tires", "wheel bearing", "hub"),
        legacy_categories=("גלגלים וצמיגים",),
        keywords=("wheel", "tire", "tyre", "rim", "hub", "bearing", "גלגל", "צמיג", "ג'נט", "מיסב"),
    ),
    PartTypeFamily(
        id="body-exterior",
        label="מרכב וחוץ",
        group_id="body",
        group_label="מרכב ונוחות",
        badge="BDY",
        icon_key="body",
        palette=("#0891b2", "#67e8f9"),
        aliases=("body", "bodywork", "exterior", "bumper"),
        legacy_categories=("פחיין ומרכב",),
        keywords=("body", "bumper", "grille", "door", "hood", "mirror", "fender", "מרכב", "פגוש", "גריל", "מראה", "כנף"),
    ),
    PartTypeFamily(
        id="lighting",
        label="תאורה",
        group_id="body",
        group_label="מרכב ונוחות",
        badge="LGT",
        icon_key="lighting",
        palette=("#ca8a04", "#fde047"),
        aliases=("lighting", "lights", "lamp", "headlight"),
        legacy_categories=("תאורה",),
        keywords=("light", "lamp", "headlight", "tail light", "fog", "bulb", "תאורה", "פנס", "נורה"),
    ),
    PartTypeFamily(
        id="electrical-sensors",
        label="חשמל וחיישנים",
        group_id="body",
        group_label="מרכב ונוחות",
        badge="ELE",
        icon_key="electrical",
        palette=("#1d4ed8", "#93c5fd"),
        aliases=("electrical", "electronics", "sensors", "wiring"),
        legacy_categories=("חשמל רכב",),
        keywords=("sensor", "switch", "starter", "alternator", "ignition", "relay", "module", "חשמל", "חיישן", "אלטרנטור", "סטרטר", "ממסר"),
    ),
    PartTypeFamily(
        id="air-conditioning-heating",
        label="מיזוג וחימום",
        group_id="body",
        group_label="מרכב ונוחות",
        badge="AC",
        icon_key="climate",
        palette=("#0369a1", "#7dd3fc"),
        aliases=("air conditioning", "ac", "hvac", "heating"),
        legacy_categories=("מיזוג",),
        keywords=("ac", "a/c", "compressor", "condenser", "blower", "heater", "climate", "מזגן", "מדחס", "מעבה", "חימום"),
    ),
    PartTypeFamily(
        id="wipers-washers",
        label="מגבים וניקוי שמשות",
        group_id="body",
        group_label="מרכב ונוחות",
        badge="WPR",
        icon_key="wiper",
        palette=("#0f766e", "#99f6e4"),
        aliases=("wipers", "washer", "windscreen"),
        legacy_categories=("מגבים",),
        keywords=("wiper", "washer", "windscreen", "מגב", "מתז", "זרוע מגב"),
    ),
    PartTypeFamily(
        id="interior-comfort",
        label="פנים ונוחות",
        group_id="body",
        group_label="מרכב ונוחות",
        badge="INT",
        icon_key="interior",
        palette=("#7c3aed", "#ddd6fe"),
        aliases=("interior", "comfort", "seat", "trim"),
        legacy_categories=("ריפוד ופנים",),
        keywords=("interior", "seat", "dashboard", "trim", "window switch", "ריפוד", "מושב", "דשבורד", "פנים"),
    ),
    PartTypeFamily(
        id="accessories",
        label="אביזרים וכללי",
        group_id="accessories",
        group_label="אביזרים וכללי",
        badge="ACC",
        icon_key="accessories",
        palette=("#475569", "#cbd5e1"),
        aliases=("accessories", "general", "care", "tools"),
        legacy_categories=("כללי",),
        keywords=("accessory", "cover", "mat", "cleaner", "tool", "אביזר", "כיסוי", "שטיח", "ניקוי"),
    ),
)


PART_TYPE_FAMILY_BY_ID: Dict[str, PartTypeFamily] = {family.id: family for family in PART_TYPE_FAMILIES}


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

    for candidate in PART_TYPE_FAMILIES:
        if normalized in candidate.normalized_terms:
            return candidate
    for candidate in PART_TYPE_FAMILIES:
        if any(normalized in term or term in normalized for term in candidate.normalized_terms):
            return candidate
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
        return None
    return None


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