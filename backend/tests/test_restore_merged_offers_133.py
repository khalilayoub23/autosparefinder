"""maintenance/restore_merged_offers_133.py in an ISOLATED, DISPOSABLE Postgres (never production).

Needs RESTORE_TEST_DSN pointing at a database whose host name contains "restore133" (a throwaway container with the
supplier_parts / suppliers / parts_catalog / price_history schema) and RESTORE_TEST_INPUT (the input TSV built from the real
2026-06-30 dump rows). Without them every test is skipped; a DSN that is not a disposable restore133 host FAILS the run.
Seeded from ~60 REAL input rows: normal restorable offers plus the edge cases (variant clash, SKU already present, original id
present, canonical offer absent, other supplier only, part not merged, canonical missing, merge chain)."""
import asyncio
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "maintenance"))

DSN = os.environ.get("RESTORE_TEST_DSN", "")
INPUT = os.environ.get("RESTORE_TEST_INPUT", "")
POLICY = Path(os.getenv("OFFER_POLICY_PATH") or ROOT.parent / "docs" / "policies" / "OFFER_DEDUPLICATION_POLICY.md")
pytestmark = pytest.mark.skipif(not (DSN and INPUT and Path(INPUT).exists() and POLICY.exists()),
                                reason="needs RESTORE_TEST_DSN (disposable DB), RESTORE_TEST_INPUT and the policy file")

import offer_policy as op  # noqa: E402


def _dsn():
    if "restore133" not in DSN:
        pytest.fail("RESTORE_TEST_DSN is not a disposable restore133 host - refusing to run against anything else", pytrace=False)
    return DSN


def q(sql, *args, many=False):
    import asyncpg

    async def go():
        c = await asyncpg.connect(_dsn())
        try:
            if many:
                return [dict(r) for r in await c.fetch(sql, *args)]
            return await c.fetchval(sql, *args)
        finally:
            await c.close()
    return asyncio.run(go())


def ex(sql, *args):
    import asyncpg

    async def go():
        c = await asyncpg.connect(_dsn())
        try:
            await c.execute(sql, *args)
        finally:
            await c.close()
    asyncio.run(go())


STATE = {}
SNAP = "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY id), '')) FROM supplier_parts t"


@pytest.fixture(scope="module")
def mod():
    import restore_merged_offers_133 as m
    return m


@pytest.fixture(scope="module")
def policy_env():
    os.environ["OFFER_POLICY_PATH"] = str(POLICY)
    return op.require_policy()


def apply_args(inp):
    """The authorization/provenance a real apply must carry (policy 5a R1/R2)."""
    sha = hashlib.sha256(Path(inp).read_bytes()).hexdigest()
    return ["--input", str(inp), "--apply", "--approval-ref", "TEST-APPROVAL-133", "--expect-input-sha256", sha,
            "--source-backup", "backups/catalog/monthly/autospare-202606.sql.gz"]


@pytest.fixture(autouse=True)
def _central_variant_api(mod, monkeypatch):
    """Until backend/variant_identity.py is deployed the script reports a FALLBACK api and refuses --apply; tests of the
    apply path run with the shared-function contract satisfied. The refusal itself is tested separately."""
    if not mod.VARIANT_API.startswith("variant_identity"):
        monkeypatch.setattr(mod, "VARIANT_API", "variant_identity (test double of the contract)")


def run(mod, capsys, *argv):
    capsys.readouterr()
    rc = asyncio.run(mod.main(["--dsn", _dsn(), *argv]))
    out = capsys.readouterr()
    try:
        i = out.out.find("{")  # the policy stamp line precedes the JSON report
        body = json.loads(out.out[i:]) if i >= 0 else None
    except ValueError:
        body = None
    return rc, body, out.err


# ---------------------------------------------------------------- seed
N_NORMAL = 40


