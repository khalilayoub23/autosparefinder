import asyncio,asyncpg,os,sys,json,glob
sys.path.insert(0,'/app')
import base_price_rule as bpr
from types import SimpleNamespace
DSN=os.environ["DATABASE_URL"].replace("postgresql+asyncpg://","postgresql://")
CP='88f6ce24-ab5f-4f99-8b41-03cf6828cf3d'
async def m():
    c=await asyncpg.connect(DSN); await c.execute("SET default_transaction_read_only=on")
    await bpr.ensure_vat_registry(conn=c); rate=await bpr.get_rate(c); sql=bpr._asyncpg_sql(bpr.inputs_sql(True))
    ids=[r['part_id'] for r in await c.fetch("select distinct part_id::text part_id from supplier_parts where supplier_id=$1::uuid and updated_at>'2026-11-17 03:57:00'",CP)]
    cnt={'correct':0,'divergent':0,'no_target':0}; legacy=0
    for i in range(0,len(ids),2000):
        for r in await c.fetch(sql,ids[i:i+2000],rate):
            row=SimpleNamespace(**dict(r)); t,s=bpr.target(row,rate)
            if t is None: cnt['no_target']+=1
            elif round(float(row.base_price or 0),2)==t: cnt['correct']+=1
            else:
                cnt['divergent']+=1
                if row.importer_price_ils and round(float(row.importer_price_ils)*1.45,2)==round(float(row.base_price or 0),2): legacy+=1
    print('parts touched by the post-restart harvest import:',len(ids),cnt,'legacy-signature among divergent:',legacy)
    rows=[]
    for f in glob.glob('/app/state/cpie_ownership/overwrite_repair/result_20261117*'): rows+=json.load(open(f))['done']
    bad=0
    for r in rows:
        l=await c.fetchrow("select price_ils,supplier_url,is_available from supplier_parts where id=$1::uuid",r['offer_id'])
        if str(l['price_ils'])!=r['after']['price_ils'] or l['supplier_url']!=r['after']['supplier_url'] or not l['is_available']: bad+=1
    print('835 repaired offers still equal to the repair value:',len(rows)-bad,'| changed since (legit re-harvest or re-poison?):',bad)
asyncio.run(m())
