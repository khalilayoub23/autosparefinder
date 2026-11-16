"""S3 (read-only on DB; external GET of car-parts.ie product listing pages via the harvester's own http_get/parse_parts, NOT via /collect
or the importer). For every unchanged offer without an owner payload row, fetch the pre-defect listing page (snap 11-14 url) and look for
the owner brand's row (title_brand) with the same article. HTML + sha256 stored as evidence. Paced 1.5 s/page, resumable."""
import sys,json,os,re,hashlib,time,collections
sys.path.insert(0,'/app'); sys.path.insert(0,'/app/harvesters')
import car_parts_ie_flaresolverr_harvester as h
import part_identity
EV='/app/state/cpie_ownership/wrong_offer_repair/live_html'; os.makedirs(EV,exist_ok=True)
snap={x['offer_id']:x for x in json.load(open('/tmp/s1_snapshot.json'))}
s2=json.load(open('/tmp/s2_out.json'))
targets=[o for o in s2 if o['unchanged']]   # 969
res_path='/app/state/cpie_ownership/wrong_offer_repair/s3_live_rows.json'
res=json.load(open(res_path)) if os.path.exists(res_path) else {}
pages={}
def clean(s): return re.sub(r'\s+',' ',str(s or '')).strip()
for o in targets:
    x=snap[o['offer_id']]
    for url in {x['snap1114']['url']}:
        if url in res: continue
        path=url.replace(h.BASE,'')
        html=h.http_get(url)
        sha=hashlib.sha256(html.encode()).hexdigest() if html else None
        if html: open(f'{EV}/{sha}.html','w').write(html)
        rows=[]
        if html:
            for p in h.parse_parts(html,path):
                sku=clean(p['sku'])
                rows.append(dict(sku=sku,brand=part_identity.title_brand(clean(p['name']),sku),name=p['name'],price_eur=p['price_eur']))
        res[url]=dict(sha256=sha,fetched_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),n=len(rows),http_ok=bool(html),
                      rows=[r for r in rows if r['sku'] in {a['article'] for a in targets}])
        if len(res)%25==0: json.dump(res,open(res_path,'w'))
        time.sleep(1.5)
json.dump(res,open(res_path,'w'))
print('done',len(res),'ok',sum(v['http_ok'] for v in res.values()))
