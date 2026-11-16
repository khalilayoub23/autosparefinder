"""S1: read-only snapshot + enrichment of the 1,043 known wrong offers. Run inside autospare_backend (read-only SQL only)."""
import asyncio,asyncpg,os,json,glob,re,collections,sys,datetime
sys.path.insert(0,'/app')
import part_identity
DB=os.environ['DATABASE_URL'].replace('postgresql+asyncpg://','postgresql://')
w=json.load(open('/tmp/w1043.json'))
def clean(s): return re.sub(r'\s+',' ',str(s or '')).strip()
# payload index: sku -> rows
idx=collections.defaultdict(list)
for f in glob.glob('/tmp/*_cpie.json'):
    try: d=json.load(open(f))
    except Exception: continue
    rows=d['parts'] if isinstance(d,dict) else d
    mt=datetime.datetime.utcfromtimestamp(os.path.getmtime(f)).isoformat()
    for i,r in enumerate(rows):
        sku=clean(r.get('inferred_sku') or r.get('sku'))
        if not sku: continue
        idx[sku].append((part_identity.title_brand(clean(r.get('name')),sku),r.get('price_eur'),r.get('source_url') or r.get('product_url'),os.path.basename(f),i,mt))
async def main():
    c=await asyncpg.connect(DB)
    sid=await c.fetchval("select supplier_id from supplier_parts where id=$1::uuid",w[0]['offer_id'])
    ids=[x['offer_id'] for x in w]
    rows=await c.fetch("select sp.id::text id,sp.part_id::text part_id,sp.supplier_sku,sp.price_ils,sp.price_usd,sp.supplier_url,sp.updated_at,sp.is_available,sp.part_type,p.name pname,p.sku psku,p.is_active from supplier_parts sp join parts_catalog p on p.id=sp.part_id where sp.id=any($1::uuid[])",ids)
    cur={r['id']:r for r in rows}
    ph=await c.fetch("select supplier_part_id::text id,old_price_ils,new_price_ils,source,created_at from price_history where supplier_part_id=any($1::uuid[]) and created_at>'2026-11-14' order by created_at",ids)
    phm=collections.defaultdict(list)
    for r in ph: phm[r['id']].append((str(r['old_price_ils']),str(r['new_price_ils']),r['source'],r['created_at'].isoformat()))
    qall=await c.fetch("select sp.id::text id,sp.supplier_sku,sp.price_ils,sp.supplier_url,p.id::text pid,p.name from supplier_parts sp join parts_catalog p on p.id=sp.part_id where sp.supplier_id=$1 and sp.supplier_sku like '%~%'",sid)
    qby=collections.defaultdict(list)
    for f in qall: qby[f['supplier_sku'].split('~',1)[1]].append(f)
    out=[]
    for x in w:
        r=cur[x['offer_id']]; art=x['article']
        mf=x['current']
        d=dict(offer_id=x['offer_id'],article=art,owner_brand=x['owner_brand'],foreign=x['foreign_brands_seen'],
          part_id=r['part_id'],part_name=r['pname'],part_sku=r['psku'],sku_now=r['supplier_sku'],
          price_now=float(r['price_ils']),url_now=r['supplier_url'],updated_now=r['updated_at'].isoformat(),
          manifest_price=mf['price'],manifest_url=mf['url'],manifest_updated=mf['updated_at'],
          part_brand_now=part_identity.title_brand(r['pname'],art),
          snap1114=x['state_at_20261114_snapshot'],cause=x['cause'],target=x['repair'].get('target_price'),target_url=x['repair'].get('target_url'),
          price_history_since=phm.get(x['offer_id'],[]),
          unchanged=(float(r['price_ils'])==mf['price'] and r['supplier_url']==mf['url'] and r['updated_at'].isoformat()==mf['updated_at']))
        rows_p=idx.get(art,[])
        d['payload_owner_rows']=[p for p in rows_p if p[0]==x['owner_brand']]
        d['payload_foreign_rows']=[p for p in rows_p if p[0] and p[0]!=x['owner_brand']]
        # foreign-brand parts/offers in DB holding same article
        fo=qby.get(art,[])
        d['qualified_offers_same_article']=[(f['supplier_sku'],float(f['price_ils']),f['supplier_url'],f['pid'],f['name']) for f in fo]
        out.append(d)
    json.dump(out,open('/tmp/s1_snapshot.json','w'),default=str)
    print(len(out),'unchanged',sum(d['unchanged'] for d in out))
asyncio.run(main())
