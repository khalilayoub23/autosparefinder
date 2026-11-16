"""S8: classification v2 = v1 final + (a) 15 H rows healed by owner re-harvest 17:06-19:58 UTC (consultant Phase D, re-read live by primary: s8_live_check.json)
+ (b) the 3 REPAIR rows marked REPAIRED (applied 2026-11-16 20:39 UTC, accepted, independently verified)."""
import json,hashlib,collections
pref="cb90b41b 0446e840 c4c5b13a be082255 cc8b12fc 27c61a32 d4fa62a5 17ab3cee c3bec056 f11b6337 04b207ab d4ea12ba 1d9b2f7d 6e4c5b1d 9a6c849a".split()
live=json.load(open('s8_live_check.json'))
c=json.load(open('classification_1043_final.json'))
n=0
for d in c:
    if d['verdict']=='REPAIR':
        d['verdict']='PASS - REPAIRED'; d['applied']=dict(manifest_sha256='01bb5c65916b2248b133279a497842e53b8e40b23520d4bcad98b19e1ff7d35a',price_history_note='source cpie_overwrite_repair')
    elif d['offer_id'][:8] in pref:
        assert live[d['offer_id']]['moved_by_importer'], d['offer_id']
        d.update(verdict='PASS - PRESERVED',disposition='G_VALID_CURRENT_STATE',reason='owner re-harvest 2026-11-16 17:06-19:58 UTC (car_parts_ie_import), equals the latest owner payload row (consultant Phase D; primary re-read s8_live_check.json)'); n+=1
assert n==15,n
b=json.dumps(c,sort_keys=True,indent=0).encode(); open('classification_1043_final_v2.json','wb').write(b)
print(hashlib.sha256(b).hexdigest(),collections.Counter(d['verdict'] for d in c))
