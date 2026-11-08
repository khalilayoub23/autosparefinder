"""
Script: tests/test_supplier_country_128.py
Purpose: #128 — a supplier whose country is missing or a placeholder ('Unknown', 'Global') has its VAT decided WITHOUT
         country evidence (price_policy.vat_rate: a name-only supplier is foreign, 0% VAT). The rule itself is a pending
         owner decision; what must hold regardless is that this is never SILENT. country_problem() / country_problem_sql()
         are the one test for "not evidence"; db_update_agent.validate_supplier_countries (read-only) reports every active
         supplier with priced offers that fails it, together with the VAT it is being billed at.
Process: pure checks + isolated DB only (tests/isolated_db_guard.py); DB tests remove exactly the rows they create.
Data Imported/Modified: test suppliers/parts/offers in the disposable catalog DB (removed afterwards).
Last Updated: 2026-11-03
"""
from __future__ import annotations

import os
import uuid

import pytest

import isolated_db_guard as guard
import price_policy as pp

_REFUSAL = guard.url_refusal(os.getenv("DATABASE_URL"))
db_only = pytest.mark.skipif(bool(_REFUSAL), reason=f"isolated DB required: {_REFUSAL}")

COUNTRY_CASES = [
    # (country, expected problem)
    ("IL", None), ("il", None), (" IL ", None), ("Israel", None), ("ישראל", None),          # Israeli
    ("IE", None), ("US", None), ("UK", None), ("GB", None), ("CN", None), ("JP", None), ("EU", None),   # foreign, explicit
    (None, "missing"), ("", "missing"), ("   ", "missing"),                                  # missing
    ("Unknown", "invalid"), ("unknown", "invalid"), ("Global", "invalid"), ("N/A", "invalid"),
    ("NA", "invalid"), ("-", "invalid"), ("?", "invalid"), ("none", "invalid"), ("Ireland", "invalid"),
    ("USA", "invalid"), ("123", "invalid"), ("I", "invalid"),                                # invalid
]


@pytest.mark.parametrize("country,problem", COUNTRY_CASES)
def test_country_problem_matrix(country, problem):
    assert pp.country_problem(country) == problem


def test_vat_rule_itself_is_unchanged_by_this_patch_decision_pending():
    """The business decision (FIXES_TRACKER #128 A or B) is NOT made here. Current behaviour, pinned:"""
    assert pp.vat_rate("Motorstore IL", "Unknown") == 0.0       # placeholder country -> foreign
    assert pp.vat_rate("RockAuto", None) == 0.0                 # name only, not a known local name -> foreign
    assert pp.vat_rate("AutoParts Pro IL", None) == pp.VAT_RATE # a known local NAME decides when the country is empty
    assert pp.vat_rate(None, None) == pp.VAT_RATE               # no name and no country = unknown supplier -> local
    assert pp.vat_rate("Kia Israel", "IL") == pp.VAT_RATE and pp.vat_rate("Car-Parts.ie", "IE") == 0.0


def test_every_unusable_country_is_flaggable_not_silent():
    """Any supplier that has a name but no usable country must fail country_problem (so the monitor sees it)."""
    for country in (None, "", "Unknown", "Global", "N/A", "Ireland"):
        assert pp.country_problem(country) is not None


@pytest.fixture(autouse=True)
async def _remove_test_rows():
    yield
    if _REFUSAL:
        return
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import async_session_factory, engine
    async with async_session_factory() as db:
        assert await guard.live_refusal(db) is None
        for stmt in ("CREATE TEMP TABLE _tp ON COMMIT DROP AS SELECT id FROM parts_catalog WHERE sku LIKE 'M128%'",
                     "DELETE FROM supplier_parts WHERE part_id IN (SELECT id FROM _tp)",
                     "DELETE FROM parts_catalog WHERE id IN (SELECT id FROM _tp)",
                     "DELETE FROM suppliers WHERE name ~ '^M128 ' AND id NOT IN (SELECT supplier_id FROM supplier_parts)"):
            await db.execute(text(stmt))
        await db.commit()
    await engine.dispose()


