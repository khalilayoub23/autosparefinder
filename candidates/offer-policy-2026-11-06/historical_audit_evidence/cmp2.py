import sys, json
from collections import Counter, defaultdict
S='snap/'
old,new=sys.argv[1:3]
ids={}; sku_part={}; sp_pairs={}; sku_any={}
with open(S+new+'.tsv') as f:
    for ln in f:
        p=ln.rstrip('\n').split('\t')
        ids[hash(p[0])]=hash((p[4],p[5],p[6]))
        sku_part[hash((p[1],p[3]))]=hash(p[2])
        sp_pairs[hash((p[1],p[2]))]=hash(p[6])
        sku_any[hash((p[2],p[3]))]=hash(p[1])
tot=Counter(); cls=Counter(); bysup=defaultdict(Counter); ex=defaultdict(list); chg=Counter(); chg_ex=[]
newonly=len(ids)
sku_new=set()
with open(S+old+'.tsv') as f:
    for ln in f:
        p=ln.rstrip('\n').split('\t'); sid=p[1]; tot[sid]+=1
        h=ids.get(hash(p[0]))
        if h is not None:
            newonly-=1
            if h!=hash((p[4],p[5],p[6])):
                chg[sid]+=1
                if len(chg_ex)<3: chg_ex.append(p)
            continue
        k=hash((sid,p[3])); kp=hash((sid,p[2]))
        if k in sku_part:
            c='REKEYED_same_supplier_sku_same_part' if sku_part[k]==hash(p[2]) else 'REMAPPED_same_supplier_sku_other_part'
        elif kp in sp_pairs:
            c='SAME_SUPPLIER_PART_DIFFERENT_SKU' + ('_variant_type_differs' if sp_pairs[kp]!=hash(p[6]) else '_same_type')
        elif sku_any.get(hash((p[2],p[3])),hash(sid))!=hash(sid):
            c='SUPPLIER_CHANGED_same_part_sku_other_supplier'
        else:
            c='GENUINELY_ABSENT_by_id_sku_pair'
        cls[c]+=1; bysup[c][sid]+=1
        if len(ex[c])<3: ex[c].append(p)
print(json.dumps({'old':old,'new':new,'old_rows':sum(tot.values()),'new_rows':len(ids),'ids_only_in_new':newonly,'missing_ids':sum(cls.values()),
 'classes':dict(cls),'changed_price_avail_type_among_surviving_ids':sum(chg.values()),
 'top_suppliers_per_class':{c:bysup[c].most_common(6) for c in cls},
 'examples':ex,'chg_examples':chg_ex}))
