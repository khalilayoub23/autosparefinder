"""
Script:  devtests/cpie_overwrite_repair_sandbox_test.py
Purpose: Sandbox-only end-to-end test of maintenance/cpie_overwrite_repair.py on a seeded copy of the 93 READY offers.
Process: seed (manifest 'current' values) -> verify -> canary(20) -> verify fields -> rollback canary -> canary again ->
         rest(73) -> all 93 repaired -> full rollback -> re-apply -> idempotency -> guards (newer write, changed price/url,
         changed part, wrong sha, concurrency lock) -> control offers untouched.
Data Imported/Modified: SANDBOX database only (refuses to run unless ENVIRONMENT=sandbox).
Data Sources: forensics manifest copied to /app/state/ovr_manifest.json.gz.
Last Updated: 2026-11-15
"""
import asyncio, gzip, json, os, sys, uuid, hashlib, subprocess
from decimal import Decimal
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/maintenance")
assert os.environ.get("ENVIRONMENT") == "sandbox", "sandbox only"
import asyncpg
import cpie_overwrite_repair as R

MAN = "/app/state/ovr_manifest.json.gz"
SHA = hashlib.sha256(open(MAN, "rb").read()).hexdigest()
DSN = R.DB
OK = []
def ok(name, cond, extra=""):
    assert cond, f"FAIL {name} {extra}"; OK.append(name); print("PASS", name)

def run(*args, expect_rc=0):
    p = subprocess.run([sys.executable, "/app/maintenance/cpie_overwrite_repair.py", *args], capture_output=True, text=True)
    assert p.returncode == expect_rc, f"rc={p.returncode} {p.stdout[-600:]} {p.stderr[-600:]}"
    return p

def last_json(p):
    return json.loads([l for l in p.stdout.splitlines() if l.startswith("{")][-1])

async def snapshot(c, ids):
    return {r["id"]: dict(r) for r in await c.fetch("select id::text id,part_id::text pid,supplier_id::text sid,supplier_sku sku,price_ils::text p,price_usd::text u,supplier_url url,updated_at ua,is_available av,warranty_months wm from supplier_parts where id=any($1::uuid[])", ids)}

async def seed_all(c, rows, ids):
    man_brand = await c.fetchval("select manufacturer_id from parts_catalog limit 1")
    await c.execute("delete from price_history where source like 'cpie_overwrite_repair%'")
    await c.execute("delete from supplier_parts where supplier_id=$1::uuid and supplier_sku like 'OVR%'", R.CPIE_SUPPLIER)
    await c.execute("delete from supplier_parts where id=any($1::uuid[])", ids)
    await c.execute("insert into suppliers(id,name,is_active,country) values($1::uuid,'cpie-test',true,'IE') on conflict (id) do update set is_active=true,country='IE'", R.CPIE_SUPPLIER)
    ctrl = []
    for x in rows + [None] * 3:
        pid = x["current_part_id"] if x else str(uuid.uuid4())
        await c.execute("insert into parts_catalog(id,sku,name,manufacturer_id,base_price,is_active) values($1::uuid,$2,'t',$3,100,true) on conflict(id) do update set base_price=100, is_active=true", pid, "S" + pid[:12], man_brand)
        if x:
            await c.execute("insert into supplier_parts(id,supplier_id,part_id,supplier_sku,price_ils,price_usd,supplier_url,updated_at,is_available,warranty_months) values($1::uuid,$2::uuid,$3::uuid,$4,$5,7.5,$6,$7,true,12)",
                            x["offer_id"], R.CPIE_SUPPLIER, pid, x["article"], R.D(x["current"]["price"]), x["current"]["url"], R.ts(x["current"]["updated_at"]))
        else:
            cid = str(uuid.uuid4()); ctrl.append(cid)
            await c.execute("insert into supplier_parts(id,supplier_id,part_id,supplier_sku,price_ils,price_usd,supplier_url,is_available) values($1::uuid,$2::uuid,$3::uuid,$4::varchar,55.55,1,'http://x/'||$4::varchar,true)", cid, R.CPIE_SUPPLIER, pid, "OVR" + cid[:8])
    return ctrl


