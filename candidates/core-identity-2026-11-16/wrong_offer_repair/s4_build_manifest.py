"""S4: final per-offer disposition of the 1,043 + the pinned repair manifest (offline; inputs: s1 snapshot, s2 payload classification,
repair evidence rows rebuilt from the payload archives for the reconstructable rows)."""
import json,hashlib,collections,sys
s1={x['offer_id']:x for x in json.load(open('s1_snapshot_20261116.json'))}
s2={x['offer_id']:x for x in json.load(open('s2_payload_classification_20261116.json'))}
ev=json.load(open('s4_owner_rows.json'))   # offer_id -> list of owner payload rows (brand,price_eur,url,file,row,payload_mtime,import_end)
R=3.48
out=[]
for i,x in s1.items():
    o=s2[i]; d=dict(offer_id=i,article=x['article'],owner_brand=x['owner_brand'],foreign_brands=x['foreign'],part_id=x['part_id'],
        current_at_snapshot=dict(price=x['price_now'],url=x['url_now'],updated_at=x['updated_now']),
        original_class='UNDO' if False else 'NOT_READY_or_UNDO(see replay manifest)')
    if not x['unchanged']:
        if o['cur_eq_owner_price_url'] and not o['cur_eq_foreign_price_url']:
            d.update(disposition='G_VALID_CURRENT_STATE',verdict='PASS - PRESERVED',reason='owner-brand rewrite after the manifest: price (eur*3.48) and URL equal an owner payload row, equal to no foreign-brand row',
                     owner_row_in_import_window=o['cur_eq_owner_in_window'])
        else:
            d.update(disposition='H_UNRESOLVED',verdict='UNRESOLVED - EVIDENCE GAP',reason='current value equals an owner row AND a foreign-brand row (same price+url): writer cannot be told apart',gap='no row/timestamp evidence separating the brands')
    elif o['n_owner_rows']>0:
        rows=ev[i]; prices={round(r[1]*R,2) for r in rows if r[1] is not None}
        d.update(disposition='C_WRONG_PRICE+D_WRONG_URL',verdict='REPAIR',owner_prices_ils=sorted(prices),n_owner_rows=len(rows))
        assert len(prices)==1, i
        last=max(rows,key=lambda r:(r[5],r[4]))
        d['target']=dict(price=prices.pop(),url=last[2],evidence=dict(file=last[3],row=last[4],payload_mtime=last[5],brand=last[0],price_eur=last[1]))
    else:
        q=x['qualified_offers_same_article']
        d.update(disposition='H_UNRESOLVED',verdict='UNRESOLVED - EVIDENCE GAP',
                 reason='current price+url positively equal a foreign-brand payload row (wrong for the owner part), but no owner-brand row exists in any retained payload and the live owner page could not be fetched (Cloudflare challenge timeout via FlareSolverr 2026-11-16 16:47 IL)',
                 gap='owner-brand price+URL for this article (a fresh owner-brand listing row)',
                 foreign_brand_has_own_qualified_offer=bool(q),foreign_qualified_price_equals_bare=any(abs(t[1]-x['price_now'])<0.01 for t in q),
                 self_heal='owner-brand re-harvest through the fixed importer rewrites the bare key (observed for 73 of 1,043 within ~1 day)')
    out.append(d)
b=json.dumps(out,sort_keys=True,indent=0).encode()
open('classification_1043.json','wb').write(b)
print(hashlib.sha256(b).hexdigest())
print(collections.Counter(d['verdict'] for d in out),collections.Counter(d['disposition'] for d in out))
rep=[d for d in out if d['verdict']=='REPAIR']
print(json.dumps(rep,indent=1)[:3000])
