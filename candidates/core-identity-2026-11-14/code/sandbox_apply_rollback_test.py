"""Sandbox only: apply + rollback of cpie_offer_ownership restores the EXACT previous ownership; price/url never change."""
import asyncio, json, os, sys, pathlib, tempfile
import asyncpg
sys.path.insert(0, '/work/t')
import cpie_offer_ownership as m
DSN = os.environ['SANDBOX_DSN_BASE'] + '/sandbox_c1'
async def snap(c):
    return [tuple(r) for r in await c.fetch("select id::text,part_id::text,supplier_id::text,supplier_sku,price_ils::float,supplier_url,is_available from supplier_parts order by id")]
async def main():
    m.DB = DSN; m.OUT = pathlib.Path(tempfile.mkdtemp())
    c = await asyncpg.connect(DSN)
    sup = await c.fetchval("select id::text from suppliers where name='Car-Parts.ie'"); m.SUP = sup
    off = await c.fetchrow("select sp.id::text id,sp.part_id::text pid,sp.supplier_url url,sp.price_ils::float price from supplier_parts sp where sp.supplier_sku='FAE~75659'")
    await c.execute("insert into parts_catalog(id,sku,oem_number,name,manufacturer,category,part_type,part_condition,is_active,created_at,updated_at) "
                    "select gen_random_uuid(),'TOYOT-DUMMYTARGET','75659','dummy target','Toyota',category,part_type,part_condition,true,now(),now() from parts_catalog where id=$1::uuid", off['pid'])
    tgt = await c.fetchval("select id::text from parts_catalog where sku='TOYOT-DUMMYTARGET'")
    before = await snap(c)
    man = m.OUT / 'man.json'
    man.write_text(json.dumps({"items": [{"action": "MOVE", "offer": off['id'], "article": "75659", "from_part": off['pid'], "to_part": tgt, "url": off['url'], "price": off['price']},
                                          {"action": "MOVE", "offer": off['id'], "article": "75659", "from_part": "00000000-0000-0000-0000-000000000000", "to_part": tgt, "url": off['url'], "price": off['price']}]}))
    await m.apply(man, 'SANDBOX')
    mid = await snap(c)
    changed = [(a, b) for a, b in zip(before, mid) if a != b]
    assert len(changed) == 1 and changed[0][1][1] == tgt, changed
    assert changed[0][0][4:] == changed[0][1][4:], "price/url/availability must not change"
    res = sorted(m.OUT.glob('cpie_ownership_result_*.json'))[-1]
    r = json.load(open(res)); assert r['applied'] == 1 and r['skipped'] == 1, r        # 2nd row: stale precondition skipped
    await m.rollback(res, 'SANDBOX', DSN)
    after = await snap(c)
    assert after == before, "rollback did not restore the exact previous state"
    out = await m.rollback(res, 'SANDBOX', DSN)                                           # second rollback: nothing to do, nothing breaks
    assert out['restored'] == 0 and (await snap(c)) == before
    await c.execute("delete from parts_catalog where sku='TOYOT-DUMMYTARGET'")
    print('OK apply -> exact rollback; stale row skipped; second rollback no-op')
asyncio.run(main())
