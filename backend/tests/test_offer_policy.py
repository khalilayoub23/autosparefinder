"""OFFER_DEDUPLICATION_POLICY compliance: the policy file, its loader (fail closed), the six-class classifier, the write gate,
the policy-bound repair agent through its REAL entry point (main()) against the sandbox DB, and static guards that no legacy
routine bypasses the policy. Runs in the sandbox (never against production)."""
import asyncio
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

import pytest

import offer_policy as op
from offer_policy import Classification as C

ROOT = Path(__file__).resolve().parent.parent
POLICY = Path(os.getenv("OFFER_POLICY_PATH") or ROOT.parent / "docs" / "policies" / "OFFER_DEDUPLICATION_POLICY.md")
pytestmark = pytest.mark.skipif(not POLICY.exists(), reason="policy file not mounted in this environment (set OFFER_POLICY_PATH)")

S1, S2 = "s1", "s2"


def offer(**kw):
    base = {"supplier_id": S1, "part_id": "p1", "supplier_sku": "X1", "part_type": "oem", "price_ils": 100.0,
            "is_available": True, "supplier_url": "u", "name": "Pad", "row_id": None}
    base.update(kw)
    return base


# ---------------------------------------------------------------- policy file + loader
def test_policy_loads_with_version_and_hash(monkeypatch):
    monkeypatch.setenv("OFFER_POLICY_PATH", str(POLICY))
    info = op.require_policy()
    assert info.version == "1.1.0"
    assert info.sha256 == hashlib.sha256(POLICY.read_bytes()).hexdigest()
    assert op.audit_stamp(info) == {"policy_version": "1.1.0", "policy_sha256": info.sha256}


def test_policy_change_changes_hash(tmp_path):
    p = tmp_path / "OFFER_DEDUPLICATION_POLICY.md"
    p.write_bytes(POLICY.read_bytes())
    h1 = op.load_policy(p).sha256
    p.write_bytes(POLICY.read_bytes() + b"\n<!-- edit -->\n")
    assert op.load_policy(p).sha256 != h1


@pytest.mark.parametrize("mutate", [lambda t: t.replace("policy-id: offer-deduplication", "policy-id: other"),
                                    lambda t: t.replace("policy-version: 1.1.0", ""),
                                    lambda t: t.replace("`UNRESOLVED`", "`X`")])
def test_malformed_policy_fails_closed(tmp_path, mutate):
    p = tmp_path / "p.md"
    p.write_text(mutate(POLICY.read_text()))
    with pytest.raises(op.PolicyUnavailable):
        op.load_policy(p)


def test_missing_and_outdated_policy_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFER_POLICY_PATH", str(tmp_path / "nope.md"))
    monkeypatch.setattr(op, "_candidate_paths", lambda: [tmp_path / "nope.md"])
    with pytest.raises(op.PolicyUnavailable):
        op.require_policy()
    with pytest.raises(op.PolicyUnavailable):
        op.load_policy(POLICY, min_version="9.0.0")


# ---------------------------------------------------------------- classifier scenarios 1-9, 14
def test_1_2_same_sku_different_suppliers_independent():
    assert op.classify_pair(offer(), offer(supplier_id=S2)) is C.COMPETING_OFFERS
    assert op.classify_pair(offer(), offer(supplier_id=S2, price_ils=250.0)) is C.COMPETING_OFFERS


def test_3_4_same_supplier_variants_separate():
    assert op.classify_pair(offer(part_type="oem"), offer(part_type="aftermarket", supplier_sku="X2")) is C.DISTINCT_VARIANTS
    assert op.classify_pair(offer(part_type="oem"), offer(part_type="oe_equivalent", supplier_sku="X2")) is C.DISTINCT_VARIANTS
    assert op.classify_pair(offer(part_type="original"), offer(part_type="oem")) is not C.DISTINCT_VARIANTS  # same class


def test_5_changed_price_same_offer_is_update_only_with_evidence():
    a, b = offer(price_ils=100.0), offer(price_ils=120.0)
    assert op.classify_pair(a, b, evidence={"later": "b"}) is C.PRICE_UPDATE
    assert op.classify_pair(a, b) is C.UNRESOLVED  # a different price alone never proves an update


