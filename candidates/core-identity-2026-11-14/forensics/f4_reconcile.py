"""READ-ONLY. Step 4: reconcile the forensic population with production totals."""
import asyncio, asyncpg, os, sys, json, collections
sys.path.insert(0, "/app")
import part_identity
SID = open("/tmp/f/sid.txt").read().strip()
async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"),
                              server_settings={"default_transaction_read_only": "on", "statement_timeout": "900000"})
    R = {}
    R["cpie_offers_total"] = await c.fetchval("SELECT count(*) FROM supplier_parts WHERE supplier_id=$1::uuid", SID)
    R["cpie_offers_qualified(~)"] = await c.fetchval("SELECT count(*) FROM supplier_parts WHERE supplier_id=$1::uuid AND supplier_sku LIKE '%~%'", SID)
    R["cpie_offers_bare"] = R["cpie_offers_total"] - R["cpie_offers_qualified(~)"]
    rows = await c.fetch("""SELECT p.oem_number art, p.name, count(*) over (partition by p.oem_number) n FROM parts_catalog p
                            WHERE p.specifications->>'source'='Car-Parts.ie' AND p.oem_number IS NOT NULL""")
    by = collections.defaultdict(set)
    for r in rows:
        if r["n"] > 1:
            b = part_identity.title_brand(r["name"], r["art"])
            if b: by[r["art"]].add(b)
    multi = {a for a, s in by.items() if len(s) >= 2}
    R["articles_with_>=2_resolved_brands_among_parts"] = len(multi)
    bare = {r["supplier_sku"] for r in await c.fetch("SELECT supplier_sku FROM supplier_parts WHERE supplier_id=$1::uuid AND supplier_sku NOT LIKE '%~%'", SID)}
    R["multi_brand_articles_with_a_bare_offer"] = len(multi & bare)
    m = json.load(open("/tmp/f/manifest.json")); exposed = {r["article"] for r in m["offers"]}
    R["manifest_exposed_offers"] = len(exposed)
    R["multi_brand_bare_offers_NOT_in_manifest(no payload row since 2026-11-14 03:00 UTC -> state predates evidence)"] = len((multi & bare) - exposed)
    R["manifest_offers_whose_article_is_not_multi_brand_by_parts(foreign brand seen only in payload rows)"] = len(exposed - multi)
    json.dump(R, open(sys.argv[1], "w"), indent=1); print(json.dumps(R, indent=1))
asyncio.run(main())
