"""Manufacturer normalization helpers shared by importers and DB worker."""

from __future__ import annotations

import re
from typing import Optional, TYPE_CHECKING
from sqlalchemy import select, or_, text
if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

PARTS_BRANDS = {
    "bosch",
    "brembo",
    "champion",
    "fram",
    "mann",
    "ngk",
    "valeo",
    "denso",
    "luk",
    "sachs",
    "delphi",
    "mahle",
    "hella",
    "mando",
    "trw",
    "aisin",
}

TRUCK_BRANDS = {
    "man": "MAN",
    "hino": "Hino",
    "scania": "Scania",
    "daf": "DAF",
    "iveco": "Iveco",
    "kenworth": "Kenworth",
    "peterbilt": "Peterbilt",
    "freightliner": "Freightliner",
    "mack": "Mack",
    "western star": "Western Star",
    "volvo trucks": "Volvo Trucks",
    "renault trucks": "Renault Trucks",
    "isuzu trucks": "Isuzu Trucks",
}

CANONICAL_CAR_BY_ALIAS = {
    "renault": "Renault",
    "mercedes": "Mercedes-Benz",
    "mercedes benz": "Mercedes-Benz",
    "chevrolet": "Chevrolet",
    "hyundai": "Hyundai",
    "mitsubishi": "Mitsubishi",
    "genesis": "Genesis",
    "gen": "Genesis",
    "suzuki": "Suzuki",
    "porsche": "Porsche",
    "smart": "Smart",
    "ora": "ORA",
    "jaecoo": "Jaecoo",
    "citroen": "Citroen",
    "citroen france": "Citroen",
    "peugeot": "Peugeot",
    "peugeot france": "Peugeot",
    "toyota": "Toyota",
    "mazda": "Mazda",
    "honda": "Honda",
    "nissan": "Nissan",
    "kia": "Kia",
    "קיה": "Kia",
    "קיה קוריאה": "Kia",
    "קיא קוריאה": "Kia",
    "gmc": "GMC",
    "mg": "MG",
    "ג'.מ": "GMC",
    "ג'מ": "GMC",
    "ג.מ": "GMC",
    "מ.ג": "MG",
    "מג": "MG",
    "volkswagen": "Volkswagen",
    "פולקסווגן": "Volkswagen",
    "audi": "Audi",
    "אאודי": "Audi",
    "bmw": "BMW",
    "ב מ וו": "BMW",
    "skoda": "Skoda",
    "סקודה": "Skoda",
    "פיג ו": "Peugeot",
    "פיג'ו": "Peugeot",
    "mercedes-benz": "Mercedes-Benz",
    "mereceds": "Mercedes-Benz",
    "merceds": "Mercedes-Benz",
    "מרצדס": "Mercedes-Benz",
    "מרצדס חלפים": "Mercedes-Benz",
    "מרצדס בנץ גרמנ": "Mercedes-Benz",
    "יונדאי": "Hyundai",
    "מיצובישי": "Mitsubishi",
    "ג נסיס": "Genesis",
    "ג'נסיס": "Genesis",
    "סוזוקי": "Suzuki",
    "סמארט": "Smart",
    "סיטרואן": "Citroen",
    "סיטרואן ספרד": "Citroen",
    "רנו טורקיה": "Renault",
    "טויוטה": "Toyota",
    "טויוטה אנגליה": "Toyota",
    "טויוטה יפן": "Toyota",
    "טויוטה צרפת": "Toyota",
    "הונדה": "Honda",
    "מותג": "",
}


# OEM number prefixes used by Israeli importers → canonical manufacturer
# These prefixes appear at the start of oem_number in parts_catalog
OEM_PREFIX_TO_MANUFACTURER: dict[str, str] = {
    "NI": "Nissan",
    "NF": "Nissan",
    "NV": "Nissan",
    "CH": "Chery",
    "XP": "Xpeng",
    "HO": "Honda",
    "JM": "JAC",
    "PO": "Polaris",
    "MR": "Mitsubishi",
    "MN": "Mitsubishi",
    "MD": "Mitsubishi",
    "MB": "Mitsubishi",
    "RE": "Renault",
    "YQ": "Citroen",
    "FQ": "Jaecoo",
    "IL": "Hyundai",
}


