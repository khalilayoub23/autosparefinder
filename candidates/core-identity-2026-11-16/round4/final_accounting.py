import json,gzip,glob,collections,sys
W='/opt/autosparefinder/candidates/core-identity-2026-11-16/wrong_offer_repair/'
v5=json.load(open(W+'classification_1043_final_v5.json'))
v4m={o['offer_id'] for o in json.load(gzip.open(W+'wo_manifest_v4_20261116.json.gz'))['offers']}
applied={}
for f in glob.glob('production/result_20261117*.json'):
    for d in json.load(open(f))['done']: applied[d['offer_id']]=f
KAM='223a9e2c-0caa-4d49-949a-4cf7be9ef29f'
pop=[r for r in v5 if r['disposition'] in('C_WRONG_PRICE_AND_URL','H_UNRESOLVED') or r['verdict'] in('REPAIR','REPAIR_IN_V4_MANIFEST')]
out=[];c=collections.Counter()
for r in pop:
    o=r['offer_id']
    if o==KAM: d='VALID_CURRENT_STATE'
    elif o in applied: d='REPAIRED_VERIFIED'
    elif r['disposition']=='H_UNRESOLVED':
        n=(r.get('v5_note') or '')
        if 'part-level identity' in (r.get('v3_note') or '') and r.get('v5_identity'): 
            d='UNRESOLVED_IDENTITY' if 'brand_mismatch' in n else 'IDENTITY_CONFIRMED_NO_PRICE'
        elif 'brand_mismatch' in n: d='UNRESOLVED_IDENTITY'
        elif 'listing row' in n: d='OTHER_EVIDENCE_GAP'
        else: d='UNRESOLVED_SOURCE_BLOCKER'
    else: d='UNACCOUNTED'
    c[d]+=1; out.append({'offer_id':o,'part_id':r['part_id'],'article':r['article'],'owner_brand':r['owner_brand'],'disposition':d,'note':r.get('v5_note') or r.get('v3_note')})
json.dump(out,open('final_disposition_951.json','w'),indent=0)
print(len(out),dict(c))