async def exclude_phase(c, rows, ids, a):
    """The production shape: 93 manifest rows, one excluded + legitimately rewritten -> canary 20 of 92, rest 72."""
    await seed_all(c, rows, ids)
    ex = rows[3]["offer_id"]
    await c.execute("update supplier_parts set price_ils=224.01, updated_at=NOW() where id=$1::uuid", ex)
    held = (await snapshot(c, [ex]))[ex]
    e = ["--exclude-offer", ex]
    ok("exclude: verify counts 92 NOT_APPLIED", last_json(run("--verify", *a, *e))["counts"] == {"NOT_APPLIED": 92})
    run("--verify", *a, "--exclude-offer", str(uuid.uuid4()), expect_rc=1)
    r1 = last_json(run("--apply", "--phase", "canary", *a, *e, "--approval-ref", "t"))
    ok("exclude: canary 20 applied, 0 skipped (excluded row not in the batch)", r1["applied"] == 20 and r1["skipped"] == 0, r1)
    r2 = last_json(run("--apply", "--phase", "rest", *a, *e, "--approval-ref", "t", "--canary-result", r1["result"]))
    ok("exclude: rest 72 applied, 0 skipped", r2["applied"] == 72 and r2["skipped"] == 0, r2)
    ok("exclude: excluded row keeps its newer legitimate value", (await snapshot(c, [ex]))[ex] == held)
    ok("exclude: 92 VERIFIED_REPAIRED", last_json(run("--verify", *a, *e))["counts"] == {"VERIFIED_REPAIRED": 92})
    fd1 = last_json(run("--fulldiff", "--result", r1["result"], *a, *e)); fd2 = last_json(run("--fulldiff", "--result", r2["result"], *a, *e))
    ok("exclude: full-row diff clean for canary (20) and rest (72), every row actually changed", not fd1["rows_with_unexpected_changes"] and not fd2["rows_with_unexpected_changes"]
       and fd1["rows_checked"]["offers"] == 20 and fd2["rows_checked"]["offers"] == 72 and fd1["changed_column_counts"].get("offer.supplier_url") == 20 and fd2["changed_column_counts"].get("offer.supplier_url") == 72, (fd1, fd2))