VEHICLE_MODEL_BY_MANUFACTURER_ALIAS = {
    ("citroen", "partner"): "BERLINGO",
    ("peugeot", "berlingo"): "PARTNER",
}


VEHICLE_MODEL_NORMALIZATION_ALIASES = {
    ("chevrolet", "camro"): "CAMARO",
    ("chevrolet", "cmro"): "CAMARO",
    ("chevrolet", "chevcmro"): "CAMARO",
    ("chevrolet", "chevroletcamaro"): "CAMARO",
    ("chevrolet", "chevroletcamaro62lil"): "CAMARO 6.2L IL",
    ("chevrolet", "capris"): "CAPRICE",
    ("chevrolet", "cruse"): "CRUZE",
    ("chevrolet", "cruz"): "CRUZE",
    ("chevrolet", "corvet"): "CORVETTE",
    ("chevrolet", "chevcorvet"): "CORVETTE",
    ("chevrolet", "chevcorvette"): "CORVETTE",
    ("chevrolet", "chevcorsica"): "CORSICA",
    ("chevrolet", "chevcorcica"): "CORSICA",
    ("chevrolet", "corcica"): "CORSICA",
    ("chevrolet", "cosicabereta"): "CORSICA & BERETTA",
    ("chevrolet", "chevlumina"): "LUMINA",
    ("chevrolet", "chevlumini"): "LUMINA",
    ("chevrolet", "chevmalibu"): "MALIBU",
    ("chevrolet", "trailblaizer"): "TRAIL BLAZER",
    ("chevrolet", "trailblazer"): "TRAIL BLAZER",
    ("chevrolet", "taho"): "TAHOE",
    ("chevrolet", "chevrolettaho"): "TAHOE",
    ("chevrolet", "chevrolettahoe"): "TAHOE",
    ("chevrolet", "chevroletblazer"): "BLAZER",
    ("chevrolet", "chevroletblazer20l"): "BLAZER 2.0L",
    ("chevrolet", "lumini"): "LUMINA",
    ("chevrolet", "silverdo"): "SILVERADO",
    ("chevrolet", "pukcip"): "PICK UP",
    ("chevrolet", "savan"): "SAVANA",
    ("chevrolet", "chevcavalier"): "CAVALIER",
    ("chevrolet", "cavalir"): "CAVALIER",
    ("chevrolet", "cavalircoupe"): "CAVALIER COUPE",
    ("chevrolet", "chevcavalir"): "CAVALIER",
    ("chevrolet", "chevcavalircoupe"): "CAVALIER",
    ("citroen", "c4acc"): "C4",
    ("citroen", "c4cactusacc"): "C4 Cactus",
    ("citroen", "c4b7acc"): "C4(B7)",
    ("citroen", "c3aircrossacc"): "C3 AIRCROSS",
    ("citroen", "c4pic"): "C4 Picasso",
    ("citroen", "visasitroen"): "VISA",
    ("peugeot", "partneracc"): "PARTNER",
    ("peugeot", "30085008acc"): "3008-5008",
}


MANUFACTURER_MODEL_PREFIXES = {
    "chevrolet": ("CHEVROLET", "CHEV", "NEW"),
    "citroen": ("CITROEN", "NEW"),
    "peugeot": ("PEUGEOT", "NEW"),
}


MODEL_JUNK_TOKENS = {
    "מק\"ט",
    "מקט",
    "sku",
    "oem",
    "part",
    "parts",
    "model",
    "unknown",
    "accessories",
    "accessory",
    "acc",
    "general",
    "אביזרים כללי",
    "כללי",
    "כל הדגמים",
    "כלי עבודה",
    "כלים יעודיים",
    "מוצרים",
    "מנועים מחודשים",
    "צבעים כללי",
}


