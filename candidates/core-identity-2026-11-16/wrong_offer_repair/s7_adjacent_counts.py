"""S7 (read-only): exact current counts for the adjacent populations (mission section 11). Run in autospare_backend."""
import asyncio,os,asyncpg,json,sys,collections,time
sys.path.insert(0,'/app'); import part_identity
CP='88f6ce24-ab5f-4f99-8b41-03cf6828cf3d'
async def main():
    c=await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://')); await c.execute('SET default_transaction_read_only=on'); await c.execute("SET statement_timeout='600s'")
    out={'run_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    out['cpie_offers']=await c.fetchval("select count(*) from supplier_parts where supplier_id=$1::uuid",CP)
    out['cpie_offers_qualified']=await c.fetchval("select count(*) from supplier_parts where supplier_id=$1::uuid and supplier_sku like '%~%'",CP)
    # unresolved holder: bare offers whose holder part title brand cannot be resolved
    unres=[];n=0
    async with c.transaction():
        async for r in c.cursor("select sp.id::text id,sp.supplier_sku sku,p.name from supplier_parts sp join parts_catalog p on p.id=sp.part_id where sp.supplier_id=$1::uuid and sp.supplier_sku not like '%~%'",CP,prefetch=5000):
            n+=1
            if part_identity.title_brand(r['name'],r['sku']) is None: unres.append(r['id'])
    out['bare_offers_scanned']=n; out['unresolved_holder_offers']=len(unres)
    json.dump(unres,open('/tmp/s7_unresolved_holder_ids.json','w'))
    # parts created by the car-parts.ie importer without any offer of the supplier
    out['cpie_parts_without_offer']=await c.fetchval("select count(*) from parts_catalog p where p.specifications->>'source'=(select name from suppliers where id=$1::uuid) and not exists (select 1 from supplier_parts sp where sp.part_id=p.id and sp.supplier_id=$1::uuid)",CP)
    out['cpie_parts_total']=await c.fetchval("select count(*) from parts_catalog p where p.specifications->>'source'=(select name from suppliers where id=$1::uuid)",CP)
    out['cpie_parts_base_eq_importer_x1_45']=await c.fetchval("select count(*) from parts_catalog p where p.specifications->>'source'=(select name from suppliers where id=$1::uuid) and p.importer_price_ils>0 and p.base_price=round(p.importer_price_ils*1.45,2)",CP)
    json.dump(out,open('/tmp/s7_out.json','w')); print(json.dumps(out))
asyncio.run(main())
