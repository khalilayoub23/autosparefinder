"""
Script:  part_identity.py
Purpose: The ONE identity policy for catalogue parts: decide whether two rows that share a NORMALISED part number are
         the SAME product, DIFFERENT products, or UNCERTAIN. Every code path that merges, deduplicates or relinks parts
         asks this module (merge_master_parts, bmw_oem_dedup, unmerge_distinct_articles, the weekly parity gate).
Process: FAIL CLOSED - only a proven SAME may be merged; UNCERTAIN is never merged, it is reported for review.
         A manufacturer OEM number is unique whatever its punctuation (06Q-103-495-H == 06Q103495H). An AFTERMARKET
         article number is only unique WITHIN ITS BRAND: REMSA 1187.02 (brake pads) and TOPRAN 118 702 (gear cable)
         collapse to the same string once dots/dashes/spaces are stripped (2026-11-14: 522 such wrong merges undone).
         A shared number, a punctuation-stripped number, a similar title or a vehicle name is NEVER proof on its own.
         classify(a, b):
           1. product class (variant_identity) - missing on either row -> UNCERTAIN; two different classes (genuine vs
              aftermarket, oe_equivalent vs aftermarket ...) -> UNCERTAIN: never collapsed, and never called DIFFERENT
              on the label alone (sources label the same part differently).
           2. brand evidence - the brand in front of the row's own number in its title
              (brand-first 'TOPRAN 118 702 ...' or vehicle-first '... Tappet FRECCIA PI 06-0019'), accepted only when it
              is a KNOWN brand (data/article_brands.json, built from the catalogue) after VERIFIED aliases
              (data/article_brand_aliases.json - every entry carries its evidence; nothing is matched fuzzily).
           3. genuine class: SAME when both rows belong to one manufacturer and neither carries a foreign brand (or both
              carry the same one); one branded + one unbranded -> UNCERTAIN.
              other classes: both brands known -> DIFFERENT when they differ, SAME when equal AND the raw numbers are
              equal (same brand + punctuation-only difference -> UNCERTAIN); a brand missing on either side
              (unbranded, vehicle-titled without a known brand, rare brand) -> UNCERTAIN.
Data Imported/Modified: none (pure functions; the two data files are read once).
Data Sources: backend/data/article_brands.json (maintenance/build_article_brands.py), backend/data/article_brand_aliases.json.
Missing Data Delegation: a missing/unreadable brand file means NO brand is known -> every non-genuine pair is UNCERTAIN.
Last Updated: 2026-11-14
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import variant_identity

SAME, DIFFERENT, UNCERTAIN = "same", "different", "uncertain"

_DATA = Path(__file__).resolve().parent / "data"
BRANDS_FILE = _DATA / "article_brands.json"
ALIASES_FILE = _DATA / "article_brand_aliases.json"
MIN_BRAND_ROWS = 30        # a title prefix is a brand when at least this many catalogue titles start with it
MIN_SUFFIX_ROWS = 200      # vehicle-first titles: the brand is only the END of the prefix, so demand more support

_PREFIX = re.compile(r"^\s*(?:article\s*(?:№|no\.?|#)?|№)\s*:?\s*", re.I)      # 'Article №: T104', '№: 70-0122'
_EDGE = " ,-–|("


@dataclass(frozen=True)
class Verdict:
    decision: str
    reason: str

    @property
    def same(self) -> bool:
        return self.decision == SAME


# --------------------------------------------------------------------------- numbers
def raw_number(oem_number: Any) -> str:
    """The number exactly as the source wrote it, minus the 'Article №:' label and case; inner spaces are KEPT
    (collapsed to one): SWF 119272 (wipers) and TOPRAN 119 272 (breather pipe) differ only by a space."""
    return re.sub(r"\s+", " ", _PREFIX.sub("", str(oem_number or ""))).strip().upper()


def norm_number(oem_number: Any) -> str:
    """Candidate key only (never proof): the raw number without spaces, dashes and dots - the merge's group key."""
    return re.sub(r"[\s.\-]", "", raw_number(oem_number))