@pytest.fixture
async def db():
    from BACKEND_DATABASE_MODELS import async_session_factory, engine
    async with async_session_factory() as session:
        assert await guard.live_refusal(session) is None
        yield session
    await engine.dispose()


@db_only
@pytest.mark.parametrize("country,problem", COUNTRY_CASES)
async def test_sql_twin_agrees_with_python(db, country, problem):
    from sqlalchemy import text
    got = (await db.execute(text("SELECT " + pp.country_problem_sql("s") + " FROM (SELECT CAST(:c AS text) AS country) s"),
                            {"c": country})).scalar_one()
    assert got is (problem is not None)


async def _mk(db, bid, name, country, priced=1, active=True, available=True):
    from sqlalchemy import text
    sid = str(uuid.uuid4())
    await db.execute(text("INSERT INTO suppliers (id, name, country, is_active) VALUES (CAST(:i AS uuid), :n, :c, :a)"),
                     {"i": sid, "n": f"M128 {name} {sid[:6]}", "c": country, "a": active})
    # one PART per offer: a supplier holds ONE untyped offer per part (uq_sp_part_supplier_untyped, and the old
    # uq_supplier_parts_part_supplier before it), so N offers of one supplier need N parts
    for n in range(max(priced, 1)):
        pid = str(uuid.uuid4())
        await db.execute(text("INSERT INTO parts_catalog (id, sku, name, manufacturer_id, part_type, is_active, created_at, updated_at) "
                              "VALUES (CAST(:i AS uuid), :k, 'm128', CAST(:m AS uuid), 'aftermarket', TRUE, NOW(), NOW())"),
                         {"i": pid, "k": "M128" + pid[:10], "m": bid})
        if n < priced:
            await db.execute(text("INSERT INTO supplier_parts (id, part_id, supplier_id, supplier_sku, price_ils, price_usd, is_available, "
                                  "updated_at) VALUES (gen_random_uuid(), CAST(:p AS uuid), CAST(:s AS uuid), :k, 50, 0, :a, NOW())"),
                             {"p": pid, "s": sid, "k": f"k{n}-{sid[:6]}", "a": available})
    return sid, f"M128 {name} {sid[:6]}"


@db_only
async def test_monitor_flags_exactly_the_unclassifiable_suppliers_and_never_writes(db, monkeypatch):
    import db_update_agent as D
    import BACKEND_API_ROUTES as R
    from sqlalchemy import text
    bid = (await db.execute(text("SELECT id::text FROM car_brands LIMIT 1"))).scalar()
    if not bid:
        pytest.skip("sandbox has no car_brands row")
    _, n_unknown = await _mk(db, bid, "Unknown-country", "Unknown", priced=3)
    _, n_null = await _mk(db, bid, "Null-country", None, priced=2)
    _, n_ok_il = await _mk(db, bid, "Israeli", "IL")
    _, n_ok_ie = await _mk(db, bid, "Irish", "IE")
    _, n_inactive = await _mk(db, bid, "Inactive-unknown", "Unknown", active=False)
    _, n_nooffers = await _mk(db, bid, "Unknown-no-offers", "Unknown", priced=0)
    _, n_unavailable = await _mk(db, bid, "Unknown-unavailable", "Unknown", available=False)
    await db.commit()
    before = (await db.execute(text("SELECT name, country FROM suppliers WHERE name ~ '^M128 ' ORDER BY name"))).all()
    sent = []

    async def fake_notify(category, title, body="", **kw):
        sent.append((category, title, body, kw))
        return {"ok": True}

    monkeypatch.setattr(R, "notify_owner", fake_notify)
    out = await D.validate_supplier_countries(db)
    names = {x["supplier"]: x for x in out["suppliers"]}
    assert out["status"] == "ok" and out["updated"] == 0
    assert n_unknown in names and n_null in names
    assert names[n_unknown]["problem"] == "invalid" and names[n_unknown]["priced_offers"] == 3 and names[n_unknown]["vat_now"] == 0.0
    assert names[n_null]["problem"] == "missing" and names[n_null]["priced_offers"] == 2
    for ok in (n_ok_il, n_ok_ie, n_inactive, n_nooffers, n_unavailable):
        assert ok not in names, f"{ok} must not be flagged"
    assert len(sent) == 1 and sent[0][0] == "supplier" and sent[0][3]["alert_key"] == "supplier_country_problem"
    assert n_unknown in sent[0][2] and "billed VAT 0%" in sent[0][2]
    after = (await db.execute(text("SELECT name, country FROM suppliers WHERE name ~ '^M128 ' ORDER BY name"))).all()
    assert after == before, "the monitor must never change a supplier"


