"""Offer identity acceptance matrix (FIXES_TRACKER 2026-11-13, owner mission section 4).

The owner's binding rules, each exercised through the REAL vocabulary (variant_identity), the REAL six-class
classifier (offer_policy.classify_pair) and the REAL write gate (offer_policy.assert_write_allowed) with Hebrew
AND English labels as importers deliver them. No database: pure rules. Expected values are written out here.

  - Hebrew and English labels of one class normalize to ONE identity; the source label is kept.
  - Genuine/OEM, OE-equivalent and aftermarket are DISTINCT valid offers of one supplier.
  - `compatible` / `replacement` are not guessed: unresolved, kept separate, never written.
  - Different suppliers are always competing offers, whatever the SKU or price.
  - Same supplier + same SKU + different price is NOT a price update without evidence.
  - Insufficient evidence -> unresolved -> no destructive write passes the gate.
  - Classifying the same pair again (repeated import / sync) gives the same answer in both orders.
"""
import itertools
import os
from pathlib import Path

import pytest

import offer_policy as op
import variant_identity as vi
from offer_policy import Classification as C

ROOT = Path(__file__).resolve().parent.parent
POLICY = Path(os.getenv("OFFER_POLICY_PATH") or ROOT.parent / "docs" / "policies" / "OFFER_DEDUPLICATION_POLICY.md")
needs_policy = pytest.mark.skipif(not POLICY.exists(), reason="policy file not mounted (set OFFER_POLICY_PATH)")


def offer(**kw):
    base = {"supplier_id": "s1", "part_id": "p1", "supplier_sku": "X1", "part_type": "oem", "price_ils": 100.0,
            "is_available": True, "supplier_url": "u", "name": "Pad", "row_id": None}
    base.update(kw)
    return base


# ── normalization: Hebrew and English, importer spellings ───────────────────────────────────
@pytest.mark.parametrize("label,stored,key", [
    ("מקורי", "original", "oem"), ("Original", "original", "oem"), ("genuine", "original", "oem"),
    ("OEM", "oem", "oem"), ("oem", "oem", "oem"),
    ("ירוקמ", "original", "oem"),                      # visual-order (reversed) PDF extraction of מקורי
    ("מקורימקורי", "original", "oem"),                 # doubled importer artefact
    ("חליפי", "aftermarket", "aftermarket"), ("תחליפי", "aftermarket", "aftermarket"),
    ("Aftermarket", "aftermarket", "aftermarket"), ("after-market", "aftermarket", "aftermarket"),
    ("OE equivalent", "oe_equivalent", "oe_equivalent"), ("oe_equivalent", "oe_equivalent", "oe_equivalent"),
])
def test_recognized_hebrew_and_english_labels_have_one_identity(label, stored, key):
    r = vi.resolve(label)
    assert (r.stored, r.key) == (stored, key)
    assert r.source_label == label                    # provenance: the source label is carried, not replaced
    assert vi.canonical_part_type(label) == stored and vi.variant_key(label) == key
    assert vi.index_key(stored) == key                # what the unique index will evaluate on the stored value


@pytest.mark.parametrize("label", ["compatible", "Compatible", "replacement", "new", "אחר", "other"])
def test_labels_needing_an_owner_decision_are_never_defaulted(label):
    r = vi.resolve(label)
    assert r.stored is None and r.key is None
    with pytest.raises(vi.AmbiguousVariantLabel):
        vi.canonical_part_type(label)


@pytest.mark.parametrize("label", ["מקורי/חליפי", "OEM / Aftermarket", "זיוף", "premium plus"])
def test_two_variants_in_one_label_or_an_unknown_label_fail_closed(label):
    with pytest.raises((vi.AmbiguousVariantLabel, vi.UnknownVariantLabel)):
        vi.canonical_part_type(label)


@pytest.mark.parametrize("label", [None, "", "  ", "unknown", "N/A", "סוג מוצר"])
def test_missing_label_is_untyped_not_a_variant(label):
    assert vi.canonical_part_type(label) is None and vi.variant_key(label) is None


def test_distinct_classes_never_share_an_identity():
    keys = {vi.variant_key(x) for x in ("מקורי", "OE equivalent", "חליפי", "remanufactured", "used")}
    assert keys == {"oem", "oe_equivalent", "aftermarket", "remanufactured", "used"}


# ── classifier ───────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("a,b", [("מקורי", "חליפי"), ("OEM", "aftermarket"), ("genuine", "OE equivalent"),
                                 ("מקורי", "oe_equivalent"), ("OE equivalent", "חליפי"), ("original", "תחליפי")])
def test_same_supplier_different_class_are_distinct_variants_in_either_language(a, b):
    x, y = offer(part_type=a), offer(part_type=b, supplier_sku="X2", price_ils=60.0)
    assert op.classify_pair(x, y) is C.DISTINCT_VARIANTS
    assert op.classify_pair(y, x) is C.DISTINCT_VARIANTS
    same_sku = offer(part_type=b, price_ils=60.0)                       # even with the SAME supplier SKU
    assert op.classify_pair(x, same_sku) is C.DISTINCT_VARIANTS


@pytest.mark.parametrize("a,b", [("מקורי", "OEM"), ("original", "genuine"), ("חליפי", "aftermarket"),
                                 ("ירוקמ", "Original")])
