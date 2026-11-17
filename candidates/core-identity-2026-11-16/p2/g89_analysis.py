"""READ-ONLY: for the 89 preserved (G) offers, compare snapshot vs live; for changed ones attribute the new price to a brand via payload files + price_history."""
import json, os, asyncio, asyncpg, glob, sys, collections
sys.path.insert(0, '/app')
import cpie_identity as ci
DB = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')
g = json.load(open('/tmp/g89.json'))
async def main():
    c = await asyncpg.connect(DB); await c.execute("SET default_transaction_read_only=on")
    ph_cols = [r[0] for r in await c.fetch("select column_name from information_schema.columns where table_name='price_history'")]
    # payload index: article -> [(brand, price_ils, file)]
    idx = collections.defaultdict(list)
    arts = {r['article'] for r in g}
    for f in glob.glob('/tmp/*_cpie.json'):
        try: ps = json.load(open(f))['parts']
        except Exception: continue
        for p in ps:
            a = str(p.get('inferred_sku') or p.get('sku'))
            if a in arts and p.get('price_eur'):
                idx[a].append((ci.brand_of(p.get('name') or a, a), round(float(p['price_eur']) * 3.48, 2), f.split('/')[-1]))
    out = []
    for r in g:
        o = await c.fetchrow("select price_ils::float p, supplier_url u, updated_at, supplier_sku from supplier_parts where id=$1::uuid", r['offer_id'])
        snap = r['current_at_snapshot']
        changed = o is None or round(o['p'] or 0, 2) != round(snap['price'], 2) or o['u'] != snap['url']
        row = dict(offer_id=r['offer_id'], article=r['article'], owner_brand=r['owner_brand'], foreign=r['foreign_brands'], changed=changed,
                   snap_price=snap['price'], live_price=o['p'] if o else None, live_sku=o['supplier_sku'] if o else None, updated_at=str(o['updated_at']) if o else None)
        if changed:
            cands = idx.get(r['article'], [])
            row['payload_matches'] = [x for x in cands if o and abs(x[1] - (o['p'] or 0)) < 0.02]
            row['payload_all'] = cands[:6]
            if 'part_id' in ph_cols:
                row['history'] = [dict(x) for x in await c.fetch("select * from price_history where supplier_part_id=$1::uuid order by 1 desc limit 3", r['offer_id'])] if 'supplier_part_id' in ph_cols else []
        out.append(row)
    json.dump(out, open('/tmp/g89_out.json', 'w'), default=str, indent=1)
    ch = [x for x in out if x['changed']]
    print('total', len(out), 'changed', len(ch))
    verdicts = collections.Counter()
    for x in ch:
        brands = {m[0] for m in x['payload_matches']}
        v = 'OWNER_BRAND_PAYLOAD' if x['owner_brand'] in brands else ('FOREIGN_PAYLOAD' if brands & set(x['foreign']) else ('NO_PAYLOAD_MATCH' if not brands else 'OTHER_BRAND'))
        x['verdict'] = v; verdicts[v] += 1
    print(verdicts)
    for x in ch[:25]: print(x['article'], x['owner_brand'], x['snap_price'], '->', x['live_price'], x.get('verdict'), [m[:2] for m in x['payload_matches']][:3], x['live_sku'])
    json.dump(out, open('/tmp/g89_out.json', 'w'), default=str, indent=1)
asyncio.run(main())
