#!/usr/bin/env python3
"""
Script:  maintenance/nir_record_reply_facts_2026_11_23.py
Purpose: Persist two facts the owner showed from the business Gmail on 2026-11-23 (screenshots), that the system had not stored:
         1. Motor State Distributing replied (13 Nov 2026, Geof Kiekenapp, New Account Manager): "we only drop ship in the US";
            a new customer applies via the website 'become a dealer' tab. Recorded as a SUPPLIER CLAIM (never VERIFIED) on
            israel_shipping + dropship_israel_delivery, contact + address stored, junk candidate name corrected, NIR task closed.
         2. ALVADI OÜ (reg. 11487376, Majaka 10, Tallinn) newsletter footer lists info@alvadi.ee and links alvadi.co.il/en
            (the candidate's website) -> contact recorded VERIFIED (evidence_kind owner_check = the owner's own mailbox).
Data Imported/Modified: suppliers.credentials / name (2 NIR candidate rows), agent_todos (1 row). Sends nothing.
Last Updated: 2026-11-23
"""
import asyncio, json, os, datetime
import asyncpg
import supplier_qualification as Q

MOTOR = "d1fa5cb0-700d-4c6d-ba86-ea0ed2c9f1cf"
ALVADI = "5380cc9c-6f40-4ac8-a417-c4409eb86d80"
TODO = "c582ced9-49ec-459f-9f9a-d84db6e71ff5"
EVID = "gmail:inbox 2026-11-13 14:32 UTC gkiekenapp@motorstate.com (owner screenshot 2026-11-23)"


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    now = datetime.datetime.now(datetime.timezone.utc)
    bak = {}
    for sid in (MOTOR, ALVADI):
        r = await c.fetchrow("SELECT name, credentials FROM suppliers WHERE id=$1::uuid", sid)
        bak[sid] = {"name": r["name"], "credentials": r["credentials"]}
    os.makedirs("/app/state/ebay_repair_backup", exist_ok=True)
    json.dump(bak, open("/app/state/ebay_repair_backup/nir_reply_facts_before_20261123.json", "w"), default=str)

    r = await c.fetchrow("SELECT credentials FROM suppliers WHERE id=$1::uuid", MOTOR)
    cr = json.loads(r["credentials"]) if isinstance(r["credentials"], str) else r["credentials"]
    rec = Q.ensure_schema(cr.get("qualification") or Q.new_record(MOTOR, now), now)
    Q.record_supplier_claim(rec, "israel_shipping", "Motor State: 'we only drop ship in the US'", value=False, source_ref=EVID, now=now)
    Q.record_supplier_claim(rec, "dropship_israel_delivery", "Motor State drop ships only in the US; a new customer must apply via the website 'become a dealer' tab", value=False, source_ref=EVID, now=now)
    cr["qualification"] = rec
    cr["original_candidate_name"] = cr.get("original_candidate_name") or "home - circle track warehouse | performance racing distributor | automotive parts"
    cr["contacts"] = [{"name": "Geof Kiekenapp", "role": "New Account Manager", "email": "gkiekenapp@motorstate.com",
                       "phone": "800.772.2678", "address": "8300 Lane Drive, Watervliet, MI 49098, United States",
                       "channel": "email_reply", "evidence": EVID, "recorded_at": now.isoformat()}]
    cr["next_step"] = "dealer application on motorstate.com ('become a dealer'); US-only dropship per the supplier - owner decision"
    await c.execute("UPDATE suppliers SET name='Motor State Distributing', country='US', credentials=$2::jsonb, updated_at=NOW() WHERE id=$1::uuid", MOTOR, json.dumps(cr))
    await c.execute("UPDATE agent_todos SET status='completed', updated_at=NOW() WHERE id=$1::uuid AND status<>'completed'", TODO)

    r = await c.fetchrow("SELECT credentials FROM suppliers WHERE id=$1::uuid", ALVADI)
    cr = json.loads(r["credentials"]) if isinstance(r["credentials"], str) else r["credentials"]
    src = "owner Gmail inbox, ALVADI newsletter footer 2026-11-23: ALVADI OU reg. 11487376, Majaka 10, Tallinn; info@alvadi.ee; alvadi.co.il/en"
    rec = Q.ensure_schema(cr.get("qualification") or Q.new_record(ALVADI, now), now)
    Q.set_requirement(rec, "contacts", Q.VERIFIED, evidence_kind="owner_check", source=src,
                      note="company mailbox published in the company's own newsletter (links the candidate website)",
                      details={"contacts": [{"address": "info@alvadi.ee", "status": Q.VERIFIED, "source": src}]}, now=now)
    cr["qualification"] = rec
    cr["contact_search"] = {**cr.get("contact_search", {}), "outcome": "found_via_owner_mailbox", "last_attempt": now.isoformat()}
    await c.execute("UPDATE suppliers SET credentials=$2::jsonb, updated_at=NOW() WHERE id=$1::uuid", ALVADI, json.dumps(cr))
    print("done", Q.verified_contacts(rec))

asyncio.run(main())
