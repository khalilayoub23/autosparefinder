"""READ-ONLY. Step 1 of the cross-brand overwrite forensics (workstream C, 2026-11-15).
Collects, for every Car-Parts.ie BARE offer written by the new importer inside the defect window
[2026-11-15 06:46:21, 16:56:32 UTC] whose article is shared by >=2 Car-Parts.ie parts, the offer, its part (A),
every other part (B) with the same oem_number, the price_history rows of the window and the pre-window backup row.
Output: JSON lines on stdout path given as argv[1]. No write to the database (default_transaction_read_only on)."""
import asyncio, asyncpg, json, os, sys, csv, datetime as dt
sys.path.insert(0, "/app")
import part_identity
DEPLOY = dt.datetime(2026, 11, 15, 6, 46, 21); FIX = dt.datetime(2026, 11, 15, 16, 56, 32)
SID = open("/tmp/f/sid.txt").read().strip()
BK = {}
for l in open("/tmp/f/bk_offers.tsv"):
    i, pid, sku, pr, url, up = l.rstrip("\n").split("\t")
    BK[i] = (pid, sku, pr, url, up)
async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"),
                              server_settings={"default_transaction_read_only": "on", "statement_timeout": "900000"})
    offs = await c.fetch("""SELECT o.id::text oid, o.part_id::text pid, o.supplier_sku sku, o.price_ils, o.supplier_url url, o.updated_at,
                                   a.name aname, a.oem_number art, a.sku asku, a.specifications::text aspec, a.online_price_ils aprice, a.created_at acreated
                            FROM supplier_parts o JOIN parts_catalog a ON a.id=o.part_id
                            WHERE o.supplier_id=$1::uuid AND o.supplier_sku NOT LIKE '%~%' AND o.updated_at>=$2 AND o.updated_at<$3""",
                         SID, DEPLOY, FIX)
    print("window offers", len(offs), file=sys.stderr)
    arts = sorted({r["art"] for r in offs if r["art"]})
    parts = {}
    for i in range(0, len(arts), 20000):
        for r in await c.fetch("""SELECT id::text pid, sku, name, oem_number art, specifications::text spec, online_price_ils price, base_price, created_at, updated_at
                                  FROM parts_catalog WHERE oem_number=ANY($1::text[]) AND specifications->>'source'='Car-Parts.ie'""", arts[i:i+20000]):
            parts.setdefault(r["art"], []).append(dict(r))
    print("articles", len(parts), file=sys.stderr)
    out = open(sys.argv[1], "w"); n = 0
    cand = []
    for r in offs:
        ps = parts.get(r["art"], [])
        ba = part_identity.title_brand(r["aname"], r["art"])
        others = []
        for b in ps:
            if b["pid"] == r["pid"]: continue
            bb = part_identity.title_brand(b["name"], r["art"])
            others.append((b, bb))
        if not any(bb and ba and bb != ba for b, bb in others): continue
        cand.append((r, ba, others))
    ids = [r["oid"] for r, _, _ in cand]
    ph = {}
    for i in range(0, len(ids), 20000):
        for h in await c.fetch("""SELECT supplier_part_id::text sp, old_price_ils, new_price_ils, source, created_at FROM price_history
                                  WHERE supplier_part_id=ANY($1::uuid[]) AND created_at>=$2 ORDER BY created_at""", ids[i:i+20000], DEPLOY - dt.timedelta(hours=1)):
            ph.setdefault(h["sp"], []).append({"old": str(h["old_price_ils"]), "new": str(h["new_price_ils"]), "src": h["source"], "at": h["created_at"].isoformat()})
    for r, ba, others in cand:
        rec = {"offer": r["oid"], "part": r["pid"], "sku": r["sku"], "price": str(r["price_ils"]), "url": r["url"], "updated_at": r["updated_at"].isoformat(),
               "a_name": r["aname"], "a_sku": r["asku"], "a_brand": ba, "a_spec": json.loads(r["aspec"] or "{}"), "a_price": str(r["aprice"]), "a_created": r["acreated"].isoformat(),
               "backup": BK.get(r["oid"]), "price_history": ph.get(r["oid"], []),
               "others": [{"part": b["pid"], "sku": b["sku"], "name": b["name"], "brand": bb, "spec": json.loads(b["spec"] or "{}"), "price": str(b["price"]), "base_price": str(b["base_price"]),
                           "created": b["created_at"].isoformat(), "updated": b["updated_at"].isoformat()} for b, bb in others]}
        out.write(json.dumps(rec, default=str) + "\n"); n += 1
    print("candidates", n, file=sys.stderr)
asyncio.run(main())
