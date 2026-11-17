import asyncio, asyncpg, os, sys, uuid, collections
sys.path.insert(0, "/app")
import base_price_rule as bpr
from types import SimpleNamespace
DSN = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
async def main():
    c = await asyncpg.connect(DSN); await c.execute("SET default_transaction_read_only=on"); await c.execute("SET statement_timeout='60s'")
    await bpr.ensure_vat_registry(conn=c)
    rate = await bpr.get_rate(c); sql = bpr._asyncpg_sql(bpr.inputs_sql(True))
    cnt = collections.Counter(); per = collections.defaultdict(collections.Counter)
    for _ in range(12):
        start = str(uuid.uuid4())
        ids = [str(r["id"]) for r in await c.fetch("select id from parts_catalog where is_active and id > $1::uuid order by id limit 800", start)]
        for r in await c.fetch(sql, ids, rate):
            row = SimpleNamespace(**dict(r)); tgt, src = bpr.target(row, rate)
            cur = None if row.base_price is None else round(float(row.base_price), 2)
            k = "no_target" if tgt is None else ("correct" if cur == tgt else "divergent")
            sup = row.offer_supplier or ("(estimate)" if src == "estimate" else "(none)")
            cnt[k] += 1; per[sup][k] += 1
    print(dict(cnt), "rate", rate)
    for s, v in sorted(per.items(), key=lambda x: -sum(x[1].values()))[:20]: print(f"{s[:40]:40} {dict(v)}")
asyncio.run(main())
