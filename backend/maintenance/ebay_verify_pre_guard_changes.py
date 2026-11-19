#!/usr/bin/env python3
"""
Script:  maintenance/ebay_verify_pre_guard_changes.py
Purpose: READ-ONLY verification of the 16 eBay offers changed by the first refresh run (2026-11-18 21:36 UTC)
         that ran BEFORE the Listing Identity Guard existed. For each offer it compares the OLD listing
         (recovered from the 2026-11-18 04:03 backup) with the CURRENT stored listing, using LIVE eBay data
         (Browse item endpoint by legacy id), and classifies the outcome with listing_identity.evaluate().
Process: 1. read the old-rows TSV (backup extract) + the live supplier_parts row
         2. fetch both listings live (get_item_by_legacy_id, IL context)
         3. print/write one JSON verdict per offer. NO database writes.
Data Imported/Modified: none (report only)
Data Sources: eBay Browse API; backup autospare-20261118.sql.gz (supplier_parts rows)
Missing Data Delegation: an unreachable listing is reported as 'missing', never guessed.
Last Updated: 2026-11-19
"""
import asyncio
import json
import os
import re
import sys

import asyncpg
import httpx

from services.suppliers.ebay_supplier import EbaySupplier

DB = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def legacy_id(url: str) -> str | None:
    m = re.search(r"/itm/(\d{9,})", url or "")
    return m.group(1) if m else None


async def fetch_legacy(client: httpx.AsyncClient, token: str, base: str, lid: str) -> dict:
    r = await client.get(
        f"{base}/buy/browse/v1/item/get_item_by_legacy_id",
        params={"legacy_item_id": lid},
        headers={"Authorization": f"Bearer {token}", "X-EBAY-C-ENDUSERCTX": "contextualLocation=country=IL"},
    )
    if r.status_code != 200:
        return {"http": r.status_code, "error": (r.json().get("errors") or [{}])[0].get("message") if r.headers.get("content-type", "").startswith("application/json") else r.text[:120]}
    j = r.json()
    ship = None
    for o in j.get("shippingOptions") or []:
        ship = o.get("shippingCost", {}).get("value")
        break
    return {
        "http": 200, "title": j.get("title"), "price": j.get("price"), "ship": ship,
        "condition": j.get("condition"), "seller": (j.get("seller") or {}).get("username"),
        "ended": j.get("itemEndDate"), "mpn": next((a.get("value") for a in (j.get("localizedAspects") or []) if "Part Number" in str(a.get("name"))), None),
        "brand": next((a.get("value") for a in (j.get("localizedAspects") or []) if a.get("name") == "Brand"), None),
    }


async def main(old_tsv: str) -> None:
    olds = {}
    with open(old_tsv, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) > 16 and re.match(r"[0-9a-f-]{36}$", p[0]):
                olds[p[0]] = {"old_usd": p[4], "old_ils": p[5], "old_ship_ils": p[7], "old_url": p[16]}
    sup = EbaySupplier()
    token = await sup._get_token()
    conn = await asyncpg.connect(DB)
    out = []
    async with httpx.AsyncClient(timeout=25) as client:
        for sid, o in olds.items():
            cur = await conn.fetchrow(
                "SELECT sp.price_ils, sp.price_usd, sp.shipping_cost_ils, sp.supplier_url, sp.is_available, pc.oem_number, pc.manufacturer, pc.name "
                "FROM supplier_parts sp JOIN parts_catalog pc ON pc.id=sp.part_id WHERE sp.id=$1::uuid", sid)
            lo, ln = legacy_id(o["old_url"]), legacy_id(cur["supplier_url"])
            rec = {"supplier_part_id": sid, "oem": cur["oem_number"], "brand": cur["manufacturer"], "part_name": cur["name"],
                   "old": o, "current": {"price_ils": float(cur["price_ils"]), "ship_ils": float(cur["shipping_cost_ils"] or 0), "url": cur["supplier_url"].split("?")[0]},
                   "same_listing_before_after": lo == ln}
            rec["old_listing_live"] = await fetch_legacy(client, token, sup._base_url, lo) if lo else None
            rec["new_listing_live"] = rec["old_listing_live"] if lo == ln else (await fetch_legacy(client, token, sup._base_url, ln) if ln else None)
            out.append(rec)
            await asyncio.sleep(0.3)
    await conn.close()
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
