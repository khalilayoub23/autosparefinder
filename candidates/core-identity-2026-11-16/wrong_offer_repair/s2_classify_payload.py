"""S2 (read-only, run in autospare_backend): re-classify the 1,043 against the UNION of payload archives (tar 11-15, tar 11-16, /tmp).
Rate/rounding = importer: round(price_eur * EUR_ILS_RATE(3.48 default), 2). Evidence for an owner row: price AND url equal, and the
last car_parts_ie_import price_history row of the offer lies inside the payload file's import window."""
import json,tarfile,glob,os,re,collections,datetime,sys
sys.path.insert(0,'/app')
import part_identity
R=float(os.getenv('EUR_ILS_RATE','3.48'))
def clean(s): return re.sub(r'\s+',' ',str(s or '')).strip()
snap=json.load(open('/tmp/s1_snapshot.json'))
arts={x['article'] for x in snap}
idx=collections.defaultdict(list)   # sku -> [(brand,price_eur,url,file,row,payload_mtime,import_end)]
seen=set()
def ingest(name,mt,raw,log_mt):
    try: d=json.loads(raw)
    except Exception: return
    rows=d['parts'] if isinstance(d,dict) else d
    for i,r in enumerate(rows):
        sku=clean(r.get('inferred_sku') or r.get('sku'))
        if sku in arts:
            k=(name,mt,i)
            if k in seen: continue
            seen.add(k)
            idx[sku].append((part_identity.title_brand(clean(r.get('name')),sku),r.get('price_eur'),r.get('source_url') or r.get('product_url'),name,i,mt,log_mt))
base='/app/state/cpie_ownership/forensics/'
for t in ('payloads_and_import_logs_20261115.tar.gz','payloads_and_import_logs_20261116.tar.gz'):
    tf=tarfile.open(base+t); mem={m.name:m for m in tf.getmembers()}
    for n,m in mem.items():
        if n.endswith('_cpie.json'):
            lg=mem.get(n.replace('_cpie.json','_cpie_import.log'))
            ingest(n,m.mtime,tf.extractfile(m).read(),lg.mtime if lg else m.mtime+120)
for f in glob.glob('/tmp/*_cpie.json'):
    lf=f.replace('_cpie.json','_cpie_import.log')
    ingest(os.path.basename(f),int(os.path.getmtime(f)),open(f).read(),int(os.path.getmtime(lf)) if os.path.exists(lf) else int(os.path.getmtime(f))+120)
def ts(s): return datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc).timestamp()
out=[]
for x in snap:
    rows=idx.get(x['article'],[])
    own=[r for r in rows if r[0]==x['owner_brand']]
    forn=[r for r in rows if r[0] and r[0]!=x['owner_brand']]
    cur=lambda r: round(r[1]*R,2)==x['price_now'] and r[2]==x['url_now'] if r[1] is not None else False
    lastph=ts(x['price_history_since'][-1][3]) if x['price_history_since'] else None
    inwin=lambda r: lastph is not None and r[5]-30<=lastph<=r[6]+30
    exact_owner=[r for r in own if cur(r)]
    exact_owner_win=[r for r in exact_owner if inwin(r)]
    exact_foreign=[r for r in forn if cur(r)]
    x2=dict(offer_id=x['offer_id'],article=x['article'],owner_brand=x['owner_brand'],unchanged=x['unchanged'],
      n_owner_rows=len(own),owner_prices_ils=sorted({round(r[1]*R,2) for r in own if r[1] is not None}),
      owner_urls_n=len({r[2] for r in own}),
      cur_eq_owner_price_url=bool(exact_owner),cur_eq_owner_in_window=bool(exact_owner_win),
      cur_eq_foreign_price_url=bool(exact_foreign),
      cur_eq_foreign_price_only=any(round(r[1]*R,2)==x['price_now'] for r in forn if r[1] is not None),
      foreign_in_payload=sorted({r[0] for r in forn}))
    out.append(x2)
json.dump(out,open('/tmp/s2_out.json','w'))
C=collections.Counter
print(C((o['unchanged'],o['n_owner_rows']>0,o['cur_eq_owner_price_url'],o['cur_eq_owner_in_window'],o['cur_eq_foreign_price_url'],o['cur_eq_foreign_price_only']) for o in out).most_common())
