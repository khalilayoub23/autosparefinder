"""
Script: supplier_qualification.py
Purpose: NIR's evidence-based supplier qualification (owner task 2026-10-27, section 3). Every candidate carries
         one record with six requirements, each VERIFIED / UNVERIFIED / FAILED with its evidence source,
         observation time and explanation. UNVERIFIED is never a pass.
Process:
  REQUIREMENTS   api (A), israel_shipping (B), availability (C), catalog (D), contacts (E), business (F)
  new_record()                 all UNVERIFIED ("no evidence collected yet")
  set_requirement()            records one status + evidence. VERIFIED needs a verifiable evidence kind
                               (official documentation, a tested API call, a fetched catalog, an owner check);
                               a supplier's own claim or a homepage keyword can only make it UNVERIFIED.
  record_supplier_claim()      what a supplier says in a reply: kept as a claim (UNVERIFIED), and a claim that
                               contradicts recorded evidence is flagged, never silently accepted.
  from_sourcing_signals()      maps NIR discovery's homepage keyword signals to UNVERIFIED claims.
  ensure_schema()              any requirement a stored record lacks (v1 records: all dropshipping checks) is added
                               as UNVERIFIED - an old record never passes a check it was never evaluated on.
  classify()                   two independent dimensions -
                               integration: integration_ready | no_api_candidate | needs_investigation | not_qualified;
                               dropshipping: DROPSHIPPING-READY (DROPSHIP_MANDATORY + DROPSHIP_CORE all VERIFIED, no
                               contradiction) | DROPSHIPPING-FAILED (any of them FAILED) | DROPSHIPPING-CANDIDATE.
                               - integration_ready ONLY when all six are VERIFIED;
                               - a FAILED api -> no_api_candidate (business relationship path, never
                                 integration-ready); a FAILED israel_shipping/availability/catalog/contacts/
                                 business -> not_qualified; otherwise anything UNVERIFIED -> needs_investigation.
                               Stale availability evidence (older than AVAILABILITY_MAX_AGE) counts as UNVERIFIED.
  verified_contacts()          only contacts whose own status is VERIFIED (outreach may use nothing else).
  candidate_summary()          the review record presented to the owner.
  load / save                  suppliers.credentials -> 'qualification' (catalog DB; no schema change).
Data Imported/Modified: suppliers.credentials.qualification (JSONB key) only.
Data Sources: NIR discovery, supplier replies (via Email Agent), owner checks.
Last Updated: 2026-10-27
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

VERIFIED, UNVERIFIED, FAILED = "VERIFIED", "UNVERIFIED", "FAILED"
STATUSES = (VERIFIED, UNVERIFIED, FAILED)
REQUIREMENTS = ("api", "israel_shipping", "availability", "catalog", "contacts", "business")
# Dropshipping dimension (owner task 2026-10-27). Separate from API/integration: a non-API supplier can be a
# dropshipping candidate, and an API supplier is not a dropshipper until these are VERIFIED.
DROPSHIP_REQUIREMENTS = ("dropship_direct_to_customer", "dropship_israel_delivery", "dropship_commercial_terms",
                         "dropship_delivery_tracking", "dropship_packaging_identity", "dropship_returns_warranty")
# DROPSHIPPING-READY needs these VERIFIED (+ DROPSHIP_CORE). packaging/identity is recorded and shown to the owner
# but does not block readiness (neutral packaging is a preference, not a capability to ship) - owner may change.
DROPSHIP_MANDATORY = ("dropship_direct_to_customer", "dropship_israel_delivery", "dropship_commercial_terms",
                      "dropship_delivery_tracking", "dropship_returns_warranty")
DROPSHIP_CORE = ("israel_shipping", "availability", "catalog", "contacts", "business")
ALL_REQUIREMENTS = REQUIREMENTS + DROPSHIP_REQUIREMENTS
DROPSHIP_READY, DROPSHIP_CANDIDATE, DROPSHIP_FAILED = "DROPSHIPPING-READY", "DROPSHIPPING-CANDIDATE", "DROPSHIPPING-FAILED"
REQ_LABEL = {"api": "API / official integration", "israel_shipping": "Ships to Israel",
             "availability": "Stock / availability data", "catalog": "Online catalog",
             "contacts": "Official contact channel", "business": "Business identity & commercial terms",
             "dropship_direct_to_customer": "Dropship: ships directly to our customer",
             "dropship_israel_delivery": "Dropship: direct delivery to Israel",
             "dropship_commercial_terms": "Dropship: fees, minimums, payment terms",
             "dropship_delivery_tracking": "Dropship: shipping methods, ETA, tracking",
             "dropship_packaging_identity": "Dropship: neutral packaging / invoices / branding",
             "dropship_returns_warranty": "Dropship: returns, damaged/wrong items, warranty"}
# evidence kinds that can support VERIFIED / FAILED; claims and keywords never can
VERIFIABLE_KINDS = frozenset({"official_documentation", "api_test", "catalog_fetch", "owner_check",
                              "official_policy_page", "shipping_quote"})
CLAIM_KINDS = frozenset({"supplier_claim", "homepage_keyword", "search_result", "none"})
AVAILABILITY_MAX_AGE = timedelta(days=30)
INTEGRATION_READY, NO_API, NEEDS_INVESTIGATION, NOT_QUALIFIED = (
    "integration_ready", "no_api_candidate", "needs_investigation", "not_qualified")
SCHEMA_VERSION = 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def new_record(supplier_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    ts = _iso(now or _now())
    return {"schema": SCHEMA_VERSION, "supplier_id": str(supplier_id), "updated_at": ts,
            "requirements": {k: {"status": UNVERIFIED, "evidence_kind": "none", "source": None,
                                 "observed_at": ts, "note": "no evidence collected yet", "details": {}}
                             for k in ALL_REQUIREMENTS},
            "claims": [], "contradictions": [], "commercial": {}}


def ensure_schema(rec: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Upgrade a stored record in place: every requirement it lacks (e.g. the dropshipping checks on a v1 record)
    becomes UNVERIFIED - never implicitly passed."""
    ts = _iso(now or _now())
    reqs = rec.setdefault("requirements", {})
    for k in ALL_REQUIREMENTS:
        reqs.setdefault(k, {"status": UNVERIFIED, "evidence_kind": "none", "source": None, "observed_at": ts,
                            "note": "no evidence recorded for this requirement", "details": {}})
    rec.setdefault("claims", [])
    rec.setdefault("contradictions", [])
    rec.setdefault("commercial", {})
    rec["schema"] = SCHEMA_VERSION
    return rec


