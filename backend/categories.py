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


def guess_category_by_text(text: str) -> str | None:
    if not text:
        return None

    blob_raw = text.strip().casefold()
    blob_norm = re.sub(r"[^\w\u0590-\u05FF]+", " ", blob_raw)
    blob_norm = re.sub(r"\s+", " ", blob_norm).strip()
    blob_compact = blob_norm.replace(" ", "")

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