def test_6_exact_reimport_is_true_duplicate():
    assert op.classify_pair(offer(), offer()) is C.TRUE_DUPLICATE


def test_7_missing_variant_never_merges():
    a, b = offer(part_type=None, supplier_sku="A"), offer(part_type=None, supplier_sku="B", name="Other")
    assert op.classify_pair(a, b) is C.UNRESOLVED
    assert op.classify_pair(offer(part_type=None), offer(part_type="oem", supplier_sku="Z")) is C.UNRESOLVED


def test_sku_or_price_alone_is_never_duplicate():
    assert op.classify_pair(offer(supplier_sku="A", part_id="p1"), offer(supplier_sku="B", part_id="p2")) is C.UNRESOLVED
    assert op.classify_pair(offer(part_id="p1"), offer(part_id="p2")) is C.UNRESOLVED  # same sku+price, other part


def test_8_unverified_attribution_not_reassigned():
    with pytest.raises(op.PolicyViolation):
        op.assert_write_allowed("reassign_supplier", C.MISATTRIBUTED, policy=op.load_policy(POLICY), dry_run_done=True, evidence={})
    with pytest.raises(op.PolicyViolation):  # F is never touched
        op.assert_write_allowed("reassign_supplier", C.UNRESOLVED, policy=op.load_policy(POLICY), dry_run_done=True,
                                evidence={"source_proves_supplier": S2})


def test_9_competing_offer_cannot_be_deactivated():
    info = op.load_policy(POLICY)
    for action in ("deactivate", "retire", "hide", "merge", "delete"):
        with pytest.raises(op.PolicyViolation):
            op.assert_write_allowed(action, C.COMPETING_OFFERS, policy=info, dry_run_done=True, has_other_orderable_offer=True)


def test_destructive_needs_dry_run_and_other_orderable_offer():
    info = op.load_policy(POLICY)
    with pytest.raises(op.PolicyViolation):
        op.assert_write_allowed("retire", C.TRUE_DUPLICATE, policy=info, dry_run_done=False, has_other_orderable_offer=True)
    with pytest.raises(op.PolicyViolation):  # would remove the only orderable offer
        op.assert_write_allowed("retire", C.TRUE_DUPLICATE, policy=info, dry_run_done=True, has_other_orderable_offer=False)
    assert op.assert_write_allowed("retire", C.TRUE_DUPLICATE, policy=info, dry_run_done=True, has_other_orderable_offer=True)


def test_14_all_six_classes_reachable():
    got = {
        op.classify_pair(offer(), offer(supplier_id=S2)),
        op.classify_pair(offer(part_type="oem"), offer(part_type="aftermarket")),
        op.classify_pair(offer(price_ils=1), offer(price_ils=2), evidence={"later": "b"}),
        op.classify_pair(offer(), offer()),
        op.classify_pair(offer(supplier_id=S1), offer(supplier_id=S2), evidence={"source_proves_supplier": S2}),
        op.classify_pair(offer(part_type=None), offer(part_type=None, supplier_sku="Q", name="z")),
    }
    assert got == set(C)


def test_gate_or_exit_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(op, "_candidate_paths", lambda: [tmp_path / "missing.md"])
    with pytest.raises(SystemExit) as e:
        op.gate_or_exit("x", dry_run=False)
    assert e.value.code == 3
    assert op.gate_or_exit("x", dry_run=True) is None  # a dry run only reports


# ---------------------------------------------------------------- static guards (legacy routines cannot bypass)
@pytest.mark.parametrize("script", ["maintenance/merge_master_parts.py", "maintenance/bmw_oem_dedup.py",
                                    "maintenance/quarantine_motorstore_offers_129.py", "maintenance/offer_policy_repair.py",
                                    "maintenance/restore_merged_offers_133.py",
                                    "maintenance/remove_placeholder_offers_149.py"])
def test_16_destructive_scripts_call_the_policy_gate(script):
    src = (ROOT / script).read_text()
    assert "offer_policy" in src and ("gate_or_exit" in src or "require_policy" in src)
    assert script in op.POLICY_BOUND_ACTORS


