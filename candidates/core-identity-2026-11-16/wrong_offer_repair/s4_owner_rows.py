"""run in container: owner rows (any payload) for the offers that need a repair"""
import json,tarfile,glob,os,re,sys
sys.path.insert(0,'/app'); import part_identity
s2=json.load(open('/tmp/s2_out.json')); s1={x['offer_id']:x for x in json.load(open('/tmp/s1_snapshot.json'))}
want={o['offer_id'] for o in s2 if o['unchanged'] and o['n_owner_rows']>0}
by={s1[i]['article']:(i,s1[i]['owner_brand']) for i in want}
def clean(s): return re.sub(r'\s+',' ',str(s or '')).strip()
out={i:[] for i in want}; seen=set()
def ing(name,mt,raw,lm):
    d=json.loads(raw); rows=d['parts'] if isinstance(d,dict) else d
    for k,r in enumerate(rows):
        sku=clean(r.get('inferred_sku') or r.get('sku'))
        if sku in by and (name,mt,k) not in seen:
            i,ob=by[sku]
            if part_identity.title_brand(clean(r.get('name')),sku)==ob:
                seen.add((name,mt,k)); out[i].append((ob,r.get('price_eur'),r.get('source_url') or r.get('product_url'),name,k,mt,lm))
b='/app/state/cpie_ownership/forensics/'
for t in ('payloads_and_import_logs_20261115.tar.gz','payloads_and_import_logs_20261116.tar.gz'):
    tf=tarfile.open(b+t); mem={m.name:m for m in tf.getmembers()}
    for n,m in mem.items():
        if n.endswith('_cpie.json'): ing(n,m.mtime,tf.extractfile(m).read(),0)
for f in glob.glob('/tmp/*_cpie.json'): ing(os.path.basename(f),int(os.path.getmtime(f)),open(f).read(),0)
json.dump(out,open('/tmp/s4_owner_rows.json','w'))