SUBMODEL_JUNK_TOKENS = {
    "IL",
    "US",
    "EU",
    "JP",
    "UK",
    "OL",
}


def _model_alias_key(value: str) -> str:
    return re.sub(r"[^a-z0-9\u0590-\u05ff]+", "", (value or "").lower())


def norm(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    v = re.sub(r"[^\w\u0590-\u05FF]+", " ", v)
    v = re.sub(r"\b(parts?|spare\s*parts?)\b", "", v)
    v = re.sub(r"\bחלפים\b", "", v)
    return re.sub(r"\s+", " ", v).strip()


def normalize_manufacturer_name(raw: Optional[str], fallback: Optional[str] = None) -> str:
    raw_clean = (raw or "").strip()
    fb = (fallback or "").strip()
    n = norm(raw_clean or fb)

    if not n:
        return fb or raw_clean

    if n in PARTS_BRANDS:
        return fb or raw_clean

    if n in TRUCK_BRANDS:
        return TRUCK_BRANDS[n]

    canonical = CANONICAL_CAR_BY_ALIAS.get(raw_clean) or CANONICAL_CAR_BY_ALIAS.get(n)
    if canonical is not None:
        return canonical

    return raw_clean or fb


def normalize_oem_manufacturer(oem_number: str, current_manufacturer: str) -> str:
    """
    Given an OEM number and its current manufacturer label,
    return the correct canonical manufacturer based on known prefixes.
    Returns current_manufacturer unchanged if no prefix match found.
    """
    if not oem_number:
        return current_manufacturer

    prefix = oem_number[:2].upper()
    correct = OEM_PREFIX_TO_MANUFACTURER.get(prefix)

    if correct and correct != current_manufacturer:
        return correct

    return current_manufacturer


def normalize_vehicle_model_name(raw: Optional[str]) -> str:
    """Normalize vehicle model text extracted from catalog/import sources.

    Returns an empty string when the value looks like a SKU/category/noise token.
    """
    v = (raw or "").strip()
    if not v:
        return ""

    # Remove appended year/range suffixes.
    v = re.sub(r"\s*(?:19|20)\d{2}(?=[^\d]|$).*$", "", v).strip()
    v = re.sub(r"\s+\d[\d\-/\.]*\s*$", "", v).strip()
    v = re.sub(r"\s+(?:US|EU|IL|JP|UK|OL)-?\s*$", "", v, flags=re.IGNORECASE).strip()
    v = re.sub(r"\b(?:accessories|accessory|new|basic)\b", "", v, flags=re.IGNORECASE).strip()
    v = re.sub(r"\s{2,}", " ", v).strip()
    if not v:
        return ""

    low = v.lower()
    if low in MODEL_JUNK_TOKENS:
        return ""
    if "כללי" in v or "אביזרים" in v or "כלים" in v:
        return ""

    # Keep only values with letters (Hebrew/Latin).
    if not re.search(r"[A-Za-z\u0590-\u05FF]", v):
        return ""

    # Reject sku-like ids (e.g. YQ009701XT, 9846913380) only when they are
    # a single compact token. Multi-token values like "BERLINGO B9" are valid.
    compact = v.replace(" ", "").replace("-", "")
    if (
        " " not in v
        and "-" not in v
        and len(compact) >= 7
        and re.fullmatch(r"[A-Z0-9]+", compact)
        and re.search(r"\d", compact)
    ):
        return ""

    return v

async def resolve_brand_id(manufacturer_name: str, db: AsyncSession) -> Optional[object]:
    """Resolve a manufacturer name to its primary ID from car_brands or truck_brands.
    This is required to satisfy NOT NULL constraints on the vehicles table.
    """
    from BACKEND_DATABASE_MODELS import CarBrand, TruckBrand

    if not manufacturer_name or not manufacturer_name.strip():
        # Ultimate fallback: return ID of first brand to avoid constraint crashes
        res = await db.execute(select(CarBrand.id).limit(1))
        return res.scalar_one_or_none()

    mfr = manufacturer_name.strip()

    # 1. Exact or alias match
    for BrandModel in (CarBrand, TruckBrand):
        stmt = select(BrandModel.id).where(
            or_(
                BrandModel.name.ilike(mfr),
                BrandModel.name_he.ilike(mfr),
                text(f"(:val)::text = ANY({BrandModel.__tablename__}.aliases)")
            )
        ).params(val=mfr).limit(1)
        res = await db.execute(stmt)
        mfr_id = res.scalar_one_or_none()
        if mfr_id:
            return mfr_id

    # 2. Fuzzy substring match
    for BrandModel in (CarBrand, TruckBrand):
        stmt = select(BrandModel.id).where(
            or_(
                BrandModel.name.ilike(f"%{mfr}%"),
                BrandModel.name_he.ilike(f"%{mfr}%")
            )
        ).limit(1)
        res = await db.execute(stmt)
        mfr_id = res.scalar_one_or_none()
        if mfr_id:
            return mfr_id

    # 3. Last resort: Return first available brand ID
    res = await db.execute(select(CarBrand.id).limit(1))
    return res.scalar_one_or_none()


def canonicalize_vehicle_model_for_manufacturer(manufacturer: Optional[str], model: Optional[str]) -> str:
    canonical_model = normalize_vehicle_model_name(model)
    if not canonical_model:
        return ""

    canonical_manufacturer = normalize_manufacturer_name(manufacturer, manufacturer).casefold()
    for prefix in MANUFACTURER_MODEL_PREFIXES.get(canonical_manufacturer, ("NEW",)):
        prefix_token = f"{prefix} "
        if canonical_model.upper().startswith(prefix_token):
            candidate = canonical_model[len(prefix_token):].strip()
            if candidate:
                canonical_model = candidate
                break

    normalized_alias = VEHICLE_MODEL_NORMALIZATION_ALIASES.get(
        (canonical_manufacturer, _model_alias_key(canonical_model))
    )
    if normalized_alias:
        canonical_model = normalized_alias

    override = VEHICLE_MODEL_BY_MANUFACTURER_ALIAS.get((canonical_manufacturer, canonical_model.casefold()))
    return override or canonical_model


def normalize_vehicle_submodel_name(raw: Optional[str]) -> str:
    """Normalize sub-model/trim names for UI hierarchy display.

    More strict than model normalization: hides internal trim codes such as B618.
    """
    raw_text = (raw or "").strip()
    if raw_text:
        raw_text = re.sub(r"\s+", " ", raw_text).strip()
        qualifier_match = re.fullmatch(
            r"(?P<platform>[A-Z]\d{1,3})\s+(?P<qualifier>[A-Z]{2,8})",
            raw_text,
            flags=re.IGNORECASE,
        )
        if qualifier_match:
            platform = qualifier_match.group("platform").upper()
            qualifier = qualifier_match.group("qualifier").upper()
            if qualifier == "ACC":
                return f"{platform} {qualifier}"

    v = normalize_vehicle_model_name(raw)
    if not v:
        return ""

    if v.upper() in SUBMODEL_JUNK_TOKENS:
        return ""
    if re.fullmatch(r"\d+(?:\.\d+)?\s*L(?:\s+(?:IL|US|EU|JP|UK|OL))?", v, flags=re.IGNORECASE):
        return ""

    # Hide pure internal code-like trims (e.g., B618, 1CB6).
    compact = v.replace(" ", "").replace("-", "")
    if re.fullmatch(r"[A-Z]{2,8}-", v.upper()):
        return ""
    if re.fullmatch(r"[A-Z]?\d{3,}[A-Z0-9]*", compact):
        return ""
    if re.fullmatch(r"[A-Z]{1,3}\d{2,}", compact):
        return ""

    return v