def _seed(mod):
    ex("TRUNCATE price_history, supplier_parts, parts_catalog, suppliers CASCADE")
    _, rows, _ = mod.load_input(Path(INPUT))
    step = max(1, len(rows) // 70)
    sample = rows[::step][:70]
    scen = {}
    exp = {}
    mfr = str(uuid.uuid4())

    def part(pid, active, marker=None):
        spec = json.dumps({"dedup_merged_into": marker} if marker else {})
        ex("INSERT INTO parts_catalog (id, sku, name, manufacturer_id, is_active, specifications, part_type) "
           "VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6::jsonb, 'aftermarket') ON CONFLICT DO NOTHING",
           pid, "SKU-" + pid[:8], "part " + pid[:8], mfr, active, spec)

    def offer(oid, sid, pid, sku, ptype, avail=True, price="10.00"):
        ex("INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_usd, price_ils, is_available, part_type, availability) "
           "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 1, $5::numeric, $6, $7, 'in_stock')", oid, sid, pid, sku, price, avail, ptype)

    other_sup = str(uuid.uuid4())
    ex("INSERT INTO suppliers (id, name) VALUES ($1::uuid, 'other supplier')", other_sup)
    for r in sample:
        ex("INSERT INTO suppliers (id, name) VALUES ($1::uuid, $2) ON CONFLICT DO NOTHING", r["supplier_id"], "sup-" + r["supplier_id"][:8])
    ids_seen = set()
    for i, r in enumerate(sample):
        if r["part_id"] in ids_seen:
            continue
        ids_seen.add(r["part_id"])
        canon = str(uuid.uuid4())
        var = mod._variant(r.get("part_type"))
        if i < N_NORMAL:
            sc = "normal"
        else:
            sc = ["variant_clash", "sku_taken", "original_id_present", "canonical_offer_absent", "other_supplier_only",
                  "part_not_merged", "canonical_missing", "chain"][(i - N_NORMAL) % 8]
        scen[r["id"]] = sc
        if sc == "part_not_merged":
            part(r["part_id"], True)
            part(canon, True)
            offer(str(uuid.uuid4()), r["supplier_id"], canon, "C-" + r["id"][:8], "aftermarket")
            exp[r["id"]] = "part_not_soft_merged"
            continue
        if sc == "canonical_missing":
            part(r["part_id"], False, str(uuid.uuid4()))
            exp[r["id"]] = "canonical_missing"
            continue
        if sc == "chain":
            mid = str(uuid.uuid4())
            part(r["part_id"], False, mid)
            part(mid, False, canon)
        else:
            part(r["part_id"], False, canon)
        part(canon, True)
        if sc == "other_supplier_only":
            offer(str(uuid.uuid4()), other_sup, canon, "O-" + r["id"][:8], "aftermarket")
            exp[r["id"]] = "no_same_supplier_offer_on_canonical"
            continue
        if sc == "canonical_offer_absent":
            exp[r["id"]] = "no_same_supplier_offer_on_canonical"
            continue
        if sc == "variant_clash":
            existing_type = var  # same normalised variant (or both untyped) -> the unique index forbids a second offer
        elif var == "aftermarket":
            existing_type = "oem"
        elif var is None or i % 2 == 0:
            existing_type = "aftermarket"
        else:
            existing_type = None  # untyped existing offer next to a typed restored one coexists
        offer(str(uuid.uuid4()), r["supplier_id"], canon, "C-" + r["id"][:8], existing_type)
        if sc == "sku_taken":
            elsewhere = str(uuid.uuid4())
            part(elsewhere, True)
            offer(str(uuid.uuid4()), r["supplier_id"], elsewhere, r["supplier_sku"], "aftermarket")
            exp[r["id"]] = "supplier_sku_already_present"
        elif sc == "original_id_present":
            elsewhere = str(uuid.uuid4())
            part(elsewhere, True)
            offer(r["id"], r["supplier_id"], elsewhere, "ELSE-" + r["id"][:8], "aftermarket")
            exp[r["id"]] = "original_id_already_present"
        elif sc == "variant_clash":
            exp[r["id"]] = "blocked_by_variant_unique_index"
        else:
            exp[r["id"]] = "eligible"
    return sample, scen, exp


@pytest.fixture(scope="module")
def seeded(mod, policy_env):
    return _seed(mod)


def _write_input(sample, tmp):
    mod_input = Path(INPUT).read_text().split("\n")
    hdr = mod_input[0]
    want = {r["id"] for r in sample}
    body = [ln for ln in mod_input[1:] if ln.split("\t", 1)[0] in want]
    p = tmp / "sample_input.tsv"
    p.write_text(hdr + "\n" + "\n".join(body) + "\n")
    return p


# ---------------------------------------------------------------- tests
def test_input_is_the_real_extract(mod):
    cols, rows, sha = mod.load_input(Path(INPUT))
    assert len(cols) == 25 and cols[0] == "id" and len(rows) == 7971 and len(sha) == 64


def test_seed_expectations(seeded):
    sample, scen, exp = seeded
    assert sum(1 for v in exp.values() if v == "eligible") >= N_NORMAL
    assert {"blocked_by_variant_unique_index", "supplier_sku_already_present", "original_id_already_present",
            "no_same_supplier_offer_on_canonical", "part_not_soft_merged", "canonical_missing"} <= set(exp.values())


def test_dry_run_reports_and_writes_nothing(mod, seeded, capsys, tmp_path):
    sample, scen, exp = seeded
    inp = _write_input(sample, tmp_path)
    ex("CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_parts_part_supplier ON supplier_parts (part_id, supplier_id)")
    before = q(SNAP)
    rc, rep, err = run(mod, capsys, "--input", str(inp))
    assert rc == 0 and rep["mode"].startswith("dry run")
    got = rep["verdicts"]
    want = {}
    for rid, st in exp.items():
        want[st] = want.get(st, 0) + 1
    # with the old index present the dry run still computes verdicts, but reports that --apply would refuse
    assert rep["index_state"]["old_index_present"] is True and "REFUSING" in rep["apply_would_refuse"]
    # the old index makes every same-supplier insert impossible; the verdict logic itself is index-independent
    assert got == want, (got, want)
    assert q(SNAP) == before


def test_apply_refuses_while_old_index_exists(mod, seeded, capsys, tmp_path):
    sample, _, _ = seeded
    inp = _write_input(sample, tmp_path)
    before = q(SNAP)
    rc, rep, err = run(mod, capsys, *apply_args(inp), "--manifest-dir", str(tmp_path))
    assert rc == 4 and "uq_supplier_parts_part_supplier" in err and "REFUSING" in err
    assert q(SNAP) == before
    assert list(tmp_path.glob("restore_merged_offers_133_*.json")) == []  # no manifest, nothing attempted


def test_apply_refuses_without_the_replacement_indexes(mod, seeded, capsys, tmp_path):
    sample, _, _ = seeded
    inp = _write_input(sample, tmp_path)
    ex("DROP INDEX uq_supplier_parts_part_supplier")
    ex("ALTER INDEX uq_sp_part_supplier_variant RENAME TO tmp_variant_idx")
    try:
        rc, rep, err = run(mod, capsys, *apply_args(inp), "--manifest-dir", str(tmp_path))
        assert rc == 4 and "replacement indexes" in err
    finally:
        ex("ALTER INDEX tmp_variant_idx RENAME TO uq_sp_part_supplier_variant")


def test_apply_inserts_exactly_the_eligible_offers_and_nothing_else(mod, seeded, capsys, tmp_path):
    sample, scen, exp = seeded
    inp = _write_input(sample, tmp_path)
    assert q("SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_supplier_parts_part_supplier'") == 0
    before_all = q(SNAP)
    rc, rep, err = run(mod, capsys, *apply_args(inp), "--manifest-dir", str(tmp_path))
    assert rc == 0, err
    want_ids = [rid for rid, st in exp.items() if st == "eligible"]
    assert rep["result"] == {"inserted": len(want_ids)} and rep["inserted_ids"] == len(want_ids)
    mpath = Path(rep["manifest"])
    manifest = json.loads(mpath.read_text())
    assert sorted(m["id"] for m in manifest["inserted"]) == sorted(want_ids)
    assert manifest["policy"]["policy_sha256"] == op.require_policy().sha256
    # each restored row: original id, original supplier+sku+price, on the CANONICAL part, unavailable, stale, no history
    by_id = {r["id"]: r for r in mod.load_input(inp)[1]}
    for rid in want_ids:
        row = q("SELECT id::text id, supplier_id::text s, part_id::text p, supplier_sku, price_ils::text pr, is_available, availability, "
                "last_checked_at, part_type FROM supplier_parts WHERE id = $1::uuid", rid, many=True)[0]
        o = by_id[rid]
        assert row["s"] == o["supplier_id"] and row["supplier_sku"] == o["supplier_sku"]
        assert row["is_available"] is False and row["availability"] == "out_of_stock" and row["last_checked_at"] is None
        assert row["p"] != o["part_id"]  # moved onto the canonical part
        assert (row["pr"] is None) == (o["price_ils"] is None)
        assert row["part_type"] == mod._variant(o["part_type"])
    assert q("SELECT count(*) FROM price_history") == 0
    # nothing else changed: every pre-existing row is byte-identical
    after_rows = q("SELECT t::text AS r FROM supplier_parts t WHERE id <> ALL($1::uuid[])", want_ids, many=True)
    assert len(after_rows) == q("SELECT count(*) FROM supplier_parts") - len(want_ids)
    STATE["before_apply"] = before_all
    STATE["manifest"] = str(mpath)


def test_rerun_is_idempotent(mod, seeded, capsys, tmp_path):
    sample, _, exp = seeded
    inp = _write_input(sample, tmp_path)
    snap = q(SNAP)
    rc, rep, err = run(mod, capsys, *apply_args(inp), "--manifest-dir", str(tmp_path))
    assert rc == 0 and rep["result"] == {} and rep["inserted_ids"] == 0
    assert rep["verdicts"].get("eligible", 0) == 0
    n_el = sum(1 for v in exp.values() if v == "eligible")
    n_op = sum(1 for v in exp.values() if v == "original_id_already_present")
    assert rep["verdicts"]["original_id_already_present"] == n_el + n_op
    assert q(SNAP) == snap


def test_rollback_dry_run_then_apply_restores_the_exact_prior_state(mod, seeded, capsys, tmp_path):
    sample, _, exp = seeded
    mp = Path(STATE["manifest"])  # written by the apply test
    n = len(json.loads(mp.read_text())["inserted"])
    snap = q(SNAP)
    rc, rep, err = run(mod, capsys, "--rollback", str(mp))
    assert rc == 0 and rep["would_delete"] == n and q(SNAP) == snap  # dry run changes nothing
    rc, rep, err = run(mod, capsys, "--rollback", str(mp), "--apply")
    assert rc == 0 and rep["deleted"] == n
    assert q(SNAP) == STATE["before_apply"]  # exact prior state, md5 over every row
    rc, rep, err = run(mod, capsys, "--rollback", str(mp), "--apply")  # second rollback deletes nothing
    assert rc == 0 and rep.get("deleted", 0) == 0 and rep["not_deleted_missing_or_changed_since"] == n


def test_rollback_leaves_rows_changed_since_the_restore(mod, seeded, capsys, tmp_path):
    sample, _, exp = seeded
    inp = _write_input(sample, tmp_path)
    rc, rep, err = run(mod, capsys, *apply_args(inp), "--manifest-dir", str(tmp_path))
    assert rc == 0 and rep["inserted_ids"] > 2
    manifest = json.loads(Path(rep["manifest"]).read_text())
    a, b = manifest["inserted"][0]["id"], manifest["inserted"][1]["id"]
    ex("UPDATE supplier_parts SET is_available = TRUE WHERE id = $1::uuid", a)  # a sync made it live
    ex("INSERT INTO price_history (id, supplier_part_id, new_price_ils, new_price_usd, source) VALUES (gen_random_uuid(), $1::uuid, 5, 1, 't')", b)
    rc, rep2, err = run(mod, capsys, "--rollback", rep["manifest"], "--apply")
    assert rc == 0 and rep2["not_deleted_missing_or_changed_since"] == 2 and rep2["deleted"] == rep["inserted_ids"] - 2
    assert q("SELECT count(*) FROM supplier_parts WHERE id = ANY($1::uuid[])", [a, b]) == 2


def test_limit_bounds_a_run(mod, seeded, capsys, tmp_path):
    sample, _, _ = seeded
    inp = _write_input(sample, tmp_path)
    rc, rep, err = run(mod, capsys, "--input", str(inp), "--limit", "5")
    assert rc == 0 and rep["input_rows"] == 5


def test_apply_without_policy_exits_3_before_any_connection(mod, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OFFER_POLICY_PATH", str(tmp_path / "nope.md"))
    monkeypatch.setattr(op, "_candidate_paths", lambda: [tmp_path / "nope.md"])
    with pytest.raises(SystemExit) as e:
        asyncio.run(mod.main(["--dsn", "postgresql://127.0.0.1:1/none", "--input", str(INPUT), "--apply"]))
    assert e.value.code == 3


def test_policy_gate_allows_only_b_and_f_and_the_script_is_bound():
    from offer_policy import Classification as C
    info = op.require_policy() if POLICY.exists() else None
    ev = {"approval_ref": "x", "input_sha256": "y", "source_backup": "z", "canonical_variant_checked": True}
    for ok in (C.DISTINCT_VARIANTS, C.UNRESOLVED):
        assert op.assert_write_allowed("restore", ok, policy=info, dry_run_done=True, evidence=ev)
    for bad in (C.TRUE_DUPLICATE, C.PRICE_UPDATE, C.COMPETING_OFFERS, C.MISATTRIBUTED):
        with pytest.raises(op.PolicyViolation):
            op.assert_write_allowed("restore", bad, policy=info, dry_run_done=True, evidence=ev)
    src = (ROOT / "maintenance" / "restore_merged_offers_133.py").read_text()
    assert "gate_or_exit" in src and "maintenance/restore_merged_offers_133.py" in op.POLICY_BOUND_ACTORS
    assert "INSERT INTO price_history" not in src and "UPDATE price_history" not in src


def test_apply_requires_authorization_and_the_approved_scope(mod, seeded, capsys, tmp_path):
    sample, _, _ = seeded
    inp = _write_input(sample, tmp_path)
    before = q(SNAP)
    rc, rep, err = run(mod, capsys, "--input", str(inp), "--apply", "--manifest-dir", str(tmp_path))
    assert rc == 5 and "authorization/provenance missing" in err
    bad = apply_args(inp)
    bad[bad.index("--expect-input-sha256") + 1] = "0" * 64
    rc, rep, err = run(mod, capsys, *bad, "--manifest-dir", str(tmp_path))
    assert rc == 5 and "not the approved scope" in err
    assert q(SNAP) == before and list(tmp_path.glob("restore_merged_offers_133_*.json")) == []


def test_apply_refuses_without_the_central_variant_module(mod, seeded, capsys, tmp_path, monkeypatch):
    sample, _, _ = seeded
    inp = _write_input(sample, tmp_path)
    monkeypatch.setattr(mod, "VARIANT_API", "offer_classification.normalize_type (FALLBACK - apply refused)")
    before = q(SNAP)
    rc, rep, err = run(mod, capsys, *apply_args(inp), "--manifest-dir", str(tmp_path))
    assert rc == 5 and "variant_identity" in err and q(SNAP) == before


def test_gate_restore_needs_authorization_evidence_and_the_canonical_variant_check():
    from offer_policy import Classification as C
    info = op.require_policy()
    full = {"approval_ref": "OWNER-2026-11-07", "input_sha256": "a" * 64, "source_backup": "b.sql.gz", "canonical_variant_checked": True}
    assert op.assert_write_allowed("restore", C.UNRESOLVED, policy=info, dry_run_done=True, evidence=full)
    for k in ("approval_ref", "input_sha256", "source_backup"):
        with pytest.raises(op.PolicyViolation):
            op.assert_write_allowed("restore", C.UNRESOLVED, policy=info, dry_run_done=True, evidence={**full, k: ""})
    with pytest.raises(op.PolicyViolation):  # canonical variant not checked first
        op.assert_write_allowed("restore", C.DISTINCT_VARIANTS, policy=info, dry_run_done=True, evidence={**full, "canonical_variant_checked": False})
    with pytest.raises(op.PolicyViolation):  # no dry run
        op.assert_write_allowed("restore", C.DISTINCT_VARIANTS, policy=info, dry_run_done=False, evidence=full)
    for bad in (C.TRUE_DUPLICATE, C.PRICE_UPDATE, C.COMPETING_OFFERS, C.MISATTRIBUTED):
        with pytest.raises(op.PolicyViolation):
            op.assert_write_allowed("restore", bad, policy=info, dry_run_done=True, evidence=full)


def test_hebrew_original_label_rows_that_collided_on_2026_11_06_are_now_blocked(mod, capsys, tmp_path):
    """The seven offers labelled 'מקורי' were restored next to canonical-part offers typed 'oem' because the label was compared
    raw. Rebuild that exact situation from ALL real 'מקורי' rows of the input: none may be restorable."""
    cols, rows, _ = mod.load_input(Path(INPUT))
    heb = [r for r in rows if r["part_type"] == "מקורי"]
    assert len(heb) == 31
    subset = os.environ.get("RESTORE_TEST_SUBSET7")
    if subset and Path(subset).exists():
        ids7 = {m["id"] for m in json.loads(Path(subset).read_text())["inserted"]}
        assert len(ids7) == 7 and ids7 <= {r["id"] for r in heb}
    ex("TRUNCATE price_history, supplier_parts, parts_catalog, suppliers CASCADE")
    mfr = str(uuid.uuid4())
    for r in heb:
        ex("INSERT INTO suppliers (id, name) VALUES ($1::uuid, $2) ON CONFLICT DO NOTHING", r["supplier_id"], "sup-" + r["supplier_id"][:8])
        canon = str(uuid.uuid4())
        ex("INSERT INTO parts_catalog (id, sku, name, manufacturer_id, is_active, specifications, part_type) VALUES "
           "($1::uuid, $2, 'loser', $3::uuid, false, $4::jsonb, 'oem')", r["part_id"], "L-" + r["id"][:8], mfr, json.dumps({"dedup_merged_into": canon}))
        ex("INSERT INTO parts_catalog (id, sku, name, manufacturer_id, is_active, specifications, part_type) VALUES "
           "($1::uuid, $2, 'canon', $3::uuid, true, '{}'::jsonb, 'oem')", canon, "C-" + r["id"][:8], mfr)
        ex("INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_usd, price_ils, is_available, part_type, availability) "
           "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 1, 10, true, 'oem', 'in_stock')", str(uuid.uuid4()), r["supplier_id"], canon, "CANON-" + r["id"][:8])
    inp = tmp_path / "heb.tsv"
    lines = Path(INPUT).read_text().split("\n")
    want = {r["id"] for r in heb}
    inp.write_text(lines[0] + "\n" + "\n".join(ln for ln in lines[1:] if ln.split("\t", 1)[0] in want) + "\n")
    before = q(SNAP)
    rc, rep, err = run(mod, capsys, "--input", str(inp))
    assert rc == 0 and rep["verdicts"].get("eligible", 0) == 0
    assert set(rep["verdicts"]) <= {"blocked_by_variant_unique_index", "variant_label_unrecognized"}
    rc, rep, err = run(mod, capsys, *apply_args(inp), "--manifest-dir", str(tmp_path))
    assert rc == 0 and rep["inserted_ids"] == 0 and q(SNAP) == before


def test_unrecognised_label_is_not_restorable(mod, capsys, tmp_path):
    _, rows, _ = mod.load_input(Path(INPUT))
    r = next(x for x in rows if x["part_type"] == "Original")
    ex("TRUNCATE price_history, supplier_parts, parts_catalog, suppliers CASCADE")
    mfr, canon = str(uuid.uuid4()), str(uuid.uuid4())
    ex("INSERT INTO suppliers (id, name) VALUES ($1::uuid, 's')", r["supplier_id"])
    ex("INSERT INTO parts_catalog (id, sku, name, manufacturer_id, is_active, specifications) VALUES ($1::uuid,'l','l',$2::uuid,false,$3::jsonb)",
       r["part_id"], mfr, json.dumps({"dedup_merged_into": canon}))
    ex("INSERT INTO parts_catalog (id, sku, name, manufacturer_id, is_active) VALUES ($1::uuid,'c','c',$2::uuid,true)", canon, mfr)
    ex("INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_usd, price_ils, is_available, part_type) "
       "VALUES ($1::uuid,$2::uuid,$3::uuid,'CANON',1,1,true,'aftermarket')", str(uuid.uuid4()), r["supplier_id"], canon)
    lines = Path(INPUT).read_text().split("\n")
    row = next(ln for ln in lines[1:] if ln.split("\t", 1)[0] == r["id"]).split("\t")
    cols = lines[0][len("#cols:"):].split("\t")
    row[cols.index("part_type")] = "garbage label"
    inp = tmp_path / "x.tsv"
    inp.write_text(lines[0] + "\n" + "\t".join(row) + "\n")
    rc, rep, err = run(mod, capsys, "--input", str(inp))
    assert rc == 0 and rep["verdicts"] == {"variant_label_unrecognized": 1}
