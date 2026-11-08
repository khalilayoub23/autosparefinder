import asyncio, os, asyncpg
async def main():
    c=await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://'))
    await c.execute("SET statement_timeout='900s'")
    tr=c.transaction(readonly=True,isolation='repeatable_read'); await tr.start()
    with open('/tmp/ph_live_raw.tsv','wb') as f: print(await c.copy_from_query("select id,supplier_part_id,created_at from price_history",output=f))
    with open('/tmp/sp_live.tsv','wb') as f: print(await c.copy_from_query("select id,supplier_id,part_id,supplier_sku,is_available from supplier_parts",output=f))
    await tr.rollback(); await c.close()
asyncio.run(main())