# --------------------------------------------------------------------------- brands
def brand_key(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip(_EDGE).upper()


# Words that are a vehicle make/model or a generic word AND also a title prefix: accepted as a brand only when they are
# the WHOLE prefix ('SMART 123 ...'), never as the end of a vehicle-first title ('Ford Transit Connect 123').
_NOT_A_SUFFIX_BRAND = {"CONNECT", "ENERGY", "GREEN", "START", "FAST", "FORCE", "PILOT", "HOOK", "SONIC", "RING",
                       "PROFIT", "MOTIVE", "MODULA", "NEXUS", "PULSAR", "OXFORD", "CHAMPION", "CONTINENTAL", "GENESIS"}
_vocab: Optional[Dict[str, int]] = None
_vehicle_words: Optional[frozenset] = None
_aliases: Optional[Dict[str, str]] = None


def load_brands(path: Optional[Path] = None) -> Dict[str, int]:
    """{BRAND: number of catalogue titles}. Unreadable file -> {} (nothing is a known brand: fail closed)."""
    try:
        raw = json.loads(Path(path or BRANDS_FILE).read_text(encoding="utf-8"))["brands"]
        return {brand_key(k): int(v) for k, v in raw.items() if brand_key(k)}
    except Exception:
        return {}


def load_aliases(path: Optional[Path] = None) -> Dict[str, str]:
    """{ALIAS: BRAND}. An entry without written evidence is ignored: an alias is a verified fact, not a guess."""
    out: Dict[str, str] = {}
    try:
        for e in json.loads(Path(path or ALIASES_FILE).read_text(encoding="utf-8")).get("aliases", []):
            a, b, ev = brand_key(e.get("alias")), brand_key(e.get("brand")), str(e.get("evidence") or "").strip()
            if a and b and a != b and len(ev) >= 20:
                out[a] = b
    except Exception:
        return {}
    return out


def load_vehicle_words(path: Optional[Path] = None) -> frozenset:
    try:
        return frozenset(brand_key(w) for w in json.loads(Path(path or BRANDS_FILE).read_text(encoding="utf-8")).get("vehicle_words", []))
    except Exception:
        return frozenset()


def _suffix_brand_ok(cand: str) -> bool:
    global _vehicle_words
    if _vehicle_words is None:
        _vehicle_words = load_vehicle_words()
    return (len(re.sub(r"[^A-Z0-9]", "", cand)) >= 3 and cand not in _NOT_A_SUFFIX_BRAND and cand not in _vehicle_words
            and _vocabulary().get(cand, 0) >= MIN_SUFFIX_ROWS)


def _vocabulary() -> Dict[str, int]:
    global _vocab
    if _vocab is None:
        _vocab = load_brands()
    return _vocab


def _alias_map() -> Dict[str, str]:
    global _aliases
    if _aliases is None:
        _aliases = load_aliases()
    return _aliases


def configure(brands: Optional[Mapping[str, int]] = None, aliases: Optional[Mapping[str, str]] = None) -> None:
    """Tests / tools: replace the loaded vocabulary or alias table (None = reload from the data files)."""
    global _vocab, _aliases, _vehicle_words
    _vehicle_words = None
    _vocab = None if brands is None else {brand_key(k): int(v) for k, v in brands.items()}
    _aliases = None if aliases is None else {brand_key(k): brand_key(v) for k, v in aliases.items()}


def canonical_brand(brand: Any) -> Optional[str]:
    """A known brand under its canonical spelling (verified aliases applied), else None."""
    b = brand_key(brand)
    if not b:
        return None
    b = _alias_map().get(b, b)
    return b if _vocabulary().get(b, 0) >= MIN_BRAND_ROWS else None


def brand_prefix(name: Any, oem_number: Any) -> Optional[str]:
    """Raw text in front of the row's own number inside its name ('REMSA 1187.02 Brake pad' -> 'REMSA'), else None.
    Not a brand by itself: a vehicle-first title gives 'NISSAN NAVARA PLATFORM TAPPET FRECCIA' (see title_brand)."""
    n, num = str(name or ""), _PREFIX.sub("", str(oem_number or "")).strip()
    if not n or not num:
        return None
    i = n.upper().find(num.upper())
    if i <= 0:
        return None
    return brand_key(n[:i]) or None


def title_brand(name: Any, oem_number: Any) -> Optional[str]:
    """The KNOWN brand standing directly in front of the row's own number in its title, else None.
    Brand-first: the whole prefix is a known brand. Vehicle-first: the longest known brand ending the prefix."""
    n, num = str(name or "").upper(), raw_number(oem_number)
    if not n or not num:
        return None
    for m in re.finditer(r"(?<![A-Z0-9])" + re.escape(num) + r"(?![A-Z0-9])", n):
        pfx = brand_key(n[:m.start()])
        if not pfx:
            continue
        whole = canonical_brand(pfx)
        if whole:
            return whole
        toks = pfx.split(" ")
        for k in (3, 2, 1):
            if len(toks) > k:
                cand = brand_key(" ".join(toks[-k:]))
                cand = _alias_map().get(cand, cand)
                if _suffix_brand_ok(cand):
                    return cand
    return None


def title_prefix(name: Any, oem_number: Any) -> Optional[str]:
    """Raw text in front of the row's own number (token boundary) in its title, known brand or not; else None."""
    n, num = str(name or "").upper(), raw_number(oem_number)
    if not n or not num:
        return None
    m = re.search(r"(?<![A-Z0-9])" + re.escape(num) + r"(?![A-Z0-9])", n)
    return (brand_key(n[:m.start()]) or None) if m else None


def row_brand(row: Mapping[str, Any]) -> Optional[str]:
    """Brand evidence of one row = the known brand in front of its own number in its title. The stored
    specifications.part_brand is NOT evidence: the importer fills it with the first word of the title ('FEBI' for
    febi bilstein, 'BR' for BR Turbo, sometimes the whole title) - a guess, measured on live rows 2026-11-14."""
    return title_brand(row.get("name"), row.get("oem_number"))


def _is_makes_own(brand: str, row: Mapping[str, Any]) -> bool:
    """'SUZUKI 09280-...' on a Suzuki row / 'ZEEKR PART ...' on a Zeekr row: the vehicle maker itself, not a foreign brand."""
    b = re.sub(r"[^A-Z0-9]", "", brand)
    m = re.sub(r"[^A-Z0-9]", "", str(row.get("manufacturer") or "").upper())
    return len(m) >= 2 and len(b) >= 2 and (b.startswith(m) or m.startswith(b))


# --------------------------------------------------------------------------- the decision
def classify(a: Mapping[str, Any], b: Mapping[str, Any]) -> Verdict:
    """SAME / DIFFERENT / UNCERTAIN for two catalogue rows (name, oem_number, part_type, manufacturer_id,
    manufacturer, specifications). Only SAME may ever be merged."""
    if not norm_number(a.get("oem_number")) or norm_number(a.get("oem_number")) != norm_number(b.get("oem_number")):
        return Verdict(DIFFERENT, "different_number")
    va, vb = variant_identity.lenient_class(a.get("part_type")), variant_identity.lenient_class(b.get("part_type"))
    if va is None or vb is None:
        return Verdict(UNCERTAIN, "missing_product_type")
    if va not in variant_identity.KNOWN_VARIANTS or vb not in variant_identity.KNOWN_VARIANTS:
        return Verdict(UNCERTAIN, "unknown_product_type")
    ba, bb = row_brand(a), row_brand(b)
    if va != vb:
        # A product-type label differs between sources for the very same part ('Connector T4A10791' typed oem by one
        # importer and oe_equivalent by another: 117,245 recorded merges), so a label conflict is never PROOF of a
        # different product - and never permission to merge. Only two known, different brands prove a difference.
        if ba and bb and ba != bb:
            return Verdict(DIFFERENT, "different_brands")
        return Verdict(UNCERTAIN, "product_type_conflict")
    if va == "oem":
        ma, mb = a.get("manufacturer_id"), b.get("manufacturer_id")
        if ma is None or mb is None or str(ma) != str(mb):
            return Verdict(UNCERTAIN, "manufacturer_missing_or_different")
        # a title that carries some text in front of its own number which is neither a known brand nor the vehicle
        # maker itself (rare brand, unknown wording) is NOT evidence of a genuine part: fail closed
        for row, brand in ((a, ba), (b, bb)):
            pfx = title_prefix(row.get("name"), row.get("oem_number"))
            if pfx and not brand and not _is_makes_own(pfx, row):
                return Verdict(UNCERTAIN, "unrecognised_title_prefix")
        fa = ba if ba and not _is_makes_own(ba, a) else None
        fb = bb if bb and not _is_makes_own(bb, b) else None
        if fa and fb:
            if fa != fb:
                return Verdict(DIFFERENT, "different_brands")
            if raw_number(a.get("oem_number")) != raw_number(b.get("oem_number")):
                return Verdict(UNCERTAIN, "same_brand_punctuation_variant")
            return Verdict(SAME, "same_brand_same_number")
        if fa or fb:
            return Verdict(UNCERTAIN, "branded_vs_unbranded")
        return Verdict(SAME, "same_manufacturer_oem_number")
    if ba and bb:
        if ba != bb:
            return Verdict(DIFFERENT, "different_brands")
        # one brand, numbers that differ only by punctuation/spacing (FRECCIA PI06-0019 / PI 06-0019): very likely one
        # article, but normalisation is a candidate, never proof -> listed for review, not merged
        if raw_number(a.get("oem_number")) != raw_number(b.get("oem_number")):
            return Verdict(UNCERTAIN, "same_brand_punctuation_variant")
        return Verdict(SAME, "same_brand_same_number")
    return Verdict(UNCERTAIN, "brand_unknown")


def distinct_articles(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """True only when a and b are PROVABLY different products (never for an uncertain pair)."""
    return classify(a, b).decision == DIFFERENT


def cluster(rows: Sequence[Mapping[str, Any]]) -> Tuple[List[List[Mapping[str, Any]]], Dict[str, int]]:
    """Split one candidate group (rows sharing manufacturer + normalised number) into identity clusters. A row joins a
    cluster only when it is SAME with EVERY member, so a mixed group is judged per article. Returns the clusters and
    {'different': rows proven to be another product, 'uncertain': rows left alone for lack of evidence}."""
    clusters: List[List[Mapping[str, Any]]] = []
    for r in rows:
        for cl in clusters:
            if all(classify(m, r).same for m in cl):
                cl.append(r)
                break
        else:
            clusters.append([r])
    held = {"different": 0, "uncertain": 0}
    if len(clusters) > 1:
        flat = [(i, m) for i, cl in enumerate(clusters) for m in cl]
        # a row with no established identity at all is UNCERTAIN against everything; it must not drag a row that IS
        # proven different from the rest (STEINHOF towbar next to K2 twins and an unbranded title) into "uncertain"
        no_identity = {id(m) for i, m in flat
                       if all(classify(m, o).decision == UNCERTAIN for j, o in flat if o is not m)}
        for i, cl in enumerate(clusters):
            if len(cl) > 1:
                continue
            me = cl[0]
            unsure = id(me) in no_identity or any(
                classify(me, o).decision == UNCERTAIN for j, o in flat if j != i and id(o) not in no_identity)
            held["uncertain" if unsure else "different"] += 1
    return clusters, held


def evidence(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Per-row evidence of one candidate group, for a reviewer: which cluster, brand, class and why it is held."""
    clusters, _ = cluster(rows)
    out = []
    for i, cl in enumerate(clusters):
        for r in cl:
            why = sorted({classify(r, m).reason for j, c2 in enumerate(clusters) if j != i for m in c2})
            out.append({"id": str(r.get("id") or ""), "cluster": i, "cluster_size": len(cl),
                        "name": str(r.get("name") or "")[:90], "oem_number": r.get("oem_number"),
                        "part_type": r.get("part_type"), "brand": row_brand(r), "vs_other_clusters": why})
    return out
