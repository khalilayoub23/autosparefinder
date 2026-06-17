from __future__ import annotations

import re
from typing import Dict, List, Tuple

from part_type_taxonomy import iter_part_subcategories, iter_part_type_families


def _is_hebrew_word(value: str) -> bool:
    return any('\u0590' <= c <= '\u05ff' for c in value)


def _normalize_phrase(value: str) -> str:
    cleaned = re.sub(r"[^\w\u0590-\u05FF]+", " ", (value or "").casefold())
    return re.sub(r"\s+", " ", cleaned).strip()


def _split_lang_terms(terms: List[str]) -> Tuple[List[str], List[str]]:
    he_terms: List[str] = []
    en_terms: List[str] = []
    for term in terms:
        norm = _normalize_phrase(term)
        if not norm:
            continue
        if _is_hebrew_word(norm):
            he_terms.append(norm)
        else:
            en_terms.append(norm)
    return sorted(set(he_terms)), sorted(set(en_terms))


def _collect_keyword_terms() -> Tuple[List[Tuple[str, List[str]]], Dict[str, Dict[str, List[str]]]]:
    # Ordered entries are (canonical family id, searchable terms).
    ordered: List[Tuple[str, List[str]]] = []
    category_map: Dict[str, Dict[str, List[str]]] = {}

    for family, subcategory in iter_part_subcategories():
        terms = list(subcategory.match_terms) + list(family.keywords)
        he_terms, en_terms = _split_lang_terms(terms)
        merged = he_terms + en_terms
        if not merged:
            continue
        ordered.append((family.id, merged))
        category_map[subcategory.label] = {"he": he_terms, "en": en_terms}

    for family in iter_part_type_families():
        terms = list(family.match_terms)
        he_terms, en_terms = _split_lang_terms(terms)
        merged = he_terms + en_terms
        if not merged:
            continue
        ordered.append((family.id, merged))
        category_map[family.label] = {"he": he_terms, "en": en_terms}

    return ordered, category_map


ORDERED_CATEGORY_TERMS, CATEGORY_MAP = _collect_keyword_terms()

