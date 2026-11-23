"""
Script:  services/nir_contact_discovery.py
Purpose: NIR's CONTINUOUS contact enrichment (2026-11-23): find a verified business e-mail for NIR candidates that have none,
         from the supplier's OWN website only, so the existing authorized outreach loop (supplier_outreach) can contact them.
Process: 1. pick candidates without a verified contact whose last search is older than RETRY_AFTER (checkpoint in
            suppliers.credentials.contact_search, so work survives restarts and is never repeated needlessly)
         2. plain HTTP GET of home + contact/about/wholesale/dealer pages (NO FlareSolverr / browser)
         3. keep only GENERIC business mailboxes on the supplier's own domain (never privacy/legal/careers/personal)
         4. record them VERIFIED with source URL + timestamp via supplier_qualification (same rules as nir_record_contacts)
         5. no address found -> store the exact outcome (no_email_published / unreachable) with the pages tried
Data Imported/Modified: suppliers.credentials (qualification.requirements.contacts, contact_search). Sends nothing.
Data Sources: the supplier's official website pages.
Missing Data Delegation: forms/phone-only sites stay recorded as such; no form is submitted, no address is guessed.
Last Updated: 2026-11-23
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

RETRY_AFTER = timedelta(days=int(os.getenv("NIR_CONTACT_RETRY_DAYS", "7")))
PAGES = ("", "/contact", "/contact-us", "/contactus", "/about", "/about-us", "/wholesale", "/dealers", "/trade", "/privacy-policy", "/terms")
GENERIC_LOCALS = {"info", "sales", "parts", "contact", "hello", "support", "service", "orders", "order", "wholesale", "dealer",
                  "dealers", "enquiries", "enquiry", "inquiries", "inquiry", "trade", "customerservice", "customercare",
                  "office", "mail", "b2b", "business", "partners", "partnerships", "accounts"}
_NON_SUPPLIER_NAME = re.compile(r"dropship|saas|software|automation|integration software", re.I)
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def site_domain(website: str) -> str:
    host = (urlparse(website if "//" in (website or "") else "//" + (website or "")).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def pick_contacts(text: str, domain: str) -> List[str]:
    """Generic business mailboxes on the supplier's own domain, from page text. Pure function (tested)."""
    import supplier_qualification as Q
    out: List[str] = []
    for a in _EMAIL_RE.findall(text or ""):
        a = a.lower().strip(".")
        local, _, d = a.partition("@")
        if not (d == domain or d.endswith("." + domain)):
            continue
        if local not in GENERIC_LOCALS or not Q.valid_email(a):
            continue
        if a not in out:
            out.append(a)
    return out[:3]


def _fetch(url: str) -> Optional[str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AutoSpareFinder-NIR/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.read(600_000).decode("utf-8", "ignore")
    except Exception:
        return None


def search_site(website: str) -> Dict[str, Any]:
    domain = site_domain(website)
    base = f"https://{domain}" if domain else ""
    found: Dict[str, str] = {}
    tried, reached = [], 0
    for p in PAGES:
        url = urljoin(base + "/", p.lstrip("/")) if p else base
        html = _fetch(url)
        tried.append(url)
        if html is None:
            continue
        reached += 1
        from services.supplier_sourcing import looks_like_software_vendor
        if looks_like_software_vendor(html):          # dropship-automation SaaS etc. is not a parts supplier
            return {"outcome": "non_supplier_software_vendor", "contacts": {}, "pages_tried": tried, "reached": reached}
        html = html.replace("&#64;", "@").replace("[at]", "@").replace("(at)", "@")
        for a in pick_contacts(html, domain):
            found.setdefault(a, url)
        if found:
            break
    outcome = "found" if found else ("no_email_published" if reached else "unreachable")
    return {"outcome": outcome, "contacts": found, "pages_tried": tried, "reached": reached}


def _due(cs: Optional[dict], now: datetime) -> bool:
    if not cs or not cs.get("last_attempt"):
        return True
    try:
        return now - datetime.fromisoformat(cs["last_attempt"]) >= RETRY_AFTER
    except Exception:
        return True


async def run_contact_cycle(batch: int = 3, conn=None) -> Dict[str, Any]:
    """One bounded pass over candidates without a verified contact. Returns a summary; never raises on a single supplier."""
    import asyncpg
    import supplier_qualification as Q
    own = conn is None
    if own:
        conn = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    now = datetime.now(timezone.utc)
    summary = {"checked": 0, "found": 0, "no_email": 0, "unreachable": 0}
    try:
        rows = await conn.fetch("""SELECT id::text id, name, website, credentials FROM suppliers
            WHERE credentials->>'source'='nir_sourcing' AND coalesce(credentials->>'status','') <> 'rejected'
              AND website IS NOT NULL AND website <> '' ORDER BY updated_at ASC""")
        done = 0
        for r in rows:
            if done >= batch:
                break
            cr = json.loads(r["credentials"]) if isinstance(r["credentials"], str) else r["credentials"]
            qual = cr.get("qualification")
            if qual and Q.verified_contacts(qual):
                continue
            if _NON_SUPPLIER_NAME.search(r["name"] or ""):   # dropship/automation software is not a parts supplier
                continue
            if not _due(cr.get("contact_search"), now):
                continue
            done += 1
            res = await asyncio.to_thread(search_site, r["website"])
            summary["checked"] += 1
            cr["contact_search"] = {"last_attempt": now.isoformat(), "outcome": res["outcome"],
                                    "pages_tried": res["pages_tried"][:12], "reached": res["reached"]}
            if res["contacts"]:
                src = next(iter(res["contacts"].values()))
                rec = Q.ensure_schema(qual or Q.new_record(r["id"], now), now)
                Q.set_requirement(rec, "contacts", Q.VERIFIED, evidence_kind="official_policy_page", source=src,
                                  note=f"generic business mailbox published on the official site (observed {now.date()})",
                                  details={"contacts": [{"address": a, "status": Q.VERIFIED, "source": s}
                                                        for a, s in res["contacts"].items()]}, now=now)
                cr["qualification"] = rec
                summary["found"] += 1
            else:
                summary["no_email" if res["outcome"] == "no_email_published" else "unreachable"] += 1
            await conn.execute("UPDATE suppliers SET credentials=$2::jsonb, updated_at=NOW() WHERE id=$1::uuid",
                               r["id"], json.dumps(cr))
    finally:
        if own:
            await conn.close()
    return summary
