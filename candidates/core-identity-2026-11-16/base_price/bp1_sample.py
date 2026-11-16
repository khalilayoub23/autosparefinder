"""READ-ONLY. Sample car-parts.ie-sourced parts and classify each against the AUTHORITATIVE base_price rule
(db_update_agent._base_price_inputs_sql + base_price_target, i.e. the same code apply_base_price_rule uses)."""
import asyncio, os, re, sys, json, collections, time
from types import SimpleNamespace
sys.path.insert(0, '/app')
import asyncpg, db_update_agent as dua
CP = '88f6ce24-ab5f-4f99-8b41-03cf6828cf3d'
PCT = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
RATE = 3.65   # only matters for USD-only offers; car-parts.ie offers carry price_ils
def pos(sql):
    sql = sql.replace(':ids', '$1').replace(':rate', '$2')
    return sql
async def main():
    c = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://'))
    await c.execute('SET default_transaction_read_only=on'); await c.execute("SET statement_timeout='600s'")
    ids = [r['id'] for r in await c.fetch(
        f"SELECT id::text id FROM parts_catalog TABLESAMPLE BERNOULLI({PCT}) WHERE specifications->>'source'=(select name from suppliers where id=$1::uuid) AND is_active", CP)]
    print('sample size', len(ids), flush=True)
    sql = pos(dua._base_price_inputs_sql(True)); cnt = collections.Counter(); ex = collections.defaultdict(list)
    for i in range(0, len(ids), 2000):
        for r in await c.fetch(sql, ids[i:i+2000], RATE):
            row = SimpleNamespace(**dict(r))
            tgt, src = dua.base_price_target(row, RATE)
            bp = None if row.base_price is None else round(float(row.base_price), 2)
            imp = row.importer_price_ils
            sig = imp is not None and float(imp) > 0 and bp == round(float(imp) * 1.45, 2)
            if tgt is None: k = f'no_target:{src}'
            elif bp == tgt: k = 'correct'
            elif bp is None or bp == 0: k = 'missing_base_price'
            else: k = 'DIVERGENT'
            k2 = k + ('|legacy_sig' if sig else '')
            cnt[k2] += 1
            if len(ex[k2]) < 5: ex[k2].append({'id': row.id, 'base': bp, 'target': tgt, 'src': src, 'importer': float(imp) if imp else None, 'offer_ils': float(row.offer_ils) if row.offer_ils else None})
    out = {'sample': len(ids), 'pct': PCT, 'counts': dict(cnt), 'examples': ex, 'run_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    print(json.dumps(out, indent=1, default=str))
    json.dump(out, open('/tmp/bp1_out.json', 'w'), default=str)
asyncio.run(main())
