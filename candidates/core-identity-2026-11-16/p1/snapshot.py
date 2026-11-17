"""READ-ONLY snapshot of offer ownership for the 3 post-restart payloads. usage: snapshot.py OUT.json"""
import json, os, asyncio, asyncpg, sys, hashlib
sys.path.insert(0, '/app')
import cpie_identity as ci
DB = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')
FILES = [('RENAU', '/tmp/renault__renault_fuego-136_cpie.json'), ('RENAU', '/tmp/renault__renault_master-ii-bus-jd_cpie.json'), ('BYD', '/tmp/byd__byd_atto-3_cpie.json')]
async def main(out):
    c = await asyncpg.connect(DB); await c.execute("SET default_transaction_read_only=on")
    sid = await c.fetchval("select id from suppliers where name ilike 'car-parts%' order by created_at limit 1")
    rows = []
    for make, f in FILES:
        d = json.load(open(f)); sha = hashlib.sha256(open(f, 'rb').read()).hexdigest()
        items = []
        for it in d['parts']:
            art = str(it.get('inferred_sku') or it.get('sku')); b = ci.brand_of(it.get('name') or art, art)
            tok = ci._tok(b) if b else None
            items.append((art, b, tok, it.get('name')))
        psk = [f"{make}-{a}" for a, b, t, n in items] + [f"{make}-{t}~{a}" for a, b, t, n in items if t]
        parts = {r['sku']: r for r in await c.fetch("select id::text, sku, name, category from parts_catalog where sku=any($1)", psk)}
        keys = []
        for a, b, t, n in items:
            keys += [a, f"{make}@{a}"] + ([f"{t}~{a}", f"{make}@{t}~{a}"] if t else [])
        offs = await c.fetch("select id::text oid, supplier_sku, part_id::text pid, price_ils::float price, supplier_url, is_available from supplier_parts where supplier_id=$1 and supplier_sku=any($2)", sid, keys)
        byp = {}
        for o in offs: byp.setdefault(o['pid'], []).append(dict(o))
        for a, b, t, n in items:
            p = parts.get(f"{make}-{a}") or (parts.get(f"{make}-{t}~{a}") if t else None)
            rows.append(dict(file=f.split('/')[-1], payload_sha256=sha, article=a, brand=b, name=n, part=dict(p) if p else None,
                             own_offers=byp.get(p['id'], []) if p else [], make=make))
    json.dump(rows, open(out, 'w'), default=str, indent=1)
    own = sum(1 for r in rows if r['part'] and r['own_offers']); nop = sum(1 for r in rows if not r['part'])
    print('items', len(rows), 'with own offer', own, 'no part row', nop, 'part but no own offer', len(rows) - own - nop)
    for fn in sorted({r['file'] for r in rows}):
        rr = [r for r in rows if r['file'] == fn]
        print(fn, len(rr), 'own', sum(1 for r in rr if r['part'] and r['own_offers']))
asyncio.run(main(sys.argv[1]))