async def rebase_phase(c, rows, ids, a):
    import price_policy as pp
    ctrl = await seed_all(c, rows, ids)
    pids = [x["current_part_id"] for x in rows]
    ctrl_parts = [r["part_id"] for r in await c.fetch("select part_id::text part_id from supplier_parts where id=any($1::uuid[])", ctrl)]
    async def parts(ids_):
        return {r["id"]: dict(r) for r in await c.fetch("select id::text id, to_jsonb(pc)::text j, base_price::text bp from parts_catalog pc where id=any($1::uuid[])", ids_)}
    r1 = last_json(run("--apply", "--phase", "canary", *a, "--approval-ref", "t")); can = r1["result"]
    r2 = last_json(run("--apply", "--phase", "rest", *a, "--approval-ref", "t", "--canary-result", can)); rest = r2["result"]
    merged = json.load(open(can)); merged["done"] += json.load(open(rest))["done"]; json.dump(merged, open("/app/state/ovr_merged_result.json", "w"))
    MR = "/app/state/ovr_merged_result.json"
    done_parts = sorted({d["part_id"] for d in merged["done"]})
    cpar0 = await parts(ctrl_parts)
    # legit concurrent base_price writer on one part, a part already at target, a part locked by another transaction
    await c.execute("update parts_catalog set base_price=77 where id=$1::uuid", done_parts[0])
    tgt = lambda price: R.D(pp.unit_price(pp.offer_cost_ils(float(price), 7.5, 3.72), "cpie-test", "IE").net)
    live_price = {r["part_id"]: R.D(r["price_ils"]) for r in await c.fetch("select part_id::text part_id, price_ils from supplier_parts where id=any($1::uuid[])", ids)}
    await c.execute("update parts_catalog set base_price=$2 where id=$1::uuid", done_parts[1], tgt(live_price[done_parts[1]]))
    holder = await asyncpg.connect(R.DB); tx = holder.transaction(); await tx.start()
    await holder.fetchrow("select 1 from parts_catalog where id=$1::uuid for update", done_parts[2])
    before = await parts(pids)
    out = last_json(run("--rebase", "--result", can, "--also-result", rest))
    await tx.rollback(); await holder.close()
    rr = json.load(open(out["rebase"]))
    sk = {x["part_id"]: x["reason"] for x in rr["skipped"]}
    ok("rebase: exactly 3 skipped = legit-changed, off-baseline, locked", out["skipped"] == 3 and sk == {done_parts[0]: "base_price_changed_since_backup", done_parts[1]: "base_price_changed_since_backup", done_parts[2]: "locked_by_other_writer"}, sk)
    after = await parts(pids)
    ok("rebase: legit write (77) preserved", after[done_parts[0]]["bp"] == "77.00" or R.D(after[done_parts[0]]["bp"]) == R.D(77))
    ok("rebase: locked part untouched", after[done_parts[2]]["j"] == before[done_parts[2]]["j"])
    exp_changed = 0
    for pid in done_parts[3:]:  # parts 0-2 are the three skipped ones
        want = tgt(live_price[pid])
        assert R.D(after[pid]["bp"]) == want, (pid, after[pid]["bp"], want)
        exp_changed += 1 if R.D(before[pid]["bp"]) != want else 0
    ok("rebase: every changed part == round(repaired price x 1.45, 2) (valid basis)", out["changed"] == exp_changed and exp_changed > 0, (out, exp_changed))
    ok("rebase: off-baseline part not touched", after[done_parts[1]]["j"] == before[done_parts[1]]["j"])
    # side effects: other columns and unrelated parts
    bk_changed = set()
    for pid in done_parts[3:]:
        b_, a_ = json.loads(before[pid]["j"]), json.loads(after[pid]["j"])
        bk_changed |= {k for k in set(b_) | set(a_) if b_.get(k) != a_.get(k)}
    ok("rebase side effects: only base_price/updated_at columns differ", bk_changed <= {"base_price", "updated_at"}, bk_changed)
    ok("rebase side effects: unrelated parts untouched", await parts(ctrl_parts) == cpar0)
    # idempotency (second pass with --prior-rebase): zero changes
    out2 = last_json(run("--rebase", "--result", can, "--also-result", rest, "--prior-rebase", out["rebase"]))
    ok("rebase second pass: only the previously locked part is newly rebased, 90 unchanged", out2["changed"] == 1 and out2["unchanged"] == 90, out2)
    out3 = last_json(run("--rebase", "--result", can, "--also-result", rest, "--prior-rebase", out["rebase"], out2["rebase"]))
    ok("rebase third pass: 0 changed (idempotent)", out3["changed"] == 0 and out3["unchanged"] == 91, out3)
    # newer legitimate write after rebase: rollback must skip it, restore the rest
    newer = done_parts[5]
    await c.execute("update parts_catalog set base_price=55.55 where id=$1::uuid", newer)
    rbk = last_json(run("--rollback-rebase", "--rebase-result", out["rebase"], "--approval-ref", "t", expect_rc=2))
    ok("rebase rollback: 1 newer write skipped, rest restored", rbk["skipped"] == 1 and rbk["restored"] == out["changed"] - 1, rbk)
    fin = await parts(pids)
    ok("rebase rollback: restored parts back to 100, newer write kept", all(R.D(fin[p_]["bp"]) == R.D(100) for p_ in done_parts[3:] if p_ != newer) and R.D(fin[newer]["bp"]) == R.D("55.55"))
    cn = await parts(ctrl_parts)
    dif = {k: [c_ for c_ in set(json.loads(cn[k]["j"])) | set(json.loads(cpar0[k]["j"])) if json.loads(cn[k]["j"]).get(c_) != json.loads(cpar0[k]["j"]).get(c_)] for k in cn if cn[k]["j"] != cpar0[k]["j"]}
    ok("rebase rollback: unrelated parts untouched (base_price never; other diffs listed)", all(cn[k]["bp"] == cpar0[k]["bp"] for k in cn), dif)
    print("   note: unrelated-part column diffs from sandbox background workers:", dif)


