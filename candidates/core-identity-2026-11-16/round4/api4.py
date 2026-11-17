import json,glob,urllib.request,sys
sys.path.insert(0,'/app')
import price_policy as pp
rows=[]
for f in glob.glob('/app/state/cpie_ownership/overwrite_repair/result_20261117*'): rows+=json.load(open(f))['done']
ok=0; bad=[]
for r in rows:
    import time; time.sleep(2.3)
    try: d=json.load(urllib.request.urlopen(f"http://localhost:8000/api/v1/parts/{r['part_id']}/suppliers",timeout=20))
    except Exception as e: bad.append((r['part_id'][:8],'ERR')); continue
    offs=d if isinstance(d,list) else d.get('suppliers') or d.get('offers') or []
    exp=pp.unit_price(float(r['after']['price_ils']),"Car-Parts.ie","IE").net
    if any(abs(float(o.get('customer_price_ils') or -1)-exp)<=0.011 for o in offs): ok+=1
    else: bad.append((r['part_id'][:8],r['after']['price_ils'],exp,[o.get('customer_price_ils') for o in offs][:4]))
print('API customer_price_ils == policy(repaired cost):',ok,'of',len(rows),'| others:',len(bad)); print(bad[:6])
