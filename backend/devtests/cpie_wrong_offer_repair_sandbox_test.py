"""
Script:  devtests/cpie_wrong_offer_repair_sandbox_test.py
Purpose: Sandbox-only test of the 2026-11-16 extensions of maintenance/cpie_overwrite_repair.py used for the 1,043 wrong-offer mission:
         --expect-count (pinned row count), --refresh-minmax (min_price_ils through the policy function), plus the safety
         properties on a v2 manifest of the real 3 REPAIR rows: dry verify, canary apply, full-row diff (only price/url/updated_at),
         foreign-brand qualified offer + control offer untouched, idempotent re-apply (guard skip), rollback, rollback refusal
         after a newer legitimate write, wrong sha / wrong count / missing approval refused.
Data Imported/Modified: SANDBOX database only (refuses to run unless ENVIRONMENT=sandbox).
Last Updated: 2026-11-16
"""
import asyncio, gzip, json, os, sys, uuid, hashlib, subprocess
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/maintenance")
assert os.environ.get("ENVIRONMENT") == "sandbox", "sandbox only"
import asyncpg
import cpie_overwrite_repair as R

CLS = "/app/state/wo_classification_1043.json"
MAN = "/app/state/wo_manifest_test.json.gz"
OK = []
def ok(name, cond, extra=""):
    assert cond, f"FAIL {name} {extra}"; OK.append(name); print("PASS", name)

def run(*args, expect_rc=0):
    p = subprocess.run([sys.executable, "/app/maintenance/cpie_overwrite_repair.py", *args], capture_output=True, text=True)
    assert p.returncode == expect_rc, f"rc={p.returncode} {p.stdout[-600:]} {p.stderr[-600:]}"
    return p

def last_json(p):
    return json.loads([l for l in p.stdout.splitlines() if l.startswith("{")][-1])

def build_manifest(cls):
    rows = [d for d in cls if d["verdict"] == "REPAIR"]
    offers = [{"offer_id": d["offer_id"], "current_part_id": d["part_id"], "article": d["article"], "classification": "VERIFIED_WRONG",
               "current": {"price": d["current_at_snapshot"]["price"], "url": d["current_at_snapshot"]["url"], "updated_at": d["current_at_snapshot"]["updated_at"]},
               "repair": {"readiness": "READY", "target_price": d["target"]["price"], "target_url": d["target"]["url"]}} for d in rows]
    with gzip.open(MAN, "wt") as f:
        json.dump({"offers": offers}, f)
    return hashlib.sha256(open(MAN, "rb").read()).hexdigest(), offers

