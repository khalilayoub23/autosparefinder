import json, sys
M=sys.argv[1]
for ph in ("old","new"):
    e=json.load(open(f"{M}/debug_{ph}.json"))
    print(f"\n######## {ph.upper()} CODE")
    for t in e["tasks"]:
        r=t["report"]; keys={k:r.get(k) for k in ("status","scanned","eligible","updated","unchanged","skipped","failed","reasons","rows_updated","total_updated","inserted","parts_matched","updated_parts","error") if k in r}
        print(f"\n-- {t['task']}: {keys}")
        for sku,d in t["diff"].items():
            for k,(a,b) in d.items():
                if k=="compatible_vehicles":
                    def mk(x): return sorted({(i.get('manufacturer'),i.get('model'),i.get('source')) for i in x} if isinstance(x,list) else [str(x)])
                    a,b=mk(a),mk(b)
                print(f"   {sku:20s} {k}: {a} -> {b}")
    print("\nsearch before:", e["search_before"]); print("search after: ", e["search_after"])
    same = e["surfaces_before"]==e["surfaces_after"]
    print("customer surfaces identical before/after all six tasks:", same)
    if not same:
        for s in e["surfaces_before"]:
            if e["surfaces_before"][s]!=e["surfaces_after"][s]: print("  ",s,e["surfaces_before"][s],"->",e["surfaces_after"][s])
    for k in ("rerun","reverse"):
        ch=[(t["task"],t["diff"]) for t in e[k] if t["diff"]]
        upd=[(t["task"],{x:t["report"].get(x) for x in ("updated","updated_parts","rows_updated","total_updated","inserted") if t["report"].get(x)}) for t in e[k]]
        print(f"{k}: rows changed -> {ch if ch else 'NONE'}; reported writes -> {[u for u in upd if u[1]]}")
    if "interrupt" in e:
        print("interrupt steps:"); [print("  ",s) for s in e["interrupt"]["steps"]]
        print("mismatch vs uninterrupted:", e["interrupt"]["mismatch_vs_uninterrupted"])
    if "cleanup_normalizer" in e: print("cleanup normalizer:", e["cleanup_normalizer"])
