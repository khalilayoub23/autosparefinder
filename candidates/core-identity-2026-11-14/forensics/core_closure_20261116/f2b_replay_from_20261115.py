"""READ-ONLY. Step 2: replay the retained harvest payloads (/tmp/*_cpie.json, the exact rows the importer received) in
import order and reconstruct, per bare Car-Parts.ie offer, which row wrote its current (price,url) and whether that row's
brand equals the offer's part brand.  Validation: the replay must reproduce the live offer (price,url) AND the live
updated_at must fall inside the writing batch's [payload mtime, import-log mtime] window; otherwise the offer is not explained.
Three importer behaviours are replayed (version of a batch chosen by its time interval; a straddling batch keeps BOTH):
  old   = importer before 2026-11-15 06:46:21 UTC: every row overwrites the bare offer (price COALESCE, url)
  buggy = 06:46:21..16:56:32: brand-aware, but a same-URL row overwrites a different-brand bare offer (the defect)
  fixed = after 16:56:32 (commit 620ca69): only the owner brand writes the bare offer (same_url only if owner unresolved)
Counterfactual = 'fixed' logic over the whole period (what the offer would hold if no foreign brand had written it)."""
import asyncio, asyncpg, glob, json, os, re, sys, datetime as dt, collections
sys.path.insert(0, "/app")
import part_identity
UTC = dt.timezone.utc
DEPLOY = dt.datetime(2026, 11, 15, 6, 46, 21).timestamp(); FIX = dt.datetime(2026, 11, 15, 16, 56, 32).timestamp()
SNAP = dt.datetime(2026, 11, 15, 3, 0, 0).timestamp()      # catalog backup 20261114 consistent-snapshot start (03:00 UTC); replay starts here
SNAP15 = dt.datetime(2026, 11, 15, 3, 0, 0).timestamp()    # catalog backup 20261115 snapshot: intermediate validation point
RATE = float(os.getenv("EUR_ILS_RATE", "3.48"))
clean = lambda s: re.sub(r"\s+", " ", (s or "")).strip()
SID = open("/tmp/f/sid.txt").read().strip()
BK = {}
BK15 = {}
for l in open("/tmp/f/bk_offers.tsv"):
    i, pid, sku, pr, url, up = l.rstrip("\n").split("\t"); BK[sku] = (i, pid, pr, url, up)   # replay start state (11-14 snapshot), keyed by bare sku
for l in open("/tmp/f/bk_offers.tsv"):
    i, pid, sku, pr, url, up = l.rstrip("\n").split("\t"); BK15[sku] = (i, pid, pr, url, up)
import gzip, csv
PREMOVE = {}   # article -> pre-move updated_at (epoch): the 06:40-06:45Z moves reset supplier_parts.updated_at
try:
    _res = []
    for _f in sorted(glob.glob("/app/state/cpie_ownership/cpie_ownership_result_*.json")): _res += json.load(open(_f))["done"]
    _moved = {x["offer"] for x in _res}
    for _r in csv.DictReader(gzip.open("/app/state/cpie_ownership/pre_apply_supplier_parts_20261115.csv.gz", "rt")):
        if _r["id"] in _moved and _r["updated_at"]:
            PREMOVE[_r["supplier_sku"]] = dt.datetime.fromisoformat(_r["updated_at"]).replace(tzinfo=UTC).timestamp()
except Exception as _e: print("premove map unavailable", _e, file=sys.stderr)
def ts(p): return os.path.getmtime(p)
def num(x):
    try: return None if x in (None, "", "None", "\\N") else round(float(x), 2)
    except: return None
# ---- batches ----
batches = []
for jf in glob.glob("/tmp/*_cpie.json"):
    lg = jf[:-5] + "_import.log"
    if not os.path.exists(lg): continue
    t0, t1 = ts(jf), ts(lg)
    if t1 < SNAP - 3600: continue
    batches.append((t1, t0, jf))
batches.sort()
print("batches", len(batches), file=sys.stderr)
def modes(t0, t1):
    m = set()
    if t0 < DEPLOY: m.add("old")
    if t1 >= DEPLOY and t0 < FIX: m.add("buggy")
    if t1 >= FIX: m.add("fixed")
    return m
rows = collections.defaultdict(list)    # article -> [(batch_i, row_i, brand, name, price, url)]
binfo = []
for bi, (t1, t0, jf) in enumerate(batches):
    j = json.load(open(jf)); parts = j["parts"] if isinstance(j, dict) else j
    binfo.append({"file": os.path.basename(jf), "payload_mtime": t0, "import_end": t1, "modes": sorted(modes(t0, t1)), "rows": len(parts), "after_snapshot": t1 > SNAP})
    for ri, p in enumerate(parts):
        art = clean(p.get("inferred_sku") or p.get("sku") or "")
        if not art: continue
        name = clean(p.get("name") or art); url = clean(p.get("product_url") or p.get("source_url") or "")
        pe = p.get("price_eur") or 0.0; price = round(float(pe) * RATE, 2) if pe else None
        rows[art].append((bi, ri, part_identity.title_brand(name, art), name, price, url))
