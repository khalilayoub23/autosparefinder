#!/usr/bin/env python3
"""
Script:  maintenance/record_sng_registered_office.py
Purpose: Record SNG Barratt's REGISTERED OFFICE on its supplier row (2026-11-23, owner-supplied address).
Process: 1. back up suppliers.shipping_info  2. merge key `registered_office` with provenance (idempotent).
Data Imported/Modified: suppliers.shipping_info (SNG Barratt row only). NEVER writes `shipping_info.address`
         (that key is what the Eurosender pickup path reads - a registered office is not a verified pickup point).
Data Sources: owner message (Heritage Building address); Companies House S.N.G. Barratt Ltd 03273655 (FIXES_TRACKER 2026-11-20).
Missing Data Delegation: pickup address -> NIR `pickup_address` inquiry to SNG (reply pending).
Last Updated: 2026-11-23
"""
import asyncio, json, os, sys, datetime
import asyncpg

SNG_ID = "6b6aea48-8d7b-44b9-8578-8b411c9159cf"
OFFICE = {"street": "The Heritage Building, Stourbridge Road", "city": "Bridgnorth", "region": "Shropshire",
          "zip": "WV15 6AP", "country": "GB", "kind": "registered_office", "pickup_verified": False,
          "source": "owner-supplied address; Companies House S.N.G. Barratt Ltd 03273655",
          "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}

async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    row = await c.fetchrow("SELECT name, shipping_info FROM suppliers WHERE id=$1::uuid", SNG_ID)
    if not row or "barratt" not in row["name"].lower():
        sys.exit("SNG Barratt row not found - nothing written")
    si = row["shipping_info"]
    si = json.loads(si) if isinstance(si, str) else (si or {})
    os.makedirs("/app/state/ebay_repair_backup", exist_ok=True)
    json.dump(si, open(f"/app/state/ebay_repair_backup/sng_shipping_info_before_{datetime.date.today()}.json", "w"))
    if si.get("registered_office", {}).get("zip") == OFFICE["zip"]:
        print("already recorded"); return
    si["registered_office"] = OFFICE
    assert "address" not in si, "pickup key must not be set here"
    await c.execute("UPDATE suppliers SET shipping_info=$1::jsonb, updated_at=NOW() WHERE id=$2::uuid", json.dumps(si), SNG_ID)
    print("recorded registered_office; pickup key untouched")

asyncio.run(main())
