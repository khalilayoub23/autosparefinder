"""Read-only: evidence for the 59 part-level identity cases (v4 H_UNRESOLVED with v3_note 'part-level identity decision required')."""
import asyncio, asyncpg, json, os, sys, re
sys.path.insert(0, "/app")
import part_identity as pi
DB = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
cls = json.load(open("/app/state/cpie_ownership/classification_1043_final_v4.json"))
cases = [r for r in cls if r["disposition"] == "H_UNRESOLVED" and "part-level identity" in (r.get("v3_note") or "")]
async def main():
    c = await asyncpg.connect(DB)
    await c.execute("SET default_transaction_read_only=on")
    out = []
    for r in cases:
        p = await c.fetchrow("select id::text id,name,oem_number,part_type,manufacturer,manufacturer_id::text mid,sku,specifications->>'source' src,specifications->>'product_url' purl,is_active from parts_catalog where id=$1::uuid", r["part_id"])
        offers = await c.fetch("select sp.id::text id,sp.supplier_sku,sp.price_ils,sp.supplier_url,sp.part_type,sp.is_available,s.name sname from supplier_parts sp join suppliers s on s.id=sp.supplier_id where sp.part_id=$1::uuid", r["part_id"])
        norm = r["article"].upper().replace(" ", "").replace("-", "")
        sib = await c.fetch("select id::text id,name,oem_number,part_type,manufacturer,manufacturer_id::text mid from parts_catalog where is_active and id<>$1::uuid and REPLACE(REPLACE(UPPER(oem_number),' ',''),'-','')=$2 limit 30", r["part_id"], norm)
        pd = dict(p) if p else None
        verdicts = []
        for s in sib:
            v = pi.classify({"name": pd["name"], "oem_number": pd["oem_number"], "part_type": pd["part_type"], "manufacturer_id": pd["mid"], "manufacturer": pd["manufacturer"]},
                            {"name": s["name"], "oem_number": s["oem_number"], "part_type": s["part_type"], "manufacturer_id": s["mid"], "manufacturer": s["manufacturer"]})
            verdicts.append({"sibling": dict(s), "verdict": v.decision, "reason": v.reason})
        out.append({"offer_id": r["offer_id"], "part_id": r["part_id"], "article": r["article"], "owner_brand": r["owner_brand"], "foreign_brands": r["foreign_brands"], "group": r["consultant_group"],
                    "part": pd, "title_brand": pi.title_brand(pd["name"], pd["oem_number"]) if pd else None, "offers": [dict(o, price_ils=str(o["price_ils"])) for o in offers], "siblings": verdicts})
    json.dump(out, open("/app/state/cpie_ownership/identity_cases_20261117.json", "w"), default=str, indent=1)
    import collections
    print(len(out), collections.Counter((o["title_brand"] or "?") == o["owner_brand"] for o in out))
    print(collections.Counter(v["verdict"] for o in out for v in o["siblings"]), "no-sibling:", sum(1 for o in out if not o["siblings"]))
asyncio.run(main())