# Supplemental Hebrew keyword dictionary — maps partial Hebrew strings to category IDs.
# Keys are lowercased Hebrew substrings; first match wins (ordered by specificity: longer first).
_HEBREW_SUPPLEMENT: List[Tuple[str, str]] = [
    # body-exterior
    ("בולם הגה", "suspension-steering"),
    ("מוט קישור", "suspension-steering"),
    ("זרוע קדמית", "suspension-steering"),
    ("זרוע אחורית", "suspension-steering"),
    ("כדורית היגוי", "suspension-steering"),
    ("מסבב גלגל", "wheels-bearings"),
    ("מגן בוץ", "body-exterior"),
    ("פגוש קדמי", "body-exterior"),
    ("פגוש אחורי", "body-exterior"),
    ("כנף קדמית", "body-exterior"),
    ("כנף אחורית", "body-exterior"),
    ("מכסה מנוע", "body-exterior"),
    ("ידית דלת", "body-exterior"),
    ("מנעול דלת", "body-exterior"),
    ("קורה תחתונה", "body-exterior"),
    ("קורה עליונה", "body-exterior"),
    ("ספוילר אחורי", "body-exterior"),
    ("ספוילר קדמי", "body-exterior"),
    ("גריל קדמי", "body-exterior"),
    ("פס קישוט", "body-exterior"),
    ("פינה לפגוש", "body-exterior"),
    ("רשת נוי", "body-exterior"),
    ("מגן סורג", "body-exterior"),
    ("מגן רוח", "body-exterior"),
    ("חלון גג", "body-exterior"),
    ("זכוכית חלון", "body-exterior"),
    ("עמוד אמצעי", "body-exterior"),
    ("תושבת פגוש", "body-exterior"),
    ("כיסוי פגוש", "body-exterior"),
    ("כיסוי מנוע", "body-exterior"),
    ("כסא נהג", "interior-comfort"),
    ("כסא נוסע", "interior-comfort"),
    ("ריפוד כסא", "interior-comfort"),
    ("ריפוד תקרה", "interior-comfort"),
    ("ריפוד דלת", "interior-comfort"),
    ("שטיח ריצפה", "interior-comfort"),
    ("שטיח תא מטען", "interior-comfort"),
    ("פנל ידית הילוכים", "interior-comfort"),
    ("ידית הילוכים", "interior-comfort"),
    ("מסוף קונסולה", "interior-comfort"),
    ("כפתור חלון", "interior-comfort"),
    ("וילון גלילה", "interior-comfort"),
    ("כיסוי דוושה", "interior-comfort"),
    ("משאבת דלק", "fuel-air"),
    ("מזרק דלק", "fuel-air"),
    ("מסנן דלק", "filters"),
    ("מצנן ביניים", "fuel-air"),
    ("תושבת מנוע", "engine"),
    ("מבודד תושבת מנוע", "engine"),
    ("אטם ראש מנוע", "engine"),
    ("מכסה שסתומים", "engine"),
    ("גל ארכובה", "engine"),
    ("שרשרת תיזמון", "belts-chains"),
    ("מותח שרשרת", "belts-chains"),
    ("רצועת תזמון", "belts-chains"),
    ("קומפרסור מזגן", "air-conditioning-heating"),
    ("אידיי מזגן", "air-conditioning-heating"),
    ("רדיאטור מזגן", "air-conditioning-heating"),
    ("בורג לקומפרסור", "air-conditioning-heating"),
    ("נורת ערפל", "lighting"),
    ("פנס ערפל", "lighting"),
    ("פנס קדמי", "lighting"),
    ("פנס אחורי", "lighting"),
    ("וישר אחורי", "wipers-washers"),
    ("מגב אחורי", "wipers-washers"),
    ("מגב קדמי", "wipers-washers"),
    ("חיישן חניה", "electrical-sensors"),
    ("חיישן מהירות", "electrical-sensors"),
    ("חיישן לחץ", "electrical-sensors"),
    ("חיישן טמפרטורה", "electrical-sensors"),
    ("מנוע חשמלי", "electrical-sensors"),
    ("מפסק אורות", "electrical-sensors"),
    ("מצבר", "electrical-sensors"),
    ("אלטרנטור", "electrical-sensors"),
    ("מנוע מצמד", "electrical-sensors"),
    ("טבעת לפיסטון", "engine"),
    ("ציר קדמי", "clutch-drivetrain"),
    ("ציר אחורי", "clutch-drivetrain"),
    ("גל הינע", "clutch-drivetrain"),
    ("גיר אוטומטי", "gearbox"),
    ("גיר ידני", "gearbox"),
    ("קופסת גיר", "gearbox"),
    ("בית גלגל", "wheels-bearings"),
    ("ספייסר", "wheels-bearings"),
    ("מיסב גלגל", "wheels-bearings"),
    ("בולם זעזועים", "suspension-steering"),
    ("קפיץ", "suspension-steering"),
    ("מוט", "suspension-steering"),
    ("זרוע", "suspension-steering"),
    ("כדורית", "suspension-steering"),
    ("מסנן אוויר", "filters"),
    ("מסנן שמן", "filters"),
    ("פילטר אוויר", "filters"),
    ("פילטר שמן", "filters"),
    ("פילטר דלק", "filters"),
    ("רדיאטור", "cooling"),
    ("תרמוסטט", "cooling"),
    ("משאבת מים", "cooling"),
    ("מפוח", "cooling"),
    ("בלם יד", "brakes"),
    ("רפידות בלמים", "brakes"),
    ("דיסק בלמים", "brakes"),
    ("צינור בלם", "brakes"),
    ("קליפר בלם", "brakes"),
    ("סמל יצרן", "body-exterior"),
    ("מגש אחורי", "interior-comfort"),
    ("עמוד הגה", "suspension-steering"),
    ("גשר אחורי", "suspension-steering"),
    ("משולש תחתון", "suspension-steering"),
    ("Air Bellows", "suspension-steering"),
    # generic body parts
    ("גריל", "body-exterior"),
    ("פגוש", "body-exterior"),
    ("כנף", "body-exterior"),
    ("ספוילר", "body-exterior"),
    ("מנעול", "body-exterior"),
    ("ידית", "body-exterior"),
    ("פח", "body-exterior"),
    ("סף", "body-exterior"),
    ("כיסוי", "body-exterior"),
    # generic interior
    ("כסא", "interior-comfort"),
    ("שטיח", "interior-comfort"),
    ("ריפוד", "interior-comfort"),
    ("פנל", "interior-comfort"),
    ("כפתור", "interior-comfort"),
    # generic suspension
    ("בולם", "suspension-steering"),
    ("היגוי", "suspension-steering"),
    # generic lighting
    ("פנס", "lighting"),
    ("נורה", "lighting"),
    # generic electrical
    ("חיישן", "electrical-sensors"),
    ("מפסק", "electrical-sensors"),
    # generic engine
    ("שסתום", "engine"),
    ("בוכנה", "engine"),
    ("אטם", "engine"),
    # generic fuel
    ("מזרק", "fuel-air"),
    ("טורבו", "fuel-air"),
    # generic belts
    ("רצועה", "belts-chains"),
    ("שרשרת", "belts-chains"),
    # generic exhaust
    ("אגזוז", "exhaust"),
    ("קטליטי", "exhaust"),
    # generic cooling
    ("קירור", "cooling"),
    # generic gearbox
    ("גיר", "gearbox"),
    ("תמסורת", "gearbox"),
    # generic drivetrain
    ("מצמד", "clutch-drivetrain"),
    ("קלאץ", "clutch-drivetrain"),
    ("ציריה", "clutch-drivetrain"),
    # generic AC
    ("מזגן", "air-conditioning-heating"),
    ("קומפרסור", "air-conditioning-heating"),
    # generic wipers
    ("מגב", "wipers-washers"),
    ("וישר", "wipers-washers"),
    # generic wheels
    ("גלגל", "wheels-bearings"),
    ("מיסב", "wheels-bearings"),
    # generic brakes
    ("בלם", "brakes"),
    ("רפידה", "brakes"),
    # generic filters
    ("מסנן", "filters"),
    ("פילטר", "filters"),
]