@db_only
async def test_a_corrected_country_stops_being_flagged_and_changes_the_billed_vat(db, monkeypatch):
    import db_update_agent as D
    import BACKEND_API_ROUTES as R
    from sqlalchemy import text

    async def fake_notify(*a, **k):
        return {"ok": True}

    monkeypatch.setattr(R, "notify_owner", fake_notify)
    bid = (await db.execute(text("SELECT id::text FROM car_brands LIMIT 1"))).scalar()
    sid, name = await _mk(db, bid, "Fixme", "Unknown", priced=1)
    await db.commit()
    first = {x["supplier"]: x for x in (await D.validate_supplier_countries(db))["suppliers"]}
    assert name in first and first[name]["vat_now"] == 0.0
    await db.execute(text("UPDATE suppliers SET country = 'IL' WHERE id = CAST(:i AS uuid)"), {"i": sid})
    await db.commit()
    second = {x["supplier"]: x for x in (await D.validate_supplier_countries(db))["suppliers"]}
    assert name not in second
    assert pp.vat_rate(name, "IL") == pp.VAT_RATE                 # the repaired record is billed 18%
    await db.execute(text("UPDATE suppliers SET country = 'US' WHERE id = CAST(:i AS uuid)"), {"i": sid})
    await db.commit()
    assert name not in {x["supplier"] for x in (await D.validate_supplier_countries(db))["suppliers"]}
    assert pp.vat_rate(name, "US") == 0.0                          # a foreign repair stays 0%


def test_monitor_is_registered_in_the_task_registry_and_the_nightly_list():
    import inspect
    import db_update_agent as D
    assert D.TASK_REGISTRY["validate_supplier_countries"] is D.validate_supplier_countries
    assert '"validate_supplier_countries",' in inspect.getsource(D.run_all_tasks)
    assert "validate_supplier_countries" not in D.OOM_BLOCKED_TASKS


# ── the two production suppliers named by the owner's decision (2026-11-03) ──────────────────────────────────────────────
def test_named_suppliers_vat_before_and_after_the_country_correction():
    """Motorstore IL ('Unknown' -> 'IL') and RockAuto (NULL -> 'US'): classification drives the VAT rate."""
    assert pp.vat_rate("Motorstore IL", "Unknown") == 0.0 and pp.vat_rate("Motorstore IL", "IL") == pp.VAT_RATE
    assert pp.vat_rate("RockAuto", None) == 0.0 and pp.vat_rate("RockAuto", "US") == 0.0          # behaviour-neutral
    assert pp.country_problem("IL") is None and pp.country_problem("US") is None
    assert pp.country_problem("Unknown") == "invalid" and pp.country_problem(None) == "missing"
    # customer unit price for a 100-shekel offer cost
    assert pp.unit_price(100, "Motorstore IL", "Unknown").total == 145.0      # today (0% VAT)
    assert pp.unit_price(100, "Motorstore IL", "IL").total == 171.1           # after the country correction
    assert pp.unit_price(100, "RockAuto", None).total == pp.unit_price(100, "RockAuto", "US").total == 145.0