def test_hebrew_and_english_spellings_of_one_class_are_not_separate_variants(a, b):
    assert op.classify_pair(offer(part_type=a), offer(part_type=b)) is not C.DISTINCT_VARIANTS
    assert op.classify_pair(offer(part_type=a), offer(part_type=b)) is C.TRUE_DUPLICATE   # same offer re-read


@pytest.mark.parametrize("kw", [{}, {"price_ils": 250.0}, {"supplier_sku": "OTHER"}, {"part_type": "חליפי"},
                                {"part_type": None}])
def test_different_suppliers_are_always_competing_offers(kw):
    assert op.classify_pair(offer(), offer(supplier_id="s2", **kw)) is C.COMPETING_OFFERS


def test_same_supplier_same_sku_different_price_is_not_a_price_update_without_evidence():
    a, b = offer(price_ils=100.0), offer(price_ils=120.0)
    assert op.classify_pair(a, b) is C.UNRESOLVED
    assert op.classify_pair(b, a) is C.UNRESOLVED
    assert op.classify_pair(a, b, evidence={"later": "b"}) is C.PRICE_UPDATE     # only with a known order of observation


def test_same_sku_alone_never_proves_duplication():
    assert op.classify_pair(offer(part_id="p1"), offer(part_id="p2")) is C.UNRESOLVED          # other product
    assert op.classify_pair(offer(part_type=None, name="A"), offer(part_type=None, name="B")) is C.UNRESOLVED


@pytest.mark.parametrize("label", ["compatible", "replacement", "premium plus", None])
def test_ambiguous_or_missing_variant_stays_unresolved(label):
    a, b = offer(part_type="מקורי"), offer(part_type=label, supplier_sku="X2")
    assert op.classify_pair(a, b) is C.UNRESOLVED
    assert op.classify_pair(b, a) is C.UNRESOLVED


def test_classification_is_stable_under_repetition_and_order():
    offers = [offer(part_type="מקורי"), offer(part_type="חליפי", supplier_sku="X2"),
              offer(part_type="OE equivalent", supplier_sku="X3"), offer(supplier_id="s2"),
              offer(price_ils=130.0), offer(part_type="compatible", supplier_sku="X4")]
    first = {(i, j): op.classify_pair(a, b) for (i, a), (j, b) in itertools.permutations(enumerate(offers), 2)}
    for _ in range(3):                                                    # a repeated import / sync changes nothing
        again = {(i, j): op.classify_pair(a, b) for (i, a), (j, b) in itertools.permutations(enumerate(offers), 2)}
        assert again == first
    assert all(first[(i, j)] is first[(j, i)] for i, j in first)         # symmetric
    assert not any(c in (C.TRUE_DUPLICATE, C.PRICE_UPDATE) for c in first.values())   # nothing here may be merged


# ── write gate ───────────────────────────────────────────────────────────────────────────────
@needs_policy
@pytest.mark.parametrize("cls", [C.COMPETING_OFFERS, C.DISTINCT_VARIANTS, C.UNRESOLVED, C.PRICE_UPDATE])
@pytest.mark.parametrize("action", ["delete", "deactivate", "merge", "hide", "retire"])
def test_no_destructive_write_passes_for_distinct_competing_or_unresolved(cls, action):
    info = op.load_policy(POLICY)
    with pytest.raises(op.PolicyViolation):
        op.assert_write_allowed(action, cls, policy=info, has_other_orderable_offer=True, dry_run_done=True)


@needs_policy
def test_price_update_is_the_only_write_for_a_proven_update_and_nothing_else_gets_it():
    info = op.load_policy(POLICY)
    assert op.assert_write_allowed("price_update", C.PRICE_UPDATE, policy=info) is info
    for cls in (C.COMPETING_OFFERS, C.DISTINCT_VARIANTS, C.UNRESOLVED, C.TRUE_DUPLICATE, C.MISATTRIBUTED):
        with pytest.raises(op.PolicyViolation):
            op.assert_write_allowed("price_update", cls, policy=info)


@needs_policy
def test_hard_delete_is_never_allowed_even_for_a_true_duplicate():
    info = op.load_policy(POLICY)
    with pytest.raises(op.PolicyViolation):
        op.assert_write_allowed("delete", C.TRUE_DUPLICATE, policy=info, has_other_orderable_offer=True, dry_run_done=True)


def test_gate_fails_closed_without_a_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(op, "_candidate_paths", lambda: [tmp_path / "missing.md"])
    with pytest.raises(op.PolicyUnavailable):
        op.assert_write_allowed("price_update", C.PRICE_UPDATE)


@pytest.mark.parametrize("script", ["maintenance/merge_master_parts.py", "maintenance/bmw_oem_dedup.py",
                                    "services/aliexpress_price_sync.py"])
def test_reviewed_write_paths_do_not_delete_or_relabel_offers(script):
    src = (ROOT / script).read_text(encoding="utf-8")
    low = src.lower()
    assert "delete from supplier_parts" not in low
    if script.startswith("maintenance/"):
        assert "gate_or_exit" in src and script in op.POLICY_BOUND_ACTORS
    assert "on conflict (part_id, supplier_id)" not in low
