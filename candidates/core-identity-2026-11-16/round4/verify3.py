import asyncio,asyncpg,os,json,glob,urllib.request,decimal,sys
DB=os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://')
rows=[]
for f in glob.glob('/app/state/cpie_ownership/overwrite_repair/result_20261117*'):
    rows+=json.load(open(f))['done']
print('done rows',len(rows),'unique',len({r['offer_id'] for r in rows}))
key=os.environ['MEILI_MASTER_KEY']; mu=os.environ['MEILI_URL']
async def m():
    c=await asyncpg.connect(DB); await c.execute("SET default_transaction_read_only=on")
    pg_bad=api_bad=meili_stale=meili_ok=0; api_n=0; ex=[]
    for r in rows:
        l=await c.fetchrow("select sp.price_ils,sp.supplier_url,sp.is_available,pc.min_price_ils from supplier_parts sp join parts_catalog pc on pc.id=sp.part_id where sp.id=$1::uuid",r['offer_id'])
        if str(l['price_ils'])!=r['after']['price_ils'] or l['supplier_url']!=r['after']['supplier_url'] or not l['is_available']: pg_bad+=1
        try:
            rq=urllib.request.Request(f"{mu}/indexes/parts/documents/{r['part_id']}",headers={'Authorization':'Bearer '+key})
            d=json.load(urllib.request.urlopen(rq))
            if l['min_price_ils'] is not None and abs(float(d.get('min_price_ils') or 0)-float(l['min_price_ils']))<0.011: meili_ok+=1
            else: meili_stale+=1; ex.append((r['part_id'],d.get('min_price_ils'),str(l['min_price_ils'])))
        except Exception as e: meili_stale+=1
    print('PG mismatches',pg_bad,'| Meili min_price match',meili_ok,'stale/missing',meili_stale, ex[:3])
asyncio.run(m())
