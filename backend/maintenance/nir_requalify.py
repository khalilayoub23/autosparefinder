"""
Script: nir_requalify.py
Purpose: Evidence-based requalification of NIR candidates that carry no `qualification` record (owner mission
         2026-11-13). Fetches each candidate's OFFICIAL site (home, contact, shipping/policy, API/developer pages),
         records every requirement as VERIFIED / UNVERIFIED / FAILED through supplier_qualification.set_requirement
         (the only writer), and marks irrelevant vendors (software, unrelated, dead domain) `rejected` with the reason.
Process:
  1. select suppliers WHERE credentials.source='nir_sourcing' AND no credentials.qualification
  2. fetch pages with httpx (timeouts, no secrets); keyword scan -> evidence kinds:
       catalog   VERIFIED (catalog_fetch)         product/cart markup on a live store page
       israel    VERIFIED (official_policy_page)  Israel named on the official shipping/policy page; FAILED only on an
                                                  explicit "does not ship outside <country>" statement; else UNVERIFIED
       api       VERIFIED (official_documentation) a documentation/developer/API page that is reachable
       contacts  VERIFIED (official_policy_page)  mailto/phone on the official contact page
       business  FAILED when software vendor / not automotive parts / domain unreachable; VERIFIED never from a fetch
       availability stays UNVERIFIED (needs a tested feed or a supplier statement)
  3. never activates anyone; never touches rows that are already active (records evidence only).
Data Imported/Modified: suppliers.credentials.qualification (+ credentials.status='rejected' for FAILED business).
Data Sources: the candidates' own websites.
Last Updated: 2026-11-13
Usage: python3 /app/maintenance/nir_requalify.py [--apply] [--out /app/state/nir_requalify_report.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

import asyncpg
import httpx

import supplier_qualification as Q
from services.supplier_sourcing import looks_like_software_vendor

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
PARTS = ("auto parts", "car parts", "spare parts", "brake", "oem", "aftermarket", "filters", "rotors", "suspension",
         "engine parts", "truck parts", "automotive")
STORE = ("add to cart", "add-to-cart", "addtocart", "shopping cart", "/cart", "checkout", "product-", "/products/", "sku")
PATHS = {"contact": ("/contact", "/contact-us", "/pages/contact", "/about"),
         "ship": ("/shipping", "/shipping-policy", "/policies/shipping-policy", "/pages/shipping", "/shipping-returns",
                  "/international-shipping", "/help/shipping"),
         "api": ("/api", "/developers", "/developer", "/api-docs", "/pages/api", "/integrations", "/dropship")}
NO_SHIP = re.compile(r"(?:we\s+)?(?:do(?:es)?\s+not|don't|cannot|can't|unable to)\s+ship\s+(?:outside|internationally|to\s+international)|"
                     r"(?:ships?|shipping)\s+(?:only\s+)?(?:within|to)\s+the\s+(?:contiguous\s+)?(?:u\.?s\.?|united states|usa)\s+only|"
                     r"u\.?s\.?\s+(?:shipping\s+)?only", re.I)
IL = re.compile(r"\bisrael\b|\bIL\b(?=[,\s])", re.I)
MAIL = re.compile(r"mailto:([\w.+-]+@[\w-]+\.[\w.-]+)|tel:([+\d][\d\s().-]{6,})", re.I)


async def get(cx, url):
    try:
        r = await cx.get(url)
        if r.status_code < 400 and len(r.text or "") > 200:
            return str(r.url), r.text[:400000]
    except Exception:
        pass
    return None, ""


async def collect(domain: str) -> dict:
    base = f"https://{domain.replace('https://', '').replace('http://', '').strip('/')}"
    ev = {"base": base, "pages": {}}
    async with httpx.AsyncClient(follow_redirects=True, timeout=10, headers=UA) as cx:
        u, home = await get(cx, base + "/")
        if not u:
            u, home = await get(cx, base.replace("https://", "https://www.") + "/")
        ev["home_url"], ev["home"] = u, home
        for kind, paths in PATHS.items():
            for p in paths:
                pu, body = await get(cx, base + p)
                if pu and "/" != pu.replace(base, "").strip():
                    ev["pages"][kind] = (pu, body)
                    break
    return ev


def judge(rec: dict, ev: dict, now) -> tuple[str, str]:
    """Fills rec; returns (verdict, reason). verdict: rejected | qualified_partial | unreachable"""
    home = (ev.get("home") or "").lower()
    hu = ev.get("home_url")
    raw = home
    home = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", raw, flags=re.S)
    home = re.sub(r"<[^>]+>", " ", home)                      # visible text only (scripts/markup are not evidence)
    home_markup = raw
    if not hu or len(home) < 1500:
        # a blocked (403/bot wall), JS-rendered or thin page proves nothing about the business: never a rejection
        Q.set_requirement(rec, "business", Q.UNVERIFIED, evidence_kind="none", source=ev["base"],
                          note="official site not readable by plain HTTP (bot protection / JS-rendered / timeout); "
                               "needs a browser or manual check - NOT evidence against the supplier", now=now)
        return "needs_check", "site not machine-readable; identity unverified"
    soft = looks_like_software_vendor(home_markup)
    store = any(s in home_markup for s in STORE)
    nparts = sum(1 for s in PARTS if s in home or s in home_markup[:60000])
    parts = nparts >= 2
    if soft:
        why = "software/platform vendor (demo/trial/SaaS wording, no store markup)"
        Q.set_requirement(rec, "business", Q.FAILED, evidence_kind="official_policy_page", source=hu, note=why, now=now)
        return "rejected", why
    if not parts:      # includes "no automotive wording": a heuristic is not proof (CARiD, Ortiz were false hits)
        Q.set_requirement(rec, "business", Q.UNVERIFIED, evidence_kind="homepage_keyword", source=hu,
                          note="weak automotive signal on homepage; needs manual review", now=now)
        return "needs_check", "weak automotive signal"
    Q.set_requirement(rec, "business", Q.UNVERIFIED, evidence_kind="homepage_keyword", source=hu,
                      note="automotive parts seller per fetched homepage; legal entity/commercial terms not yet verified", now=now)
    if store:
        Q.set_requirement(rec, "catalog", Q.VERIFIED, evidence_kind="catalog_fetch", source=hu,
                          note="live store page with cart/product markup (online catalog exists; machine-readable access not verified)", now=now)
    shipped = False
    for kind in ("ship",):
        if kind in ev["pages"]:
            pu, body = ev["pages"][kind]
            if NO_SHIP.search(body):
                Q.set_requirement(rec, "israel_shipping", Q.FAILED, evidence_kind="official_policy_page", source=pu,
                                  note="shipping policy states no international/outside-US shipping", now=now)
                shipped = True
            elif IL.search(body):
                Q.set_requirement(rec, "israel_shipping", Q.VERIFIED, evidence_kind="official_policy_page", source=pu,
                                  note="Israel named on the official shipping page", now=now)
                shipped = True
    if not shipped:
        Q.set_requirement(rec, "israel_shipping", Q.UNVERIFIED, evidence_kind="none", source=None,
                          note="no shipping policy naming Israel found; needs supplier statement or quote", now=now)
    if "api" in ev["pages"]:
        pu, body = ev["pages"]["api"]
        if re.search(r"\bapi\b|developer|dropship", body, re.I):
            Q.set_requirement(rec, "api", Q.VERIFIED, evidence_kind="official_documentation", source=pu,
                              note="official API / developer / dropship page reachable (access terms not verified)", now=now)
    cp = ev["pages"].get("contact")
    contacts = sorted({m[0].strip().lower() for m in MAIL.findall(cp[1] if cp else "") if m[0]})[:3]
    if cp and contacts:
        Q.set_requirement(rec, "contacts", Q.VERIFIED, evidence_kind="official_policy_page", source=cp[0],
                          note="contact channel(s) published on the official contact page",
                          details={"contacts": [{"address": c, "status": Q.VERIFIED, "source": cp[0]} for c in contacts]}, now=now)
    Q.set_requirement(rec, "availability", Q.UNVERIFIED, evidence_kind="none", source=None,
                      note="no tested stock feed or supplier statement yet", now=now)
    return "qualified_partial", "automotive parts seller; remaining requirements need supplier information"


# Candidates whose fetched homepage text was read and found NOT to be a parts seller (operator review 2026-11-13).
MANUAL_REJECT = {
    "53c6552b": "general-purpose dropshipping platform (all product categories), not an automotive parts supplier",
    "487745b2": "affiliate-marketing WordPress plugin / publisher tool, not a parts seller",
    "3abd6b6c": "AI ecommerce/dropshipping software tools (AppScenic), not a parts seller",
    "3fb3bb55": "unrelated 'Identity Sovereignty Governance Engine' demonstration page, no automotive products",
    "45ad2417": "dropshipping automation SOFTWARE (Spark Shipping), not a parts seller",
}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="/app/state/nir_requalify_report.json")
    a = ap.parse_args()
    db = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    c = await asyncpg.connect(db)
    rows = await c.fetch("""SELECT id::text id,name,website,is_active,credentials FROM suppliers
        WHERE credentials->>'source'='nir_sourcing' AND NOT (credentials ? 'qualification') ORDER BY created_at""")
    sem = asyncio.Semaphore(8)
    report = []

    async def one(r):
        cr = r["credentials"] if isinstance(r["credentials"], dict) else json.loads(r["credentials"])
        async with sem:
            ev = await collect(r["website"] or "")
        now = datetime.now(timezone.utc)
        rec = Q.new_record(r["id"], now)
        verdict, why = judge(rec, ev, now)
        if r["id"][:8] in MANUAL_REJECT:
            verdict, why = "rejected", MANUAL_REJECT[r["id"][:8]]
            Q.set_requirement(rec, "business", Q.FAILED, evidence_kind="official_policy_page",
                              source=ev.get("home_url") or ev["base"], note="homepage read by operator: " + why, now=now)
        cls = Q.classify(rec, now)
        return r, cr, rec, verdict, why, cls

    for r, cr, rec, verdict, why, cls in await asyncio.gather(*[one(r) for r in rows]):
        reqs = {k: v["status"] for k, v in rec["requirements"].items() if k in Q.REQUIREMENTS}
        report.append({"id": r["id"], "name": r["name"][:50], "website": r["website"], "active": r["is_active"],
                       "verdict": verdict, "reason": why, "classification": cls["classification"],
                       "requirements": reqs})
        if a.apply:
            cr["qualification"] = rec
            protected = r["is_active"] or cr.get("status") != "pending_review"   # existing relationships: evidence only
            if verdict == "rejected" and not protected:
                cr["status"] = "rejected"
                cr["rejected_reason"] = why
                cr["rejected_at"] = datetime.now(timezone.utc).isoformat()
            elif verdict == "qualified_partial" and not protected:
                cr["status"] = "needs_information"
            elif verdict == "needs_check" and not protected:
                cr["status"] = "needs_manual_check"
            await c.execute("UPDATE suppliers SET credentials=$2::jsonb, updated_at=NOW() WHERE id=$1::uuid",
                            r["id"], json.dumps(cr))
    json.dump(report, open(a.out, "w"), indent=1, ensure_ascii=False)
    from collections import Counter
    print(Counter(x["verdict"] for x in report), "applied" if a.apply else "dry-run")
    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
