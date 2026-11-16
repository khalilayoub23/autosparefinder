"""S5: final disposition per offer = S4 classification + consultant Phase A/B corrections (consultant_phaseAB_review.md, consultant_wrongpart_lists.json).
625485ba (AIC 53506) moved H->G (AIC rewrote it 2026-11-16 16:52:57 UTC after the S1 snapshot; verified live by primary in S6)."""
import json,hashlib,collections
c={d['offer_id']:d for d in json.load(open('classification_1043.json'))}
w=json.load(open('consultant_wrongpart_lists.json'))
cls={}
for k,v in w.items():
    for r in v: cls[r[0]]=(k,r)
out=[]
for i,d in c.items():
    k,r=cls[i]; d=dict(d); d['consultant_group']=k; d['creator_evidence']=r[3:5]
    if d['verdict']=='REPAIR':
        d['disposition']='C_WRONG_PRICE'; d['note']='target url == current url (listing page shows both brands): price-only update'
        if d['article']=='ZR CT728 R': d['note']+='; JUMASA price rests on ONE owner payload observation'
    elif k.startswith('G|') and d['verdict']!='PASS - PRESERVED':
        d.update(disposition='G_VALID_CURRENT_STATE',verdict='PASS - PRESERVED',reason='owner rewrite after the S1 snapshot (consultant live read; primary re-verifies in S6)')
    elif d['verdict']=='UNRESOLVED - EVIDENCE GAP':
        d['wrong_part_risk']='RIGHT_PART_WRONG_PRICE' if k.startswith('H|creator=owner') else 'PART_LEVEL_DECISION_REQUIRED(part identity mixed/ambiguous) - excluded from any owner-price UPDATE' if not k.startswith('H|creator_unknown') else 'CREATOR_UNKNOWN'
        d['gap']='owner-brand current price: the owner product page (specifications.product_url) was not fetchable (Cloudflare challenge) and no owner payload row exists'
    d['mixed_part_identity_G']=k.startswith('G|creator=third_brand')
    out.append(d)
b=json.dumps(out,sort_keys=True,indent=0).encode(); open('classification_1043_final.json','wb').write(b)
print(hashlib.sha256(b).hexdigest()); print(collections.Counter(d['verdict'] for d in out)); print(collections.Counter(d.get('wrong_part_risk') for d in out))
