"""READ-ONLY audit of the 3 post-restart payloads (renault/fuego-136, renault/master-ii-bus-jd, byd/atto-3): which part rows own an offer."""
import json, os, asyncio, asyncpg, sys, collections
sys.path.insert(0, '/app')
import cpie_identity as ci
DB = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')
FILES = {'RENAU': ['/tmp/renault__renault_fuego-136_cpie.json', '/tmp/renault__renault_master-ii-bus-jd_cpie.json'], 'BYD': ['/tmp/byd__byd_atto-3_cpie.json']}
async def main():
    c = await asyncpg.connect(DB); await c.execute("SET default_transaction_read_only=on")
    sid = await c.fetchval("select id from suppliers where name ilike 'car-parts%' limit 1")
    out = []
    for make, fs in FILES.items():
        for f in fs:
            d = json.load(open(f)); ps = d['parts']
            for it in ps:
                art = str(it.get('inferred_sku') or it.get('sku')); brand = ci.brand_of(it['name'], art)
                part = await c.fetchrow("select id::text, sku, name from parts_catalog where sku=$1 or sku=$2", f"{make}-{art}", f"{make}-{ci._tok(brand)}~{art}" if brand else '-')
                offs = await c.fetch("select sp.id::text, sp.supplier_sku, sp.part_id::text, sp.price_ils, sp.supplier_url, p.sku psku from supplier_parts sp join parts_catalog p on p.id=sp.part_id where sp.supplier_id=$1 and (sp.supplier_sku=$2 or sp.supplier_sku=$3 or sp.supplier_sku like $4)", sid, art, f"{ci._tok(brand)}~{art}" if brand else '-', f"%@%{art}")
                own = [o for o in offs if part and o['part_id'] == part['id']]
                out.append(dict(file=f.split('/')[-1], article=art, brand=brand, part=dict(part) if part else None,
                    has_own_offer=bool(own), holder=[dict(o) for o in offs if not own][:1], n_offers_for_article=len(offs)))
    json.dump(out, open('/tmp/audit754.json', 'w'), default=str)
    cnt = collections.Counter((o['file'], o['part'] is not None, o['has_own_offer'], o['brand'] is not None) for o in out)
    for k, v in sorted(cnt.items(), key=str): print(k, v)
asyncio.run(main())
