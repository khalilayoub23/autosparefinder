#!/usr/bin/env python3
"""
Script:  maintenance/nir_outreach_status.py
Purpose: READ-ONLY accounting of every NIR sourcing candidate: qualification status, verified contacts, outreach history
         (sent / replied / failed / cancelled) and the supplier-flag switches in the running process. No writes, no emails.
Process: 1. read suppliers (credentials->source='nir_sourcing') from the catalog DB
         2. read supplier_outreach from the PII DB, group per supplier
         3. print a JSON report (counts + one row per candidate)
Data Imported/Modified: none
Data Sources: suppliers (catalog DB), supplier_outreach (PII DB), supplier_flags
Missing Data Delegation: a candidate with no VERIFIED contact is listed as 'no_verified_contact' (never guessed).
Last Updated: 2026-11-19
"""
import asyncio
import collections
import json
import os

import asyncpg

import supplier_flags
import supplier_qualification as q


def _dsn(name: str) -> str:
    return os.environ[name].replace("postgresql+asyncpg://", "postgresql://")


async def main() -> None:
    cat = await asyncpg.connect(_dsn("DATABASE_URL"))
    pii = await asyncpg.connect(_dsn("DATABASE_PII_URL"))
    rows = await cat.fetch("SELECT id::text, name, is_active, credentials->>'status' st, credentials->'qualification' qual "
                           "FROM suppliers WHERE credentials->>'source'='nir_sourcing' ORDER BY name")
    out = await pii.fetch("SELECT supplier_id::text sid, state, attempt, recipient, sent_at, reply_at FROM supplier_outreach")
    by = collections.defaultdict(list)
    for r in out:
        by[r["sid"]].append(dict(r))
    report, tally = [], collections.Counter()
    for r in rows:
        qual = json.loads(r["qual"]) if isinstance(r["qual"], str) else r["qual"]
        vc = q.verified_contacts(qual) if qual else []
        hist = by.get(r["id"], [])
        states = collections.Counter(h["state"] for h in hist)
        if states.get("REPLY_RECEIVED"):
            bucket = "replied"
        elif states.get("AWAITING_REPLY"):
            bucket = "sent_awaiting_reply"
        elif not vc:
            bucket = "no_verified_contact"
        else:
            bucket = "verified_contact_not_yet_contacted"
        tally[bucket] += 1
        report.append({"id": r["id"], "name": (r["name"] or "")[:60], "status": r["st"], "active": r["is_active"],
                       "verified_contacts": len(vc), "outreach": dict(states), "bucket": bucket})
    print(json.dumps({"flags": supplier_flags.snapshot(), "candidates": len(rows), "buckets": dict(tally),
                      "outreach_total": len(out), "outreach_states": dict(collections.Counter(o["state"] for o in out)),
                      "rows": report}, indent=1, default=str))
    await cat.close()
    await pii.close()


asyncio.run(main())
