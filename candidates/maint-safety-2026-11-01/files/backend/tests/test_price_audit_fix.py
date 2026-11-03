"""maintenance/price_audit_fix.py (rewritten 2026-11-01) and its two superseded siblings, against the price policy.

The old script wrote `importer*1.18*1.45` (gross) and `max*1.45` over every part; these tests pin the new contract:
read-only dry run by default, `--apply` only through the guarded owner tasks, offer-owned values never replaced by an
estimate, Israeli / foreign VAT irrelevant to the NET base_price, idempotent, restartable after an interruption."""
import os

import pytest
import sqlalchemy as sa

import db_update_agent as dua
import maintenance_guard as mg
import price_policy as pp
from base_price_inventory import scan_file
from test_maintenance_guard import (Crash, bases, env, offer, offer_parts, part, quiet_catalog, setting)  # noqa: F401
from test_maintenance_policy_alignment import base_of, db, fit_rows  # noqa: F401

import price_audit_fix as paf

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def gate_open(monkeypatch):
    monkeypatch.setattr(dua, "OOM_BLOCKED_TASKS", frozenset())      # as if A3 had released the owner tasks


async def mixed_catalog(db):
    offer_il = await part(db, base=999.0)                           # eligible visible offer, Israeli supplier
    await offer(db, offer_il, ils=100, country="IL")
    offer_foreign = await part(db, base=1.0)                        # eligible visible offer, foreign supplier (VAT 0)
    await offer(db, offer_foreign, ils=80, country="IE")
    est = await part(db, base=5.0, importer=100)                    # no eligible offer -> catalog estimate
    est_max = await part(db, base=None, mx=118)                     # estimate from the consumer reference max/1.18
    owned_with_estimate = await part(db, base=1.0, importer=10)     # has a visible offer AND an importer cost
    await offer(db, owned_with_estimate, ils=200)
    other = await part(db, "oem", base=7.0)                         # only an offer of another class
    await offer(db, other, ils=50, klass="aftermarket")
    return dict(offer_il=offer_il, offer_foreign=offer_foreign, est=est, est_max=est_max,
                owned=owned_with_estimate, other=other)


EXPECT = dict(offer_il=145.0, offer_foreign=116.0, est=145.0, est_max=145.0, owned=290.0, other=7.0)


async def test_dry_run_reports_and_writes_nothing(db):
    ids = await mixed_catalog(db)
    before = {k: await base_of(db, v) for k, v in ids.items()}
    out = await paf.dry_run(db, sample=50)
    assert out["would_change"] == {"offer": 3, "estimate": 2}                  # fix_base_prices x3, normalize x2
    assert out["left_alone"] == {"other_class_only": 1}
    assert {k: await base_of(db, v) for k, v in ids.items()} == before        # NOTHING written


async def test_apply_runs_the_owner_tasks_and_an_offer_owned_value_is_not_replaced_by_the_estimate(db):
    ids = await mixed_catalog(db)
    res = await paf.apply_tasks(db)
    assert res["fix_base_prices"]["status"] == "ok" and res["normalize_base_price"]["status"] == "ok"
    assert {k: await base_of(db, v) for k, v in ids.items()} == EXPECT
    assert await base_of(db, ids["owned"]) == pp.base_price_for_cost(200) != pp.base_price_for_cost(10)   # offer, not importer
    # Israeli vs foreign supplier: the NET is the same function of the cost (VAT is a customer-price concern)
    assert pp.unit_price(100, supplier_country="IL").net == pp.unit_price(100, supplier_country="IE").net


async def test_apply_is_idempotent_and_a_correct_value_is_a_no_op(db):
    await mixed_catalog(db)
    await paf.apply_tasks(db)
    again = await paf.apply_tasks(db)
    assert again["fix_base_prices"]["updated"] == 0 and again["normalize_base_price"]["updated"] == 0
    assert again["fix_base_prices"]["unchanged"] == 3 and again["normalize_base_price"]["unchanged"] == 2


async def test_apply_after_an_interruption_converges_without_double_writes(db, monkeypatch):
    monkeypatch.setenv("BASE_PRICE_TASK_BATCH", "2")
    ids = await offer_parts(db, 4)
    real = dua._save_task_cursor
    state = {"crash": True}

    async def crashing(d, task, value):
        if state["crash"]:
            state["crash"] = False
            raise Crash()
        return await real(d, task, value)
    monkeypatch.setattr(dua, "_save_task_cursor", crashing)
    with pytest.raises(Crash):
        await paf.apply_tasks(db)
    assert await bases(db, ids) == [145.0, 145.0, 1.0, 1.0]
    res = await paf.apply_tasks(db)
    assert await bases(db, ids) == [145.0] * 4 and res["fix_base_prices"]["updated"] == 2


async def test_apply_while_the_tasks_are_blocked_writes_nothing(db, monkeypatch):
    monkeypatch.setattr(dua, "OOM_BLOCKED_TASKS", frozenset(mg.GUARDED_TASKS))   # the real state today
    ids = await mixed_catalog(db)
    before = {k: await base_of(db, v) for k, v in ids.items()}
    res = await paf.apply_tasks(db)
    assert all(r["status"] == "skipped" for r in res.values())
    assert {k: await base_of(db, v) for k, v in ids.items()} == before


async def test_apply_honours_the_kill_switch(db):
    ids = await mixed_catalog(db)
    before = {k: await base_of(db, v) for k, v in ids.items()}
    await mg.trip(db, "price", "test")
    res = await paf.apply_tasks(db)
    assert all(r["status"] == "stopped" for r in res.values())
    assert {k: await base_of(db, v) for k, v in ids.items()} == before


def test_the_three_scripts_contain_no_price_formula_and_the_margin_script_delegates():
    for f in ("maintenance/price_audit_fix.py", "maintenance/margin_fix_batched.py", "maintenance/vat_fix_batched.py"):
        res = scan_file(os.path.join(BACKEND, f))
        assert res["formulas"] == [] and res["writes"] == [], (f, res)       # none writes base_price any more
    assert "price_audit_fix" in open(os.path.join(BACKEND, "maintenance/margin_fix_batched.py")).read()
    assert "sys.exit(" not in open(os.path.join(BACKEND, "maintenance/price_audit_fix.py")).read().split("def ")[0]