async def main():
    rows = R.load_ready(MAN, SHA)
    ids = [x["offer_id"] for x in rows]
    c = await asyncpg.connect(DSN)
    ctrl = await seed_all(c, rows, ids)
    seed = await snapshot(c, ids); cseed = await snapshot(c, ctrl)
    pc_seed = {r["id"]: r["base_price"] for r in await c.fetch("select id::text id, base_price::text from parts_catalog where id=any($1::uuid[])", [x["current_part_id"] for x in rows])}
    a = ["--manifest", MAN, "--expect-sha256", SHA, "--dsn", DSN]
    ok("seed: 93 NOT_APPLIED", last_json(run("--verify", *a))["counts"] == {"NOT_APPLIED": 93})
    # wrong sha / missing approval refused
    run("--verify", "--manifest", MAN, "--expect-sha256", "0" * 64, expect_rc=1)
    run("--apply", "--phase", "canary", *a, expect_rc=1)  # no approval-ref
    ok("refusals: wrong sha, no approval", True)
    # canary
    r = last_json(run("--apply", "--phase", "canary", *a, "--approval-ref", "sandbox-test"))
    ok("canary applied 20", r["applied"] == 20 and r["skipped"] == 0, r)
    canres = r["result"]
    cnt = last_json(run("--verify", *a))["counts"]
    ok("canary state 20 repaired + 73 untouched", cnt == {"VERIFIED_REPAIRED": 20, "NOT_APPLIED": 73}, cnt)
    after = await snapshot(c, ids)
    for i, x in enumerate(rows):
        s, f = seed[x["offer_id"]], after[x["offer_id"]]
        e = R.expected(x)
        if i < 20:
            ok_fields = (f["pid"], f["sid"], f["sku"], f["u"], f["av"], f["wm"]) == (s["pid"], s["sid"], s["sku"], s["u"], s["av"], s["wm"])
            tgt = (f["url"] == e["t_url"]) and (e["t_price"] is None or R.D(f["p"]) == e["t_price"]) and f["ua"] > s["ua"]
            assert ok_fields and tgt, f"canary row {i} wrong {f}"
        else:
            assert f == s, f"row {i} touched too early"
    ok("canary: only price/url/updated_at changed, rows 21-93 untouched", True)
    nph = await c.fetchval("select count(*) from price_history where source='cpie_overwrite_repair'")
    expect_ph = sum(1 for x in rows[:20] if R.expected(x)["t_price"] is not None and R.expected(x)["t_price"] != R.expected(x)["price"])
    ok("canary price_history rows == price changes", nph == expect_ph, (nph, expect_ph))
    ok("controls untouched after canary", await snapshot(c, ctrl) == cseed)
    fd = last_json(run("--fulldiff", "--result", canres, *a))
    ok("full-row diff after canary: only allowed columns changed", not fd["rows_with_unexpected_changes"] and set(fd["changed_column_counts"]) <= {"offer.price_ils","offer.supplier_url","offer.updated_at","part.updated_at"}, fd)
    # rest refused without canary result / with unverified
    run("--apply", "--phase", "rest", *a, "--approval-ref", "t", expect_rc=1)
    # crash path: result file unavailable -> rollback from the write-ahead journal
    jr = json.load(open(canres))["run_id"]
    jpath = f"/app/state/cpie_ownership/overwrite_repair/journal_{jr}.jsonl"
    x40 = rows[40]; e40 = R.expected(x40)
    fake = {"status": "intent", "offer_id": x40["offer_id"], "part_id": x40["current_part_id"], "supplier_sku": x40["article"],
            "before": {"price_ils": str(e40["price"]), "supplier_url": e40["url"], "updated_at": e40["ua"].isoformat()},
            "after": {"price_ils": str(e40["t_price"] or e40["price"]), "supplier_url": e40["t_url"], "updated_at": "2026-11-15T19:00:00"}}
    with open(jpath, "a") as f:  # an intent that never committed (row unchanged) must not break the rollback
        f.write(json.dumps(fake) + "\n")
    rb = last_json(run("--rollback", "--journal", jpath, "--approval-ref", "t", "--dsn", DSN))
    ok("journal rollback restores 20 (uncommitted intent is harmless)", rb["restored"] == 20 and rb["hard_skips"] == 0, rb)
    rb = last_json(run("--rollback", "--result", canres, "--approval-ref", "t", "--dsn", DSN))
    ok("canary rollback after journal rollback restores 0", rb["restored"] == 0, rb)
    back = await snapshot(c, ids)
    ok("rollback exact (price,url,part,sku)", all((back[i]["p"], back[i]["url"], back[i]["pid"], back[i]["sku"]) == (seed[i]["p"], seed[i]["url"], seed[i]["pid"], seed[i]["sku"]) for i in ids))
    rb2 = last_json(run("--rollback", "--result", canres, "--approval-ref", "t", "--dsn", DSN))
    ok("second rollback idempotent (0 restored, only already_rolled_back)", rb2["restored"] == 0 and rb2["hard_skips"] == 0, rb2)
    # rollback state is NOT_APPLIED? updated_at moved -> NEWER_WRITE_SAME_VALUE, so apply must refuse (guard) -> reseed timestamps
    cnt = last_json(run("--verify", *a))["counts"]
    ok("after rollback value==manifest but updated_at newer -> not auto re-applied", cnt.get("NEWER_WRITE_SAME_VALUE") == 20, cnt)
    for x in rows[:20]:
        await c.execute("update supplier_parts set updated_at=$2 where id=$1::uuid", x["offer_id"], R.ts(x["current"]["updated_at"]))
    # canary again, rest
    r = last_json(run("--apply", "--phase", "canary", *a, "--approval-ref", "t")); canres = r["result"]
    ok("canary re-run applied 20", r["applied"] == 20)
    r2 = last_json(run("--apply", "--phase", "rest", *a, "--approval-ref", "t", "--canary-result", canres))
    ok("rest applied 73", r2["applied"] == 73 and r2["skipped"] == 0, r2); resres = r2["result"]
    ok("all 93 VERIFIED_REPAIRED", last_json(run("--verify", *a))["counts"] == {"VERIFIED_REPAIRED": 93})
    ok("controls untouched after full apply", await snapshot(c, ctrl) == cseed)
    # idempotency
    r3 = last_json(run("--apply", "--phase", "canary", *a, "--approval-ref", "t"))
    ok("apply again: 0 applied, 20 guarded skips", r3["applied"] == 0 and r3["skipped"] == 20 and all(s.startswith("guard:VERIFIED_REPAIRED") for s in r3["reasons"]), r3)
    full = await snapshot(c, ids)
    # newer legitimate write on 2 rows -> rollback must skip them
    for i in (0, 50):
        await c.execute("update supplier_parts set price_ils=price_ils+1, updated_at=NOW() where id=$1::uuid", ids[i])
    newer = await snapshot(c, ids)
    # merge canary+rest results for the full rollback
    both = {"done": json.load(open(canres))["done"] + json.load(open(resres))["done"]}
    json.dump(both, open("/app/state/ovr_both.json", "w"))
    rb = run("--rollback", "--result", "/app/state/ovr_both.json", "--approval-ref", "t", "--dsn", DSN, expect_rc=2)
    j = last_json(rb)
    ok("full rollback: 91 restored, 2 newer-write skipped, exit 2", j["restored"] == 91 and j["hard_skips"] == 2, j)
    fin = await snapshot(c, ids)
    ok("skipped rows keep the newer value", all(fin[ids[i]] == newer[ids[i]] for i in (0, 50)))
    ok("restored rows equal seed values", all((fin[i]["p"], fin[i]["url"]) == (seed[i]["p"], seed[i]["url"]) for i in ids if i not in (ids[0], ids[50])))
    ok("rollback wrote compensating history", await c.fetchval("select count(*) from price_history where source='cpie_overwrite_repair_rollback'") > 0)
    ok("rollback never changed part/sku/sid/available/warranty/usd", all((fin[i]["pid"], fin[i]["sku"], fin[i]["sid"], fin[i]["av"], fin[i]["wm"], fin[i]["u"]) == (seed[i]["pid"], seed[i]["sku"], seed[i]["sid"], seed[i]["av"], seed[i]["wm"], seed[i]["u"]) for i in ids))
    # guards on apply: reseed everything
    async def reseed():
        for x in rows:
            await c.execute("update supplier_parts set price_ils=$2,supplier_url=$3,updated_at=$4,part_id=$5::uuid where id=$1::uuid", x["offer_id"], R.D(x["current"]["price"]), x["current"]["url"], R.ts(x["current"]["updated_at"]), x["current_part_id"])
    await reseed()
    await c.execute("update supplier_parts set updated_at=NOW() where id=$1::uuid", ids[1])                       # newer write, same values
    await c.execute("update supplier_parts set price_ils=price_ils+2 where id=$1::uuid", ids[2])                  # changed price
    await c.execute("update supplier_parts set supplier_url='http://other' where id=$1::uuid", ids[3])            # changed url
    await c.execute("update supplier_parts set part_id=$2::uuid where id=$1::uuid", ids[4], rows[5]["current_part_id"])  # changed part
    await c.execute("delete from supplier_parts where id=$1::uuid", ids[5])                                       # missing
    # concurrency: another transaction holds the row lock
    holder = await asyncpg.connect(DSN); tx = holder.transaction(); await tx.start()
    await holder.fetchrow("select 1 from supplier_parts where id=$1::uuid for update", ids[6])
    r = last_json(run("--apply", "--phase", "canary", *a, "--approval-ref", "t"))
    await tx.rollback(); await holder.close()
    reasons = {s["offer_id"]: s["reason"] for s in json.load(open(r["result"]))["skipped"]}
    ok("guards: newer write, price, url, part, missing, locked row all skipped", all(i in reasons for i in ids[1:7]), reasons)
    ok("guards: exactly the 14 other canary rows applied", r["applied"] == 14, r)
    g = await snapshot(c, ids[1:5])
    ok("guarded rows untouched", g[ids[2]]["p"] == str(R.D(rows[2]["current"]["price"] + 2)) and g[ids[3]]["url"] == "http://other")
    ok("controls untouched at the end", await snapshot(c, ctrl) == cseed)
    await exclude_phase(c, rows, ids, a)
    await rebase_phase(c, rows, ids, a)
    await c.close()
    print("ALL", len(OK), "CHECKS PASSED")
asyncio.run(main())
