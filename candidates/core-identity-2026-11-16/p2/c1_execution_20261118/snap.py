import asyncio, asyncpg, os, json
DB=os.environ["DATABASE_URL"].replace("postgresql+asyncpg://","postgresql://")
async def main():
    c=await asyncpg.connect(DB); await c.execute("SET default_transaction_read_only=on")
    d="/app/state/c1_cleanup/"
    mis=json.load(open(d+"mis_ids.json")); sp=json.load(open(d+"scope_pids.json")); dl=json.load(open(d+"del_ids.json"))
    pids=sorted({r["part_id"] for b in ("c1_apply_1_ice_c1_44","c1_apply_2_ice_extra7") for r in json.load(open(d+b+"/backup_pvf.json")).values()})
    o={"affected_pids":len(pids)}
    md=lambda q,*a: c.fetchval(f"SELECT md5(coalesce(string_agg(x,'|' ORDER BY x),'')) FROM ({q}) s(x)",*a)
    o["mis_rows"]=await c.fetchval("SELECT count(*) FROM part_vehicle_fitment WHERE id=ANY($1::uuid[])",mis)
    o["mis_md5"]=await md("SELECT t::text FROM part_vehicle_fitment t WHERE id=ANY($1::uuid[])",mis)
    o["del_present"]=await c.fetchval("SELECT count(*) FROM part_vehicle_fitment WHERE id=ANY($1::uuid[])",dl)
    o["pvf_other_rows_of_affected_parts"]=await c.fetchval("SELECT count(*) FROM part_vehicle_fitment WHERE part_id=ANY($1::uuid[]) AND NOT id=ANY($2::uuid[])",pids,dl)
    o["pvf_other_md5"]=await md("SELECT t::text FROM part_vehicle_fitment t WHERE part_id=ANY($1::uuid[]) AND NOT id=ANY($2::uuid[])",pids,dl)
    o["parts_md5_excl_compat"]=await md("SELECT id::text||'|'||coalesce(sku,'')||'|'||coalesce(base_price::text,'')||'|'||coalesce(is_active::text,'')||'|'||coalesce(name,'') FROM parts_catalog WHERE id=ANY($1::uuid[])",pids)
    o["sp_count"]=await c.fetchval("SELECT count(*) FROM supplier_parts WHERE part_id=ANY($1::uuid[])",pids)
    o["sp_md5"]=await md("SELECT t::text FROM supplier_parts t WHERE part_id=ANY($1::uuid[])",pids)
    o["orderitems_ref"]=await c.fetchval("SELECT count(*) FROM supplier_parts WHERE part_id=ANY($1::uuid[]) AND id IN (SELECT supplier_part_id FROM order_items WHERE supplier_part_id IS NOT NULL)") if False else None
    print(json.dumps(o,indent=1))
asyncio.run(main())