async def main():
    cls = json.load(open(CLS))
    sha, offers = build_manifest(cls)
    ids = [x["offer_id"] for x in offers]
    c = await asyncpg.connect(R.DB)
    man = await c.fetchval("select manufacturer_id from parts_catalog limit 1")
    await c.execute("insert into suppliers(id,name,is_active,country) values($1::uuid,'cpie-test',true,'IE') on conflict (id) do update set is_active=true,country='IE'", R.CPIE_SUPPLIER)
    await c.execute("delete from price_history where source like 'cpie_overwrite_repair%'")
    await c.execute("delete from supplier_parts where id=any($1::uuid[]) or supplier_sku like 'WOQ%'", ids)
    qual, ctrl = [], []
    for x in offers:
        await c.execute("insert into parts_catalog(id,sku,name,manufacturer_id,base_price,min_price_ils,is_active) values($1::uuid,$2,'t',$3,100,$4,true) on conflict(id) do update set base_price=100,min_price_ils=$4,is_active=true",
                        x["current_part_id"], "S" + x["current_part_id"][:12], man, x["current"]["price"])
        await c.execute("insert into supplier_parts(id,supplier_id,part_id,supplier_sku,price_ils,price_usd,supplier_url,updated_at,is_available,warranty_months) values($1::uuid,$2::uuid,$3::uuid,$4,$5,7.5,$6,$7,true,12)",
                        x["offer_id"], R.CPIE_SUPPLIER, x["current_part_id"], x["article"], R.D(x["current"]["price"]), x["current"]["url"], R.ts(x["current"]["updated_at"]))
        # a foreign brand's own qualified offer on ANOTHER part, and a control offer of the same supplier: must never change
        fp = str(uuid.uuid4()); qid = str(uuid.uuid4())
        await c.execute("insert into parts_catalog(id,sku,name,manufacturer_id,base_price,is_active) values($1::uuid,$2,'f',$3,100,true)", fp, "S" + fp[:12], man)
        await c.execute("insert into supplier_parts(id,supplier_id,part_id,supplier_sku,price_ils,price_usd,supplier_url,is_available) values($1::uuid,$2::uuid,$3::uuid,$4::varchar,77.7,1,'http://q/'||$4::varchar,true)", qid, R.CPIE_SUPPLIER, fp, "WOQ" + qid[:8])
        qual.append(qid)
    async def snap(idl):
        return {r["id"]: dict(r) for r in await c.fetch("select id::text id,part_id::text pid,supplier_sku sku,price_ils::text p,supplier_url url,updated_at ua,is_available av from supplier_parts where id=any($1::uuid[])", idl)}
    qseed = await snap(qual); seed = await snap(ids)
    a = ["--manifest", MAN, "--expect-sha256", sha, "--dsn", R.DB, "--expect-count", "3"]
    ok("verify: 3 NOT_APPLIED", last_json(run("--verify", *a))["counts"] == {"NOT_APPLIED": 3})
    run("--verify", "--manifest", MAN, "--expect-sha256", sha, "--dsn", R.DB, expect_rc=1)           # default count 93 refused
    run("--verify", "--manifest", MAN, "--expect-sha256", sha, "--dsn", R.DB, "--expect-count", "4", expect_rc=1)
    run("--verify", "--manifest", MAN, "--expect-sha256", "0" * 64, "--dsn", R.DB, "--expect-count", "3", expect_rc=1)
    run("--apply", "--phase", "canary", *a, "--refresh-minmax", expect_rc=1)                          # no approval ref
    ok("refusals: wrong count (93 default, 4), wrong sha, no approval", True)
    r = last_json(run("--apply", "--phase", "canary", *a, "--approval-ref", "sandbox-wo", "--refresh-minmax"))
    ok("canary applied 3, 0 skipped", r["applied"] == 3 and r["skipped"] == 0, r)
    res = json.load(open(r["result"]))
    now = await snap(ids)
    for x in offers:
        n = now[x["offer_id"]]
        ok(f"{x['article']}: price+url = target, part_id/sku unchanged", R.D(n["p"]) == R.D(x["repair"]["target_price"]) and n["url"] == x["repair"]["target_url"]
           and n["pid"] == seed[x["offer_id"]]["pid"] and n["sku"] == seed[x["offer_id"]]["sku"] and n["av"] == seed[x["offer_id"]]["av"])
    ok("minmax refresh reported for 3 parts, none errored", len(res["minmax_refresh"]) == 3 and not any("error" in m for m in res["minmax_refresh"]), res["minmax_refresh"])
    mins = {r_["id"]: r_["m"] for r_ in await c.fetch("select id::text id,min_price_ils::text m from parts_catalog where id=any($1::uuid[])", [x["current_part_id"] for x in offers])}
    tgt = {x["current_part_id"]: R.D(x["repair"]["target_price"]) for x in offers}
    ok("min_price_ils follows the repaired offer (policy function)", all(R.D(mins[k]) == v for k, v in tgt.items()), (mins, tgt))
    ok("foreign qualified offers untouched", await snap(qual) == qseed)
    fd = last_json(run("--fulldiff", "--result", r["result"], *a))
    ok("fulldiff: only price_ils/supplier_url/updated_at (offers) changed", not fd["rows_with_unexpected_changes"] and set(k for k in fd["changed_column_counts"] if k.startswith("offer.")) <= {"offer.price_ils", "offer.supplier_url", "offer.updated_at"}, fd)
    ac = last_json(run("--accept", "--result", r["result"], "--phase-check", "canary", *a))
    ok("accept: passes on the 3-row run", not ac.get("fails"), ac)
    r2 = last_json(run("--apply", "--phase", "canary", *a, "--approval-ref", "sandbox-wo"))
    ok("idempotent: second apply writes nothing (guard skip)", r2["applied"] == 0 and r2["skipped"] == 3, r2)
    # newer legitimate write on one row -> rollback skips it (exit 2), restores the others
    nv = ids[0]
    await c.execute("update supplier_parts set price_ils=price_ils+1, updated_at=NOW() where id=$1::uuid", nv)
    held = (await snap([nv]))[nv]
    rb = run("--rollback", "--result", r["result"], "--approval-ref", "sandbox-wo", "--refresh-minmax", expect_rc=2)
    now = await snap(ids)
    ok("rollback: newer legitimate write kept, other 2 restored", now[nv] == held and all(now[i]["p"] == seed[i]["p"] and now[i]["url"] == seed[i]["url"] for i in ids[1:]), (now, seed))
    ok("rollback: foreign qualified offers untouched", await snap(qual) == qseed)
    mins2 = {r_["id"]: r_["m"] for r_ in await c.fetch("select id::text id,min_price_ils::text m from parts_catalog where id=any($1::uuid[])", [x["current_part_id"] for x in offers])}
    ok("rollback: min_price_ils restored to the restored offer price (policy refresh)", all(R.D(mins2[x["current_part_id"]]) == R.D(seed[x["offer_id"]]["p"]) for x in offers[1:]), (mins2, seed))
    run("--apply", "--phase", "canary", *a, "--approval-ref", "t", "--refresh-minmax", "--dsn", "postgresql://other/x", expect_rc=1)
    ok("refresh-minmax with a foreign --dsn refused", True)
    print(f"ALL {len(OK)} CHECKS PASSED")
    await c.close()

asyncio.run(main())
