import asyncio, os, sys, json
sys.path.insert(0,'/app'); sys.path.insert(0,'/app/maintenance')
import asyncpg, restore_merged_offers_133 as m
from collections import Counter, defaultdict
async def main():
    cols, rows, sha = m.load_input(__import__('pathlib').Path('/tmp/restore133_input.tsv'))
    net=set()
    for f in ('ma','mb','mc'): net|={x['id'] for x in json.load(open(f'/tmp/{f}.json'))['inserted']}
    sub={x['id'] for x in json.load(open('/tmp/sub7.json'))['inserted']}; net-=sub
    c=await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://'))
    await c.execute("SET statement_timeout='250s'"); await c.execute("SET default_transaction_read_only=on")
    ver=[]
    for i in range(0,len(rows),1000): ver+=await m.evaluate(c,rows[i:i+1000])
    cnt=Counter(v['status'] for v in ver); print('input',len(rows),'sha',sha[:16],'variant_api',m.VARIANT_API); print('verdicts',dict(cnt))
    # sub-classification
    out=defaultdict(Counter); ex={}; det=[]
    canon=await m.resolve_canonicals(c,list({r['part_id'] for r in rows}))
    pairs=[(canon[r['part_id']][0],r['supplier_id']) for r in rows if canon[r['part_id']][0] and len(canon[r['part_id']][0])==36]
    existing=defaultdict(list)
    for r in await c.fetch("select sp.id,sp.supplier_id,sp.part_id,sp.supplier_sku,sp.part_type,sp.is_available from supplier_parts sp join unnest($1::uuid[],$2::uuid[]) u(p,s) on sp.part_id=u.p and sp.supplier_id=u.s",[p for p,_ in pairs],[s for _,s in pairs]):
        existing[(str(r['part_id']),str(r['supplier_id']))].append(dict(r))
    skurows={}
    for r in await c.fetch("select sp.id,sp.supplier_id,sp.part_id,sp.supplier_sku,sp.is_available from supplier_parts sp join unnest($1::uuid[],$2::text[]) u(s,k) on sp.supplier_id=u.s and sp.supplier_sku=u.k",[r['supplier_id'] for r in rows],[r['supplier_sku'] or '' for r in rows]):
        skurows[(str(r['supplier_id']),r['supplier_sku'])]=dict(r)
    for v in ver:
        r=v['row']; st=v['status']; cn=v['canonical']; k=None
        if st=='original_id_already_present':
            k='restored_by_133_manifests' if r['id'] in net else ('rolled_back_7?' if r['id'] in sub else 'id_exists_other')
        elif st=='supplier_sku_already_present':
            e=skurows.get((r['supplier_id'],r['supplier_sku']))
            cc=canon[r['part_id']][0]
            k=('live_same_sku_on_canonical_part' if e and str(e['part_id'])==cc else 'live_same_sku_on_original_part' if e and str(e['part_id'])==r['part_id'] else 'live_same_sku_on_other_part')
        elif st=='blocked_by_variant_unique_index':
            ex_=existing.get((cn,r['supplier_id']),[]); myv=m._variant(r['part_type'])
            bl=[e for e in ex_ if m._variant(e['part_type'])==myv]
            k='slot_taken_by_133_sibling' if any(str(e['id']) in net for e in bl) else 'slot_taken_by_preexisting_live_offer'
            k+= '|both_untyped' if myv is None else '|variant='+myv
        elif st=='eligible':
            k='eligible_'+('hebrew_label' if r['part_type']=='מקורי' else 'other')
        else: k=st
        det.append((r['id'],r['supplier_id'],st,k,r['part_type'] or '')); out[st][k]+=1; ex.setdefault((st,k),(r['id'],r['supplier_id'][:8],r['supplier_sku'],r['part_type']))
    for st in out:
        print(st,dict(out[st]))
    print('examples',{f'{a}/{b}':v for (a,b),v in list(ex.items())[:14]})
    heb=[v for v in ver if v['row']['part_type']=='מקורי']
    print('hebrew-label input rows',len(heb),dict(Counter(v['status'] for v in heb)),'| of which the 7 rolled back now:',[v['status'] for v in heb if v['row']['id'] in sub])
    print('input part_type counts',dict(Counter(r['part_type'] for r in rows)))
    # null price among unrestored
    print('unrestored rows (not in net):',sum(1 for r in rows if r['id'] not in net))
    open('/tmp/recon_detail.tsv','w').write('id\tsupplier_id\tstatus\tsubclass\tsource_part_type\n'+'\n'.join('\t'.join(x) for x in sorted(det))+'\n')
    await c.close()
asyncio.run(main())