def set_requirement(rec: Dict[str, Any], key: str, status: str, *, evidence_kind: str, source: Optional[str],
                    note: str, details: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None) -> None:
    if key not in ALL_REQUIREMENTS:
        raise ValueError(f"unknown requirement {key!r}")
    ensure_schema(rec)
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    if status in (VERIFIED, FAILED):
        if evidence_kind not in VERIFIABLE_KINDS:
            raise ValueError(f"{status} needs verifiable evidence, not {evidence_kind!r}")
        if not source:
            raise ValueError(f"{status} needs an evidence source (URL or reference)")
    if not note:
        raise ValueError("an explanation is required")
    ts = _iso(now or _now())
    rec["requirements"][key] = {"status": status, "evidence_kind": evidence_kind, "source": source,
                                "observed_at": ts, "note": note[:500], "details": details or {}}
    rec["updated_at"] = ts


def record_supplier_claim(rec: Dict[str, Any], key: str, claim: str, *, value: Optional[bool], source_ref: str,
                          now: Optional[datetime] = None) -> Dict[str, Any]:
    """A supplier's statement (e.g. from an email reply). Never VERIFIED by itself."""
    if key not in ALL_REQUIREMENTS:
        raise ValueError(f"unknown requirement {key!r}")
    ensure_schema(rec)
    ts = _iso(now or _now())
    entry = {"requirement": key, "claim": claim[:500], "value": value, "source_ref": source_ref, "at": ts}
    rec["claims"].append(entry)
    cur = rec["requirements"][key]
    contradicts = value is not None and (
        (cur["status"] == FAILED and value is True) or (cur["status"] == VERIFIED and value is False))
    if contradicts:
        rec["contradictions"].append({**entry, "recorded_status": cur["status"], "recorded_source": cur["source"]})
    elif cur["status"] == UNVERIFIED:
        cur.update(evidence_kind="supplier_claim", source=source_ref, observed_at=ts,
                   note=f"supplier states: {claim[:300]} (needs verification)")
    rec["updated_at"] = ts
    return {"contradiction": contradicts, **entry}


def from_sourcing_signals(supplier_id: str, signals: Dict[str, Any], domain: str,
                          now: Optional[datetime] = None) -> Dict[str, Any]:
    """NIR discovery only reads homepage keywords: every requirement stays UNVERIFIED, with the claim noted."""
    rec = new_record(supplier_id, now)
    src = f"https://{domain}" if domain else None
    pairs = (("api", "has_api", "API / feed / dropship keywords on the homepage"),
             ("israel_shipping", "ships_il", "Israel / international shipping keywords on the homepage"),
             ("catalog", "is_parts_store", "auto-parts keywords on the homepage"))
    for key, sig, text in pairs:
        if signals.get(sig):
            r = rec["requirements"][key]
            r.update(evidence_kind="homepage_keyword", source=src, note=f"{text} (keyword only - not verified)")
    if signals.get("http_ok"):
        rec["requirements"]["business"].update(evidence_kind="homepage_keyword", source=src,
                                               note="website responds over HTTP (identity not verified)")
    return rec