def guess_category_by_text(text: str) -> str | None:
    if not text:
        return None

    blob_raw = text.strip().casefold()
    blob_norm = re.sub(r"[^\w\u0590-\u05FF]+", " ", blob_raw)
    blob_norm = re.sub(r"\s+", " ", blob_norm).strip()
    blob_compact = blob_norm.replace(" ", "")

    # Check supplemental Hebrew dictionary first (longer phrases take priority)
    for phrase, category in _HEBREW_SUPPLEMENT:
        phrase_norm = re.sub(r"[^\w\u0590-\u05FF]+", " ", phrase.casefold()).strip()
        if phrase_norm and phrase_norm in blob_norm:
            return category

    for category, keywords in ORDERED_CATEGORY_TERMS:
        for keyword in keywords:
            kw_norm = _normalize_phrase(keyword)
            if not kw_norm:
                continue
            kw_compact = kw_norm.replace(" ", "")

            tokens = [t for t in kw_norm.split() if t]
            if len(tokens) > 1:
                stems = []
                for token in tokens:
                    if len(token) >= 4:
                        stems.append(token[:3] if _is_hebrew_word(token) else token[:5])
                    else:
                        stems.append(token)
                if stems and all(stem in blob_norm or stem in blob_compact for stem in stems):
                    return category
                continue

            lookup = kw_compact or kw_norm
            if not lookup:
                continue

            if len(lookup) >= 4:
                stem = lookup[:3] if _is_hebrew_word(lookup) else lookup[:5]
                if stem in blob_norm or stem in blob_compact:
                    return category
            else:
                if lookup in blob_norm or lookup in blob_compact:
                    return category

    return None


__all__ = ['CATEGORY_MAP', 'guess_category_by_text']
