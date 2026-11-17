import asyncio,asyncpg,os,sys,json
sys.path.insert(0,'/app')
import base_price_rule as bpr
from types import SimpleNamespace
DSN=os.environ["DATABASE_URL"].replace("postgresql+asyncpg://","postgresql://")
CP='88f6ce24-ab5f-4f99-8b41-03cf6828cf3d'
async def m():
    c=await asyncpg.connect(DSN)
    await bpr.ensure_vat_registry(conn=c); rate=await bpr.get_rate(c); sql=bpr._asyncpg_sql(bpr.inputs_sql(True))
    ids=[r['part_id'] for r in await c.fetch("select distinct part_id::text part_id from supplier_parts where supplier_id=$1::uuid and updated_at>'2026-11-17 03:57:00'",CP)]
    div=[]
    for r in await c.fetch(sql,ids,rate):
        row=SimpleNamespace(**dict(r)); t,s=bpr.target(row,rate)
        if t is not None and round(float(row.base_price or 0),2)!=t and s=='offer': div.append({'part_id':row.id,'before':str(row.base_price),'target':t})
    res=await bpr.apply_to_parts(c,[d['part_id'] for d in div],rate)
    after={r['id']:str(r['base_price']) for r in await c.fetch("select id::text id,base_price from parts_catalog where id=any($1::uuid[])",[d['part_id'] for d in div])}
    for d in div: d['after']=after[d['part_id']]
    json.dump({'result':res,'rows':div},open('/app/state/cpie_ownership/base_price_repair/heal32_20261117.json','w'),indent=1,default=str)
    print(len(div),res)
asyncio.run(m())