def test_motorstore_price_basis_coupling_is_documented_by_numbers():
    """Motorstore's displayed price is the FINAL consumer price (checkout adds no VAT; browser evidence 2026-11-03). Treated as an ex-VAT
    cost under an Israeli classification it is charged 18% twice; the consistent treatment is cost = price / 1.18 at ingest (the
    importer consumer-price formula). Country classification and the VAT/price basis are separate concerns: this pins both."""
    shown = 100.0
    as_exvat_cost_after_IL = pp.unit_price(shown, "Motorstore IL", "IL").total               # 171.10: VAT counted twice
    as_consumer_price = pp.unit_price(round(shown / (1 + pp.VAT_RATE), 2), "Motorstore IL", "IL").total   # cost = price/1.18
    assert as_exvat_cost_after_IL == 171.1
    assert abs(as_consumer_price - 145.0) <= 0.01                                             # 145.01: the cost is rounded to the agora
    assert abs(as_consumer_price - pp.unit_price(shown, "Motorstore IL", "Unknown").total) <= 0.01   # today's 0% equals the right total (+-1 agora)


@db_only
async def test_correcting_a_country_changes_nothing_but_the_country(db, monkeypatch):
    """The two prepared UPDATEs (RockAuto NULL->'US', Motorstore 'Unknown'->'IL') on look-alike rows: identity, offers, prices and
    stock stay byte-identical; only suppliers.country changes."""
    from sqlalchemy import text
    bid = (await db.execute(text("SELECT id::text FROM car_brands LIMIT 1"))).scalar()
    if not bid:
        pytest.skip("sandbox has no car_brands row")
    ms, ms_name = await _mk(db, bid, "Motorstore-like", "Unknown", priced=3)
    ra, ra_name = await _mk(db, bid, "RockAuto-like", None, priced=2)
    await db.commit()
    snap = lambda: db.execute(text(
        "SELECT s.id::text, s.name, s.website, s.is_active, s.priority, "
        "(SELECT md5(string_agg(sp.id::text || coalesce(sp.price_ils::text,'') || coalesce(sp.price_usd::text,'') || sp.is_available::text || "
        "coalesce(sp.availability,'') || coalesce(sp.supplier_url,''), ',' ORDER BY sp.id)) FROM supplier_parts sp WHERE sp.supplier_id = s.id) AS offers_md5 "
        "FROM suppliers s WHERE s.id IN (CAST(:a AS uuid), CAST(:b AS uuid)) ORDER BY s.id"), {"a": ms, "b": ra})
    before = (await snap()).all()
    r1 = await db.execute(text("UPDATE suppliers SET country = 'IL' WHERE id = CAST(:i AS uuid) AND country = 'Unknown'"), {"i": ms})
    r2 = await db.execute(text("UPDATE suppliers SET country = 'US' WHERE id = CAST(:i AS uuid) AND country IS NULL"), {"i": ra})
    await db.commit()
    assert r1.rowcount == 1 and r2.rowcount == 1
    assert (await snap()).all() == before, "something other than the country changed"
    assert pp.vat_rate(ms_name, "IL") == pp.VAT_RATE and pp.vat_rate(ra_name, "US") == 0.0
    # rollback of both
    await db.execute(text("UPDATE suppliers SET country = 'Unknown' WHERE id = CAST(:i AS uuid)"), {"i": ms})
    await db.execute(text("UPDATE suppliers SET country = NULL WHERE id = CAST(:i AS uuid)"), {"i": ra})
    await db.commit()
    got = (await db.execute(text("SELECT country FROM suppliers WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid)) ORDER BY id"), {"a": ms, "b": ra})).all()
    assert sorted(map(lambda x: x[0] or "", got)) == ["", "Unknown"]
