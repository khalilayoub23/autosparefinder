import asyncio,asyncpg,os,sys,json
sys.path.insert(0,'/app')
import base_price_rule as bpr
from types import SimpleNamespace
from decimal import Decimal
DSN=os.environ["DATABASE_URL"].replace("postgresql+asyncpg://","postgresql://")
J=sys.argv[1]
d={}
for l in open(J):
    x=json.loads(l)
    if x['state']=='committed': d[x['part_id']]=x
async def m():
    c=await asyncpg.connect(DSN); await c.execute("SET default_transaction_read_only=on")
    await bpr.ensure_vat_registry(conn=c); rate=await bpr.get_rate(c); sql=bpr._asyncpg_sql(bpr.inputs_sql(True))
    ids=list(d); ok=bad=0
    for i in range(0,len(ids),2000):
        for r in await c.fetch(sql,ids[i:i+2000],rate):
            row=SimpleNamespace(**dict(r)); t,_=bpr.target(row,rate)
            if t is not None and round(float(row.base_price),2)==t and Decimal(str(row.base_price))==Decimal(d[row.id]['after']): ok+=1
            else: bad+=1
    print('committed',len(d),'independently equal to rule + journal:',ok,'other:',bad)
asyncio.run(m())
