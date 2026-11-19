#!/usr/bin/env python3
"""
Script:  maintenance/nir_record_contacts.py
Purpose: Record business contact addresses read from a supplier's OFFICIAL contact page (observed in a real browser, which
         the blocked server IP cannot do) as VERIFIED `contacts` evidence on the NIR qualification record, so the existing
         outreach gates (supplier_outreach.plan -> verified_contacts) can send the authorized verification inquiry.
Process: input JSON {"<supplier id prefix>": {"source": "<official contact page URL>", "contacts": ["a@b.com", ...]}}
         1. the address must pass supplier_qualification.valid_email (syntax, no placeholder domain)
         2. its domain must equal / be a subdomain of the supplier's own website domain (never a guessed or third-party address)
         3. the contacts requirement becomes VERIFIED (evidence_kind official_policy_page, source URL, timestamp)
Data Imported/Modified: suppliers.credentials.qualification.requirements.contacts only. No email is sent here.
Data Sources: the official contact page named in `source`.
Missing Data Delegation: an address that fails a check is reported and NOT stored.
Last Updated: 2026-11-19
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import asyncpg

import supplier_qualification as Q


def _domain(url: str) -> str:
    host = (urlparse(url if "//" in url else "//" + url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


async def main(path: str, apply: bool) -> None:
    spec = json.load(open(path))
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    for prefix, item in spec.items():
        row = await c.fetchrow("SELECT id::text id, website, credentials FROM suppliers WHERE id::text LIKE $1 || '%' AND credentials->>'source'='nir_sourcing'", prefix)
        if not row:
            print(prefix, "NOT FOUND"); continue
        site = _domain(row["website"])
        ok, bad = [], []
        for a in item["contacts"]:
            a = a.strip().lower()
            d = a.rsplit("@", 1)[-1]
            (ok if Q.valid_email(a) and (d == site or d.endswith("." + site)) else bad).append(a)
        print(prefix, site, "accepted:", ok, "rejected:", bad)
        if not apply or not ok:
            continue
        cr = json.loads(row["credentials"]) if isinstance(row["credentials"], str) else row["credentials"]
        now = datetime.now(timezone.utc)
        rec = Q.ensure_schema(cr.get("qualification") or Q.new_record(row["id"], now), now)
        Q.set_requirement(rec, "contacts", Q.VERIFIED, evidence_kind="official_policy_page", source=item["source"],
                          note="contact channel(s) published on the official contact page (read in a browser 2026-11-19)",
                          details={"contacts": [{"address": x, "status": Q.VERIFIED, "source": item["source"]} for x in ok]}, now=now)
        cr["qualification"] = rec
        await c.execute("UPDATE suppliers SET credentials=$2::jsonb, updated_at=NOW() WHERE id=$1::uuid", row["id"], json.dumps(cr))
    await c.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], "--apply" in sys.argv))