def test_no_other_script_deletes_offers_unbound():
    """Every non-test file that DELETEs from supplier_parts must be a policy-bound actor (or be explicitly reviewed)."""
    reviewed = set(op.POLICY_BOUND_ACTORS)
    offenders = []
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "devtests/", "legacy/", "alembic/")):
            continue
        if re.search(r"DELETE\s+FROM\s+supplier_parts", p.read_text(errors="ignore"), re.I) and rel not in reviewed:
            offenders.append(rel)
    assert offenders == [], f"offer-deleting code not bound to the policy: {offenders}"


# ---------------------------------------------------------------- repair agent through its REAL entry point
def _dsn():
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def test_13_repair_agent_refuses_without_policy_and_opens_no_connection(monkeypatch, tmp_path, capsys):
    import asyncpg
    import maintenance.offer_policy_repair as rep
    monkeypatch.setattr(op, "_candidate_paths", lambda: [tmp_path / "missing.md"])

    async def boom(*a, **k):  # any DB connection would fail the test
        raise AssertionError("repair agent opened a DB connection without the policy")
    monkeypatch.setattr(asyncpg, "connect", boom)
    assert asyncio.run(rep.main(["--apply", "--plan", str(tmp_path / "x.json")])) == 3
    assert "POLICY UNAVAILABLE" in capsys.readouterr().err


@pytest.mark.skipif("sandbox" not in os.getenv("DATABASE_URL", "") and os.getenv("ENVIRONMENT") != "sandbox", reason="sandbox DB only")
def test_11_12_17_repair_agent_applies_logs_stamps_and_rolls_back(monkeypatch, tmp_path, capsys):
    import asyncpg
    import maintenance.offer_policy_repair as rep
    monkeypatch.setenv("OFFER_POLICY_PATH", str(POLICY))
    tag = uuid.uuid4().hex[:8]

    async def run():
        c = await asyncpg.connect(_dsn())
        sup_a, sup_b, part, off = (uuid.uuid4() for _ in range(4))
        brand = await c.fetchrow("SELECT id, name FROM car_brands LIMIT 1")
        try:
            for sid, nm in ((sup_a, f"pol-A-{tag}"), (sup_b, f"pol-B-{tag}")):
                await c.execute("INSERT INTO suppliers (id, name, country, is_active) VALUES ($1,$2,'IL',true)", sid, nm)
            await c.execute("INSERT INTO parts_catalog (id, sku, name, manufacturer, manufacturer_id, is_active) VALUES ($1,$2,'t',$3,$4,true)",
                            part, f"POL-{tag}", brand["name"], brand["id"])
            await c.execute("INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_usd, price_ils, is_available, min_order_qty)"
                            " VALUES ($1,$2,$3,$4,1,10,true,1)", off, sup_a, part, f"S-{tag}")
            plan = [{"row_id": str(off), "class": "E", "action": "reassign_supplier",
                     "evidence": {"source_proves_supplier": str(sup_b)}}]
            pf = tmp_path / "plan.json"
            pf.write_text(json.dumps(plan))
            assert await rep.main(["--apply", "--plan", str(pf)]) == 0
            out = capsys.readouterr().out
            run_id = re.search(r"run_id (\w+)", out).group(1)
            assert str(await c.fetchval("SELECT supplier_id FROM supplier_parts WHERE id=$1", off)) == str(sup_b)
            log = await c.fetchrow("SELECT * FROM offer_policy_audit_log WHERE run_id=$1", run_id)
            info = op.require_policy()
            assert (log["policy_version"], log["policy_sha256"]) == (info.version, info.sha256)
            assert json.loads(log["before"])["supplier_id"] == str(sup_a) and json.loads(log["after"])["supplier_id"] == str(sup_b)
            # re-running the same plan is idempotent: no second audit row
            assert await rep.main(["--apply", "--plan", str(pf)]) == 0
            assert await c.fetchval("SELECT count(*) FROM offer_policy_audit_log WHERE supplier_part_id=$1", off) == 1
            assert await rep.main(["--rollback", run_id]) == 0
            assert str(await c.fetchval("SELECT supplier_id FROM supplier_parts WHERE id=$1", off)) == str(sup_a)
            # a non-permitted action in a plan is rejected and writes nothing
            bad = [{"row_id": str(off), "class": "A", "action": "retire", "has_other_orderable_offer": True}]
            pf.write_text(json.dumps(bad))
            with pytest.raises(op.PolicyViolation):
                await rep.main(["--apply", "--plan", str(pf)])
            assert await c.fetchval("SELECT is_available FROM supplier_parts WHERE id=$1", off) is True
        finally:
            await c.execute("DELETE FROM offer_policy_audit_log WHERE supplier_part_id=$1", off) if await c.fetchval("SELECT to_regclass('offer_policy_audit_log')") else None
            await c.execute("DELETE FROM supplier_parts WHERE id=$1", off)
            await c.execute("DELETE FROM parts_catalog WHERE id=$1", part)
            await c.execute("DELETE FROM suppliers WHERE id = ANY($1::uuid[])", [sup_a, sup_b])
            await c.close()
    asyncio.run(run())


