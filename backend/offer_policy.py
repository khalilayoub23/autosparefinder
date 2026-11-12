"""
Script: offer_policy.py
Purpose: the ONE executable counterpart of docs/policies/OFFER_DEDUPLICATION_POLICY.md - policy loader (fail closed),
         the six-class offer classifier, and the write gate every destructive/identity-changing offer operation must pass.
Process:
  require_policy()            -> PolicyInfo(version, sha256, path) or raises PolicyUnavailable (missing/unreadable/
                                 malformed/older than min_version). Callers must NOT fall back to older matching logic.
  classify_pair(a, b)         -> Classification A..F for two offers (decision order = policy section 2).
  assert_write_allowed(...)   -> raises PolicyViolation unless the action is permitted for the classification + evidence.
  audit_stamp()               -> {"policy_version","policy_sha256"} for every report / audit row.
Data Imported/Modified: none (pure functions; reads the policy file).
Data Sources: docs/policies/OFFER_DEDUPLICATION_POLICY.md (searched via OFFER_POLICY_PATH, <backend>/../docs/policies,
              /policies, /app/docs/policies; pin OFFER_POLICY_EXPECTED_SHA256, required when OFFER_POLICY_REQUIRE_PIN=1 - docker-compose sets both). NOTE: the production containers mount only backend/ -> /app, so inside them the
              file is NOT visible until docs/ is mounted (needs a compose change + recreate); until then every
              destructive caller fails closed, by design.
Missing Data Delegation: unknown variant / supplier source -> UNRESOLVED (left unchanged).
Last Updated: 2026-11-07
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

import variant_identity as _vi
from offer_classification import normalize_type

POLICY_ID = "offer-deduplication"
POLICY_FILENAME = "OFFER_DEDUPLICATION_POLICY.md"
REQUIRED_MARKERS = ("COMPETING_OFFERS", "DISTINCT_VARIANTS", "PRICE_UPDATE", "TRUE_DUPLICATE", "MISATTRIBUTED", "UNRESOLVED")

# Actors (agents / scripts) that may change offer identity and have been bound to this policy. A new agent is added here
# in the same change that makes it call require_policy() and that adds it to tests/test_offer_policy.py.
POLICY_BOUND_ACTORS = (
    "maintenance/offer_policy_repair.py",
    "maintenance/merge_master_parts.py",
    "maintenance/bmw_oem_dedup.py",
    "maintenance/quarantine_motorstore_offers_129.py",
    "maintenance/restore_merged_offers_133.py",
    "maintenance/remove_placeholder_offers_149.py",   # owner-authorised removal of provably fabricated sentinel offers (archived)
)


class PolicyUnavailable(RuntimeError):
    """The policy cannot be loaded -> no destructive/irreversible offer write may happen."""


class PolicyViolation(RuntimeError):
    """An attempted offer operation contradicts the policy."""


class Classification(str, Enum):
    COMPETING_OFFERS = "A"
    DISTINCT_VARIANTS = "B"
    PRICE_UPDATE = "C"
    TRUE_DUPLICATE = "D"
    MISATTRIBUTED = "E"
    UNRESOLVED = "F"


@dataclass(frozen=True)
class PolicyInfo:
    version: str
    sha256: str
    path: str
    pinned: bool = False  # True when the file hash matched a configured OFFER_POLICY_EXPECTED_SHA256 pin


def _candidate_paths() -> Sequence[Path]:
    here = Path(__file__).resolve().parent
    out = []
    if os.getenv("OFFER_POLICY_PATH"):
        out.append(Path(os.environ["OFFER_POLICY_PATH"]))
    out += [here.parent / "docs" / "policies" / POLICY_FILENAME, Path("/policies") / POLICY_FILENAME,
            Path("/app/docs/policies") / POLICY_FILENAME,
            here / "docs" / "policies" / POLICY_FILENAME]
    return out


def _vtuple(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def configured_pins() -> Tuple[str, ...]:
    """Hashes accepted by the runtime pin (OFFER_POLICY_EXPECTED_SHA256: one or several, comma/space separated)."""
    return tuple(h for h in re.split(r"[\s,;]+", os.getenv("OFFER_POLICY_EXPECTED_SHA256", "").strip().lower()) if h)


def load_policy(path: Optional[os.PathLike] = None, min_version: Optional[str] = None) -> PolicyInfo:
    """Read + validate the policy file. Raises PolicyUnavailable with the exact problem.
    Pin (policy section 8): with OFFER_POLICY_EXPECTED_SHA256 set the file's hash must be one of the listed hashes; with
    OFFER_POLICY_REQUIRE_PIN=1 (set in docker-compose) a missing pin is itself a refusal - clearing the pin never weakens it."""
    paths = [Path(path)] if path else list(_candidate_paths())
    problems = []
    for p in paths:
        try:
            raw = p.read_bytes()
        except OSError as exc:
            problems.append(f"{p}: {exc.__class__.__name__}")
            continue
        text = raw.decode("utf-8", "replace")
        if f"policy-id: {POLICY_ID}" not in text:
            raise PolicyUnavailable(f"{p}: not the {POLICY_ID} policy (policy-id marker missing)")
        m = re.search(r"policy-version:\s*([0-9][0-9.]*)", text)
        if not m:
            raise PolicyUnavailable(f"{p}: policy-version marker missing")
        missing = [k for k in REQUIRED_MARKERS if f"`{k}`" not in text]
        if missing:
            raise PolicyUnavailable(f"{p}: classes missing from the policy text: {missing}")
        if min_version and _vtuple(m.group(1)) < _vtuple(min_version):
            raise PolicyUnavailable(f"{p}: version {m.group(1)} is older than required {min_version}")
        digest = hashlib.sha256(raw).hexdigest()
        pins = configured_pins()
        if os.getenv("OFFER_POLICY_REQUIRE_PIN", "").strip().lower() in ("1", "true", "yes", "on") and not pins:
            raise PolicyUnavailable(f"{p}: OFFER_POLICY_REQUIRE_PIN is set but OFFER_POLICY_EXPECTED_SHA256 is empty")
        if pins and digest not in pins:  # a changed/edited policy is refused until the pin is deliberately updated
            raise PolicyUnavailable(f"{p}: sha256 {digest[:16]}... does not match OFFER_POLICY_EXPECTED_SHA256 "
                                    f"({', '.join(x[:16] + '...' for x in pins)})")
        return PolicyInfo(m.group(1), digest, str(p), pinned=bool(pins))
    raise PolicyUnavailable("policy file not found/readable: " + "; ".join(problems))


def require_policy(min_version: Optional[str] = None) -> PolicyInfo:
    return load_policy(min_version=min_version)


def audit_stamp(info: Optional[PolicyInfo] = None) -> dict:
    info = info or require_policy()
    return {"policy_version": info.version, "policy_sha256": info.sha256}


# ---------------------------------------------------------------- classification
def _clean_type(v: Optional[str]) -> Optional[str]:
    t = normalize_type(v)
    return None if t in (None, "", "unknown", "none", "null") else t


def _variant(o: Mapping[str, Any]) -> Optional[str]:
    """Offer variant: the offer's own class; NULL inherits the catalog part's class (legacy rule)."""
    return _clean_type(o.get("part_type")) or _clean_type(o.get("catalog_part_type"))


def _label_unrecognised(o: Mapping[str, Any]) -> bool:
    """Policy 1a: the offer's effective label (its own, else the catalog part's) is present but is not a known
    variant ('compatible', 'replacement', a multi-variant label, an unseen spelling). Such an offer is neither the
    same as nor different from any variant."""
    for k in ("part_type", "catalog_part_type"):
        r = _vi.resolve(o.get(k))
        if r.kind == "missing":
            continue
        return r.kind != "ok"
    return False


def _same(a: Any, b: Any) -> bool:
    return a is not None and b is not None and str(a).strip().lower() == str(b).strip().lower()


def classify_pair(a: Mapping[str, Any], b: Mapping[str, Any], *, evidence: Optional[Mapping[str, Any]] = None) -> Classification:
    """Classify two offers of the same product identity (policy section 2, first match wins).

    Offer keys: supplier_id, part_id, supplier_sku, part_type (offer), catalog_part_type, price_ils, is_available,
    supplier_url, name, row_id, observed_at.   evidence: {"source_proves_supplier": <supplier_id>} for class E,
    {"later": "a"|"b"} when one record is known to be a later observation of the other.
    A price or SKU relation alone never yields C or D."""
    ev = evidence or {}
    # E is decided per offer, only on explicit source evidence that names a DIFFERENT correct supplier.
    proven = ev.get("source_proves_supplier")
    if proven is not None and str(proven) != str(a.get("supplier_id")) and str(proven) != str(b.get("supplier_id")):
        return Classification.UNRESOLVED  # evidence names a third supplier: not enough to act on, stay put
    if proven is not None and str(proven) != str(a.get("supplier_id")) and str(proven) == str(b.get("supplier_id")):
        return Classification.MISATTRIBUTED
    if str(a.get("supplier_id")) != str(b.get("supplier_id")):
        return Classification.COMPETING_OFFERS
    if _label_unrecognised(a) or _label_unrecognised(b):
        # Decision order 2 / section 1a. The folded raw label used to be compared like a class, so
        # 'compatible' vs 'oem' came out as B (a verified distinct variant) instead of F.
        return Classification.UNRESOLVED
    va, vb = _variant(a), _variant(b)
    if va is None or vb is None:
        # variant unknown on a side: only the same commercial offer can be reasoned about; otherwise unresolved
        if va is None and vb is None and _same(a.get("supplier_sku"), b.get("supplier_sku")) and _same(a.get("name"), b.get("name")):
            pass  # falls through to C/D tests below
        else:
            return Classification.UNRESOLVED
    elif va != vb:
        return Classification.DISTINCT_VARIANTS
    same_offer = (_same(a.get("supplier_sku"), b.get("supplier_sku")) and str(a.get("part_id")) == str(b.get("part_id"))) \
        or (a.get("row_id") is not None and a.get("row_id") == b.get("row_id"))
    if not same_offer:
        return Classification.UNRESOLVED
    if ev.get("later") in ("a", "b") and not _same(a.get("price_ils"), b.get("price_ils")):
        return Classification.PRICE_UPDATE
    if all(_same(a.get(k), b.get(k)) or (a.get(k) is None and b.get(k) is None)
           for k in ("price_ils", "supplier_url")) and bool(a.get("is_available")) == bool(b.get("is_available")):
        return Classification.TRUE_DUPLICATE
    return Classification.UNRESOLVED


# ---------------------------------------------------------------- write gate
DESTRUCTIVE = {"delete", "deactivate", "hide", "merge", "reassign_supplier", "retire"}
_ALLOWED = {
    "delete": set(),  # nothing is ever hard-deleted (policy rule 10)
    "deactivate": {Classification.TRUE_DUPLICATE},
    "retire": {Classification.TRUE_DUPLICATE},
    "merge": {Classification.TRUE_DUPLICATE},
    "hide": {Classification.TRUE_DUPLICATE},
    "reassign_supplier": {Classification.MISATTRIBUTED},
    "price_update": {Classification.PRICE_UPDATE},
    # non-destructive INSERT of a previously deleted offer (maintenance/restore_merged_offers_133.py): only where the restored
    # offer is a distinct variant (B) or its relation to the existing offer is unresolved (F). Never a duplicate/update/competing.
    "restore": {Classification.DISTINCT_VARIANTS, Classification.UNRESOLVED},
}


def assert_write_allowed(action: str, classification: Classification, *, policy: Optional[PolicyInfo] = None,
                         has_other_orderable_offer: Optional[bool] = None, dry_run_done: bool = False,
                         evidence: Optional[Mapping[str, Any]] = None) -> PolicyInfo:
    """Gate for any offer write. Raises PolicyUnavailable (policy unreadable) or PolicyViolation."""
    info = policy or require_policy()
    allowed = _ALLOWED.get(action)
    if allowed is None:
        raise PolicyViolation(f"unknown offer action {action!r}")
    if classification not in allowed:
        raise PolicyViolation(f"action {action!r} is not permitted for class {classification.name} ({classification.value})")
    if action == "restore":  # policy section 5a: authorised, evidence-based, canonical variant checked first, dry run done
        ev = evidence or {}
        need = [k for k in ("approval_ref", "input_sha256", "source_backup") if not str(ev.get(k) or "").strip()]
        if need:
            raise PolicyViolation(f"restore needs authorization/provenance evidence: missing {need}")
        if ev.get("canonical_variant_checked") is not True:
            raise PolicyViolation("restore refused: the canonical variant (policy 1a) was not checked before the uniqueness check")
        if not dry_run_done:
            raise PolicyViolation("a dry-run report must precede any restore write")
    if action in DESTRUCTIVE:
        if not dry_run_done:
            raise PolicyViolation("a dry-run report must precede any destructive offer write")
        if action != "reassign_supplier" and has_other_orderable_offer is not True:
            raise PolicyViolation("refusing: would remove the product's only orderable offer (or that is unverified)")
        if action == "reassign_supplier" and not (evidence or {}).get("source_proves_supplier"):
            raise PolicyViolation("reassignment needs source evidence naming the correct supplier")
    return info


def gate_or_exit(actor: str, dry_run: bool, enforce: bool = True) -> Optional[PolicyInfo]:
    """CLI entry guard for maintenance scripts that change offers. Dry runs only report; a real run needs the policy.
    enforce=False (temporary, only for scheduled legacy jobs whose offer-deleting branches are replaced by a pending
    candidate patch): warns and proceeds instead of exiting, so a missing mount cannot silently stop a scheduled job.
    Prints the policy stamp; on PolicyUnavailable prints the exact problem and exits 3 BEFORE any connection/write."""
    import sys
    try:
        info = require_policy()
    except PolicyUnavailable as exc:
        if dry_run or not enforce:
            print(f"[offer-policy] {actor}: {'DRY RUN' if dry_run else 'NOT ENFORCED (legacy scheduled job)'} without policy ({exc})", file=sys.stderr)
            return None
        print(f"[offer-policy] {actor}: POLICY UNAVAILABLE - refusing to change offers: {exc}", file=sys.stderr)
        raise SystemExit(3)
    print(f"[offer-policy] {actor}: policy {info.version} sha256={info.sha256[:16]}")
    return info