def _effective_status(key: str, req: Dict[str, Any], now: datetime) -> Tuple[str, Optional[str]]:
    st = req.get("status", UNVERIFIED)
    if key == "availability" and st == VERIFIED:
        try:
            seen = datetime.fromisoformat(str(req.get("details", {}).get("data_timestamp") or req["observed_at"]))
        except (TypeError, ValueError):
            return UNVERIFIED, "availability evidence has no readable timestamp"
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        if now - seen > AVAILABILITY_MAX_AGE:
            return UNVERIFIED, "availability evidence is stale"
    return st, None


def classify(rec: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or _now()
    statuses, notes = {}, {}
    for k in REQUIREMENTS:
        st, why = _effective_status(k, rec["requirements"].get(k, {}), now)
        statuses[k] = st
        if why:
            notes[k] = why
    failed = [k for k, s in statuses.items() if s == FAILED]
    unverified = [k for k, s in statuses.items() if s == UNVERIFIED]
    if any(k != "api" for k in failed):
        cls = NOT_QUALIFIED
    elif "api" in failed:
        cls = NO_API
    elif unverified or rec.get("contradictions"):
        cls = NEEDS_INVESTIGATION
    else:
        cls = INTEGRATION_READY
    ensure_schema(rec)
    ds_status = {k: _effective_status(k, rec["requirements"][k], now)[0] for k in DROPSHIP_REQUIREMENTS}
    needed = {**{k: ds_status[k] for k in DROPSHIP_MANDATORY}, **{k: statuses[k] for k in DROPSHIP_CORE}}
    contradicted = {c.get("requirement") for c in rec.get("contradictions") or []}
    if any(v == FAILED for v in needed.values()):
        ds = DROPSHIP_FAILED
    elif all(v == VERIFIED for v in needed.values()) and not (contradicted & set(needed)):
        ds = DROPSHIP_READY
    else:
        ds = DROPSHIP_CANDIDATE
    return {"classification": cls, "statuses": statuses, "failed": failed, "unverified": unverified,
            "contradictions": list(rec.get("contradictions") or []), "notes": notes,
            "integration_ready": cls == INTEGRATION_READY,
            "dropshipping": ds, "dropshipping_ready": ds == DROPSHIP_READY,
            "dropship_statuses": ds_status,
            "dropship_failed": [k for k, v in needed.items() if v == FAILED],
            "dropship_unverified": [k for k, v in needed.items() if v == UNVERIFIED],
            "needs_owner_approval_to_activate": True}


def verified_contacts(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    req = rec["requirements"]["contacts"]
    out = []
    for c in (req.get("details") or {}).get("contacts", []) or []:
        if c.get("status") == VERIFIED and c.get("source") and "@" in str(c.get("address", "")):
            out.append(c)
    return out


def candidate_summary(rec: Dict[str, Any], name: str, website: str, country: str,
                      now: Optional[datetime] = None) -> Dict[str, Any]:
    c = classify(rec, now)
    return {"name": name, "website": website, "country": country, **c,
            "requirements": {k: {**rec["requirements"][k],
                                 "effective_status": c["statuses"].get(k) or c["dropship_statuses"][k]}
                             for k in ALL_REQUIREMENTS},
            "commercial": rec.get("commercial", {}),
            "recommendation": {
                INTEGRATION_READY: "all six requirements verified - may be recommended to the owner for activation",
                NO_API: "no API: business-relationship candidate only, not for system integration",
                NEEDS_INVESTIGATION: "missing or unverified evidence - request the missing information",
                NOT_QUALIFIED: "a mandatory requirement failed - do not recommend",
            }[c["classification"]]}


def summary_text(summary: Dict[str, Any]) -> str:
    mark = {VERIFIED: "✅", UNVERIFIED: "❔", FAILED: "❌"}
    lines = [f"{summary['name']} · {summary['classification']} · {summary['dropshipping']}"]
    for k in ALL_REQUIREMENTS:
        r = summary["requirements"][k]
        lines.append(f"{mark[r['effective_status']]} {REQ_LABEL[k]}: {r['note'][:90]}")
    if summary["contradictions"]:
        lines.append(f"⚠️ {len(summary['contradictions'])} סתירות בין טענות הספק לראיות")
    return "\n".join(lines)


# ── persistence (catalog DB, asyncpg connection like services/supplier_sourcing.py) ────────────────
async def load(conn, supplier_id: str) -> Optional[Dict[str, Any]]:
    raw = await conn.fetchval("SELECT credentials->'qualification' FROM suppliers WHERE id = $1::uuid", supplier_id)
    if raw is None:
        return None
    return ensure_schema(json.loads(raw) if isinstance(raw, str) else raw)


async def save(conn, supplier_id: str, rec: Dict[str, Any]) -> None:
    await conn.execute(
        "UPDATE suppliers SET credentials = jsonb_set(coalesce(credentials,'{}'::jsonb), '{qualification}', "
        "$2::jsonb, true), updated_at = NOW() WHERE id = $1::uuid", supplier_id, json.dumps(rec, default=str))