def test_audit_mode_is_read_only_and_reports_policy_stamp(monkeypatch, tmp_path, capsys):
    if "sandbox" not in os.getenv("DATABASE_URL", ""):
        pytest.skip("sandbox DB only")
    import maintenance.offer_policy_repair as rep
    monkeypatch.setenv("OFFER_POLICY_PATH", str(POLICY))
    assert asyncio.run(rep.main(["--out", str(tmp_path)])) == 0
    r = json.loads((tmp_path / "audit_report.json").read_text())
    assert r["policy"]["policy_sha256"] == op.require_policy().sha256 and r["planned_writes"] == 0


def test_no_importer_keys_supplier_parts_on_part_and_supplier_only():
    """ON CONFLICT (part_id, supplier_id) overwrites a different variant of the same supplier (policy class B)."""
    offenders = []
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "devtests/", "legacy/", "alembic/")) or rel == "maintenance/audit_importers.py":
            continue
        if re.search(r"ON\s+CONFLICT\s*\(\s*part_id\s*,\s*supplier_id\s*\)", p.read_text(errors="ignore"), re.I):
            offenders.append(rel)
    assert offenders == [], offenders


# ---------------------------------------------------------------- production-mount readiness + agent hardening
def test_loader_finds_policy_at_planned_container_path(tmp_path, monkeypatch):
    """The planned compose mount is ./docs/policies -> /policies:ro; the loader must probe exactly that path."""
    assert Path("/policies") / op.POLICY_FILENAME in list(op._candidate_paths())


def test_hash_pin_mismatch_fails_closed(monkeypatch):
    monkeypatch.setenv("OFFER_POLICY_EXPECTED_SHA256", "0" * 64)
    with pytest.raises(op.PolicyUnavailable, match="does not match"):
        op.load_policy(POLICY)
    monkeypatch.setenv("OFFER_POLICY_EXPECTED_SHA256", hashlib.sha256(POLICY.read_bytes()).hexdigest().upper())
    assert op.load_policy(POLICY).version == "1.1.0"


def test_repair_agent_refuses_incorrect_hash_without_db(monkeypatch, tmp_path, capsys):
    import asyncpg
    import maintenance.offer_policy_repair as rep
    monkeypatch.setenv("OFFER_POLICY_PATH", str(POLICY))
    monkeypatch.setenv("OFFER_POLICY_EXPECTED_SHA256", "f" * 64)

    async def boom(*a, **k):
        raise AssertionError("DB connection opened despite a policy hash mismatch")
    monkeypatch.setattr(asyncpg, "connect", boom)
    assert asyncio.run(rep.main(["--apply"])) == 3


def test_unresolved_and_competing_records_are_never_written():
    info = op.load_policy(POLICY)
    for action in ("reassign_supplier", "retire", "deactivate", "merge", "hide", "delete"):
        for cls in (C.UNRESOLVED, C.COMPETING_OFFERS, C.DISTINCT_VARIANTS, C.PRICE_UPDATE):
            with pytest.raises(op.PolicyViolation):
                op.assert_write_allowed(action, cls, policy=info, dry_run_done=True, has_other_orderable_offer=True,
                                        evidence={"source_proves_supplier": "x"})