print("articles in payloads", len(rows), file=sys.stderr)
async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"),
                              server_settings={"default_transaction_read_only": "on", "statement_timeout": "900000"})
    t_read = dt.datetime.utcnow().timestamp()
    arts = sorted(rows); cur = {}
    for i in range(0, len(arts), 20000):
        for r in await c.fetch("""SELECT o.id::text oid, o.part_id::text pid, o.supplier_sku art, o.price_ils, o.supplier_url url, o.updated_at,
                                         p.name pname, p.sku psku FROM supplier_parts o JOIN parts_catalog p ON p.id=o.part_id
                                  WHERE o.supplier_id=$1::uuid AND o.supplier_sku=ANY($2::text[])""", SID, arts[i:i+20000]):
            cur[r["art"]] = dict(r)
    print("live bare offers", len(cur), file=sys.stderr)
    out = open(sys.argv[1], "w"); C = collections.Counter()
    for art, rs in rows.items():
        o = cur.get(art)
        if not o: C["no_live_bare_offer"] += 1; continue
        X = part_identity.title_brand(o["pname"], art)
        b0 = BK.get(art)
        init = (num(b0[2]), b0[3], None) if b0 else (None, None, None)
        def run(logic_for):                       # logic_for(batch) -> set of modes; returns set of (price,url,writer)
            S = {init}; ev = []; snap15 = None
            for (bi, ri, Y, name, price, url) in rs:
                if binfo[bi]["import_end"] <= SNAP: continue
                if snap15 is None and binfo[bi]["import_end"] > SNAP15: snap15 = {(st[0], st[1]) for st in S}
                new = set()
                for st in S:
                    for m in logic_for(bi):
                        w = False
                        if m == "old": w = True
                        elif Y is None: w = False
                        else:
                            same = bool(st[1]) and st[1].lower() == url.lower()
                            if X is None: w = same
                            elif X == Y: w = True
                            else: w = same if m == "buggy" else False
                        new.add((price if price is not None else st[0], url, (bi, ri, Y)) if w else st)
                        if w and len(S) == 1 and len(logic_for(bi)) == 1 and Y != X: ev.append((bi, ri, Y, m))
                S = new
            if snap15 is None: snap15 = {(st[0], st[1]) for st in S}
            return S, ev, snap15
        sim, events, snap15 = run(lambda bi: set(binfo[bi]["modes"]))
        cf, _, _ = run(lambda bi: {"fixed"})
        act = (num(o["price_ils"]), o["url"])
        upd = o["updated_at"].replace(tzinfo=UTC).timestamp()
        match = []
        for st in sim:
            if st[2] is None:
                if (st[0], st[1]) == act: match.append(st)
            else:
                b = binfo[st[2][0]]
                if (st[0], st[1]) == act and (b["payload_mtime"] - 2 <= upd <= b["import_end"] + 2 or (art in PREMOVE and b["payload_mtime"] - 2 <= PREMOVE[art] <= b["import_end"] + 2)): match.append(st)
        rec = {"article": art, "offer": o["oid"], "part": o["pid"], "part_sku": o["psku"], "part_name": o["pname"], "owner_brand": X,
               "actual": {"price": act[0], "url": act[1], "updated_at": o["updated_at"].isoformat()},
               "backup": {"price": init[0], "url": init[1]} if b0 else None, "backup15": ({"price": num(BK15[art][2]), "url": BK15[art][3]} if art in BK15 else None), "rows_after_snapshot": sum(1 for r in rs if binfo[r[0]]["import_end"] > SNAP),
               "brands_in_rows": sorted({r[2] for r in rs if r[2]}), "matched": bool(match), "moved_offer_premove_timestamp_used": art in PREMOVE, "cross_brand_write_events": [{"file": binfo[b]["file"], "row": r_, "brand": y, "mode": m} for (b, r_, y, m) in events],
               "snapshot15_check": (None if art not in BK15 else ((num(BK15[art][2]), BK15[art][3]) in snap15)), "replay_start_backup": "20261115"}
        def wr(st):
            if st[2] is None: return None
            bi, ri, Y = st[2]; r = next(x for x in rs if x[0] == bi and x[1] == ri); b = binfo[bi]
            return {"file": b["file"], "row": ri, "brand": Y, "name": r[3], "price": r[4], "url": r[5], "batch_modes": b["modes"],
                    "payload_time": dt.datetime.fromtimestamp(b["payload_mtime"], UTC).isoformat(), "import_end": dt.datetime.fromtimestamp(b["import_end"], UTC).isoformat()}
        rec["writers"] = [wr(st) for st in match]
        rec["counterfactual"] = [{"price": st[0], "url": st[1], "writer": wr(st)} for st in cf]
        out.write(json.dumps(rec, default=str) + "\n"); C["offers_replayed"] += 1; C["matched"] += bool(match)
    print(dict(C), file=sys.stderr)
    json.dump({"t_read_utc": dt.datetime.utcfromtimestamp(t_read).isoformat(), "batches": binfo}, open(sys.argv[1] + ".meta.json", "w"))
asyncio.run(main())
