"""
Script: variant_identity.py
Purpose: ONE authoritative rule for the VARIANT of a supplier offer (supplier_parts.part_type): how a free-text
         label (Hebrew / English / importer spelling) becomes (a) the value we STORE and (b) the identity KEY the
         database uses for uniqueness. Every writer, normalizer and comparer calls this module; nothing else may keep
         a label dictionary (offer_classification / offer_policy / db_update_agent / restore script / importers import it).
Process:
  resolve_text(text)        -> same result for a short free-text type FIELD that contains a label (SNG type names).
  resolve(label)            -> VariantResolution(kind, source_label, stored, key, reason). Never raises, never guesses.
  canonical_part_type(label)-> stored value, None when the label is MISSING (blank / placeholder);
                               raises UnknownVariantLabel / AmbiguousVariantLabel (fail closed) otherwise.
  variant_key(label)        -> identity key that is IDENTICAL to the production index expression
                               uq_sp_part_supplier_variant:  CASE WHEN lower(part_type) IN ('original','genuine','oem','oe')
                               THEN 'oem' ELSE lower(part_type) END   (None = untyped = uq_sp_part_supplier_untyped).
  index_key(stored)         -> the same expression applied to an already-stored value (Python twin of the SQL).
  INDEX_KEY_SQL(col)        -> the SQL text of that expression (used by the collision checks; tests compare it to the
                               migration file and to pg_get_indexdef so code and index can never disagree).
Vocabulary (stored value -> index key = product class):
  original -> oem | oem -> oem | oe_equivalent -> oe_equivalent | aftermarket -> aftermarket
  remanufactured -> remanufactured | used -> used.   `original` and `oem` stay two STORED spellings (no rewrite of
  ~290k existing rows) that are ONE identity (same index key): a supplier cannot hold both for one part.
Distinctness (never collapsed): oem vs oe_equivalent vs aftermarket vs remanufactured vs used. `replacement` and
  `compatible` are NOT aftermarket synonyms here (AMBIGUOUS: need an owner decision), `new` is a condition not a variant.
Missing vs unknown vs ambiguous:
  missing   blank / None / placeholder ('unknown', 'none', 'null', 'n/a', 'סוג מוצר' column header) -> stored None
  unknown   a label this module has never been told about -> typed error; the caller skips + reports, never defaults
  ambiguous two different variants in one label ('מקורי/חליפי'), or a label needing an owner decision -> typed error
Provenance: supplier_parts has NO column/JSONB for the source label. `VariantResolution.source_label` carries it to the
  caller; normalize_part_types appends before/after rows to /app/state/part_type_normalization_manifest.jsonl.
  Proposal for write-time retention (NOT applied, needs approval): additive sidecar table (see FIXES_TRACKER entry).
Data Imported/Modified: none (pure functions + SQL text).
Data Sources: production data model (offer_classification vocabulary, CLAUDE.md section 3 barcode types), importer sources
  (colmobil / toyota / subaru / champion: מקורי / חליפי; lexus+porsche PDFs: reversed ירוקמ; amayama: genuine / analog).
Missing Data Delegation: n/a.
Last Updated: 2026-11-07
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Optional

# --------------------------------------------------------------------------- vocabulary
STORED_VALUES: FrozenSet[str] = frozenset({"original", "oem", "oe_equivalent", "aftermarket", "remanufactured", "used"})

# EXACTLY the list inside the production index expression (uq_sp_part_supplier_variant). Tested against the migration
# SQL and pg_get_indexdef: if the index is ever rebuilt with another list this constant must change in the same change.
INDEX_OEM_GROUP = ("original", "genuine", "oem", "oe")

# folded synonym -> stored value.  Folding (see _fold) lower-cases, strips niqqud / bidi marks / quotes and turns
# `_`, `-`, `.` runs into one space, so every spelling variant below is written once, in natural form.
_SYNONYMS: Dict[str, str] = {}


def _add(stored: str, *labels: str) -> None:
    for lab in labels:
        _SYNONYMS[_fold(lab)] = stored


_PLACEHOLDERS_RAW = ("", "none", "null", "nan", "n/a", "na", "unknown", "unspecified", "סוג מוצר", "לא ידוע", "-", "--", "?")


def _fold(label: object) -> str:
    s = unicodedata.normalize("NFKC", str(label))
    s = "".join(ch for ch in s if unicodedata.category(ch) not in ("Mn", "Cf"))   # niqqud, cantillation, bidi/zero-width
    s = s.casefold()
    s = re.sub(r"[\"'`׳״“”‘’]", "", s)
    s = re.sub(r"[_\-.–—]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_add("original", "original", "genuine", "original oem", "oem original", "genuine oem", "original part", "genuine part",
     "מקורי", "ירוקמ")                      # ירוקמ = visual-order extraction of מקורי seen in lexus/porsche PDFs
_add("oem", "oem", "oe", "original equipment", "original equipment manufacturer")
_add("oe_equivalent", "oe equivalent", "oem equivalent", "oe equiv", "oem equiv", "שווה ערך", "שווה ערך ל oem", "שווה ערך ל oe")
_add("aftermarket", "aftermarket", "after market", "חליפי", "תחליפי", "יפילח", "שוק משני", "generic", "third party",
     "alternative", "analog", "analogue")
_add("remanufactured", "remanufactured", "reman", "refurbished", "משופץ")
_add("used", "used", "משומש")

_PLACEHOLDERS: FrozenSet[str] = frozenset(_fold(p) for p in _PLACEHOLDERS_RAW)
# labels that are real but need an OWNER decision before they can map to a variant (never silently merged)
_NEEDS_DECISION: FrozenSet[str] = frozenset(_fold(p) for p in ("replacement", "compatible", "new", "אחר", "other"))
_MULTI_SPLIT = re.compile(r"\s*(?:[/,;|+&]|\band\b|\bor\b|\bאו\b)\s*")


# --------------------------------------------------------------------------- errors / result
class VariantLabelError(ValueError):
    """A label that cannot be turned into a variant. Callers must skip + report it, never default it."""

    def __init__(self, label: object, reason: str):
        super().__init__(f"{reason}: {label!r}")
        self.label, self.reason = label, reason


class UnknownVariantLabel(VariantLabelError):
    pass


class AmbiguousVariantLabel(VariantLabelError):
    pass


@dataclass(frozen=True)
class VariantResolution:
    kind: str                        # 'ok' | 'missing' | 'unknown' | 'ambiguous'
    source_label: Optional[str]      # the label exactly as received (provenance)
    stored: Optional[str] = None     # value to write (ok only)
    key: Optional[str] = None        # identity key == index expression (ok only)
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == "ok"


# --------------------------------------------------------------------------- the index expression (code twin)
def index_key(stored: Optional[str]) -> Optional[str]:
    """Python twin of the production index expression, applied to an already-STORED value (no folding: the index doesn't)."""
    if stored is None:
        return None
    low = stored.lower()
    return "oem" if low in INDEX_OEM_GROUP else low


def INDEX_KEY_SQL(col: str = "part_type") -> str:
    """SQL text of the index key expression for `col` (for collision queries). Mirrors the migration file exactly."""
    grp = ",".join(f"'{g}'" for g in INDEX_OEM_GROUP)
    return f"(CASE WHEN lower({col}) IN ({grp}) THEN 'oem' ELSE lower({col}) END)"


# --------------------------------------------------------------------------- resolution
def _resolve_single(folded: str) -> Optional[str]:
    if folded in _SYNONYMS:
        return _SYNONYMS[folded]
    compact = folded.replace(" ", "")
    for syn, stored in _SYNONYMS.items():          # doubled importer artefacts: מקורימקורי, חליפיחליפי, משופץמשופץ
        s = syn.replace(" ", "")
        if len(s) >= 3 and len(compact) > len(s) and len(compact) % len(s) == 0 and compact == s * (len(compact) // len(s)):
            return stored
    return None


def resolve(label: object) -> VariantResolution:
    """Classify a label. Total function: never raises, never defaults."""
    src = None if label is None else str(label)
    folded = _fold(label) if label is not None else ""
    if folded in _PLACEHOLDERS:
        return VariantResolution("missing", src, reason="blank or placeholder label")
    if folded in _NEEDS_DECISION:
        return VariantResolution("ambiguous", src, reason="label needs an owner decision (not mapped to any variant)")
    parts = [p for p in _MULTI_SPLIT.split(folded) if p]
    if len(parts) > 1:
        res = [_resolve_single(p) for p in parts]
        if None in res:
            return VariantResolution("unknown", src, reason="multi-part label with an unrecognised part")
        if len(set(res)) == 1:
            stored = res[0]
            return VariantResolution("ok", src, stored, index_key(stored))
        return VariantResolution("ambiguous", src, reason="label names more than one variant")
    stored = _resolve_single(folded)
    if stored is None:
        return VariantResolution("unknown", src, reason="unrecognised label")
    return VariantResolution("ok", src, stored, index_key(stored))


_TEXT_RX: Optional["re.Pattern[str]"] = None


def _text_rx():
    global _TEXT_RX
    if _TEXT_RX is None:
        alts = sorted(_SYNONYMS, key=len, reverse=True)           # longest first: 'oem equivalent' is consumed before 'oem'
        _TEXT_RX = re.compile(r"(?<![^\W_])(" + "|".join(re.escape(a) for a in alts) + r")(?![^\W_])")
    return _TEXT_RX


def resolve_text(text: object) -> VariantResolution:
    """Like resolve(), but for a short FREE-TEXT type field that merely CONTAINS a known label ('Genuine Land Rover Part',
    'OEM Equivalent', 'Uprated - OE equivalent'). Whole-word, longest-label-first matching, so 'OEM equivalent' is one
    oe_equivalent match and never also an 'oem'. Several matches must agree on the identity KEY (stored value: the
    'original' spelling wins over 'oem'); different keys -> ambiguous. No match -> unknown (or missing when blank).
    For type/label columns only - never run it over a product title."""
    r = resolve(text)
    if r.kind in ("ok", "missing"):
        return r
    src = None if text is None else str(text)
    folded = _fold(text)
    found = [m.group(1) for m in _text_rx().finditer(folded)]
    if not found:
        if folded in _NEEDS_DECISION or any(w in folded.split() for w in _NEEDS_DECISION):
            return VariantResolution("ambiguous", src, reason="label needs an owner decision (not mapped to any variant)")
        return VariantResolution("unknown", src, reason="no known variant label inside the text")
    stored = {_SYNONYMS[f] for f in found}
    keys = {index_key(v) for v in stored}
    if len(keys) > 1:
        return VariantResolution("ambiguous", src, reason="text names more than one variant")
    pick = "original" if "original" in stored else sorted(stored)[0]
    return VariantResolution("ok", src, pick, index_key(pick))


def canonical_part_type(label: object) -> Optional[str]:
    """Stored value for a label; None when MISSING. Raises UnknownVariantLabel / AmbiguousVariantLabel (fail closed)."""
    r = resolve(label)
    if r.kind == "ok":
        return r.stored
    if r.kind == "missing":
        return None
    raise (AmbiguousVariantLabel if r.kind == "ambiguous" else UnknownVariantLabel)(label, r.reason)


def variant_key(label: object) -> Optional[str]:
    """Identity key (== index expression) for a label; None = untyped. Same errors as canonical_part_type."""
    return index_key(canonical_part_type(label))


def same_variant(a: object, b: object) -> bool:
    """True when two labels are ONE variant for uniqueness purposes (both missing counts as the untyped identity)."""
    return variant_key(a) == variant_key(b)


def lenient_class(label: object) -> Optional[str]:
    """Product class (== variant key) for COMPARISON/DISPLAY code that must never raise: ok -> key, missing -> None,
    unknown/ambiguous -> the folded raw label (compares equal only to itself, never merged with a real class)."""
    r = resolve(label)
    if r.kind == "ok":
        return r.key
    if r.kind == "missing":
        return None
    return _fold(label) or None


# Names used by maintenance/restore_merged_offers_133.py (policy 1a): the CLASS of a label and the set of known classes.
KNOWN_VARIANTS: FrozenSet[str] = frozenset(index_key(v) for v in STORED_VALUES)
canonical_variant = lenient_class


def catalog_part_type(label: object) -> Optional[str]:
    """Value to STORE in parts_catalog.part_type (catalog convention = the variant CLASS / index key: oem | oe_equivalent |
    aftermarket | remanufactured | used). Missing, unknown and ambiguous labels give None (left untyped, never guessed) so a
    catalog write can neither raise nor invent a variant. Every parts_catalog.part_type writer calls this."""
    r = resolve(label)
    return r.key if r.kind == "ok" else None


def try_canonical(label: object) -> Optional[str]:
    """Write-time helper for importers: stored value, or None when missing/unknown/ambiguous (the offer is then written
    UNTYPED = uq_sp_part_supplier_untyped, never with a guessed variant). Use resolve() when the reason matters."""
    r = resolve(label)
    return r.stored if r.kind == "ok" else None


def labels_for_stored(*stored: str) -> List[str]:
    """Every lower-cased spelling (space / underscore / hyphen forms) that resolves to one of the STORED values: for SQL
    IN-lists and UI bucket lists, so no module keeps its own copy of the vocabulary."""
    out = set()
    for syn, st in _SYNONYMS.items():
        if st in stored:
            forms = {syn, syn.replace(" ", "_"), syn.replace(" ", "-")}
            if re.fullmatch(r"[\u0590-\u05ff]+", syn) and len(syn) >= 3:
                forms.add(syn + syn)          # doubled importer artefacts (מקורימקורי) still resolve to this value
            out.update(forms)
    return sorted(out)


def sql_labels(key: str) -> List[str]:
    """Same, selected by identity KEY (class) instead of stored value."""
    return labels_for_stored(*[v for v in STORED_VALUES if index_key(v) == key])


def noncanonical_literals(target: str = "class") -> Dict[str, str]:
    """{exact stored spelling -> value it must become} for the spellings importers are known to have written (case forms of
    every synonym plus doubled artefacts). Equality lookups on these literals use the part_type index; the mapping itself is
    produced by resolve(), so there is no second dictionary. target: 'class' (parts_catalog convention = index key) or
    'stored' (supplier_parts convention)."""
    out: Dict[str, str] = {}
    for syn in list(_SYNONYMS):
        forms = {syn, syn.replace(" ", "_"), syn.replace(" ", "-"), syn.replace(" ", "")}
        forms |= {f + f for f in list(forms) if len(f.replace(" ", "")) >= 3 and re.fullmatch(r"[\u0590-\u05ff]+", f)}
        for f in forms:
            for lit in {f, f.upper(), f.capitalize(), f.title()}:
                r = resolve(lit)
                if r.ok:
                    want = r.key if target == "class" else r.stored
                    if want != lit:
                        out[lit] = want
    return out


def synonym_table() -> Dict[str, str]:
    """Read-only copy of folded synonym -> stored value (for docs, audits and tests)."""
    return dict(_SYNONYMS)


def placeholders() -> FrozenSet[str]:
    return _PLACEHOLDERS


def known_stored(values: Iterable[str]) -> List[str]:
    return [v for v in values if v in STORED_VALUES]
