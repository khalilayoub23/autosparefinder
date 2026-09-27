"""Offer-classification coexistence rule (owner rule 2026-09-21): OEM + Aftermarket + Equivalent offers may coexist and
are presented by class, supplier and price; an offer of another class is never silently substituted for the default.

Pure tests (no DB) + real-schema tests inside a rolled-back transaction (an existing supplier offer on a real priced
part + an AliExpress offer of a different class)."""
import datetime
import hashlib
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import offer_classification as oc
import routes.cart as cart
import routes.parts as parts
import services.aliexpress_price_sync as svc
from BACKEND_DATABASE_MODELS import engine
from routes.schemas import CartAddRequest


# ---------------------------------------------------------------- pure rule
@pytest.mark.parametrize("raw,norm", [("OEM", "oem"), ("Original", "oem"), ("genuine", "oem"), ("aftermarket", "aftermarket"),
                                       ("OE_Equivalent", "oe_equivalent"), ("oem equivalent", "oe_equivalent"),
                                       (None, None), ("", None), ("unknown", None), ("  ", None)])
def test_normalize(raw, norm):
    assert oc.normalize_type(raw) == norm


@pytest.mark.parametrize("part,offer,compat", [
    ("oem", "oem", True), ("oem", "original", True), ("oem", None, True), (None, "aftermarket", True),   # unclassified = compatible
    ("oem", "aftermarket", False), ("oem", "oe_equivalent", False), ("aftermarket", "oem", False),
    ("aftermarket", "aftermarket", True), ("oe_equivalent", "aftermarket", False),
])
def test_compatibility(part, offer, compat):
    assert oc.is_compatible(part, offer) is compat and oc.class_rank(part, offer) == (0 if compat else 1)


def test_label_prefers_offer_then_inherits_part_class():
    assert oc.offer_label("oem", "aftermarket") == "aftermarket"
    assert oc.offer_label("oem", None) == "oem"                       # legacy offer inherits
    assert oc.offer_label(None, None) is None


@pytest.mark.parametrize("title,expected", [
    ("Oil Filter 15400-PLM-A01 for Honda Civic", "aftermarket"),
    ("BOSCH Oil Filter 0451103316", "oe_equivalent"),
    ("Denso spark plug K20PR-U11", "oe_equivalent"),
    ("OEM Genuine Original Front brake pads 04465-02220", "aftermarket"),        # never 'oem': provenance unverifiable
    ("Update kit rate crate style", "aftermarket"),                              # 'ate' inside words must not match
    ("", "aftermarket"), (None, "aftermarket"),
])
def test_marketplace_listing_is_never_oem(title, expected):
    assert oc.classify_marketplace_listing(title) == expected


@pytest.mark.asyncio
async def test_sql_rank_matches_python_rank():
    """The SQL twin must agree with the Python rule for every combination (real Postgres)."""
    vals = [None, "", "unknown", "oem", "OEM", "original", "genuine", "aftermarket", "oe_equivalent", "OE Equivalent", "oem equivalent", "used"]
    eng = create_async_engine(engine.url, poolclass=NullPool)
    try:
        async with eng.connect() as c:
            for p in vals:
                for o in vals:
                    got = (await c.execute(sa.text(f"SELECT {oc.class_rank_sql('CAST(:o AS text)', 'CAST(:p AS text)')}"), {"o": o, "p": p})).scalar_one()
                    assert got == oc.class_rank(p, o), (p, o)
    finally:
        await eng.dispose()


# ---------------------------------------------------------------- real-schema coexistence (rolled back)
@pytest.fixture
async def db():
    eng = create_async_engine(engine.url, poolclass=NullPool)
    try:
        conn = await eng.connect()
    except Exception as exc:                                                            # pragma: no cover
        await eng.dispose()
        pytest.skip(f"database unreachable: {type(exc).__name__}")
    trans = await conn.begin()
    sess = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield sess
    finally:
        await sess.close(); await trans.rollback(); await conn.close(); await eng.dispose()


class _FakePII:
    def __init__(self): self.added = []
    async def execute(self, *a, **k):
        outer = self
        class R:
            def scalar_one_or_none(s): return None
            def scalars(s): return s
            def all(s): return list(outer.added)
        return R()
    def add(self, o): self.added.append(o)
    async def flush(self): pass


async def _priced_oem_part(db):
    """A synthetic PRICED OEM part with one existing OEM-class offer (cost 100, other supplier) + AliExpress supplier."""
    pid = (await db.execute(sa.text("""SELECT pc.id::text FROM parts_catalog pc WHERE pc.is_active AND pc.oem_number<>'' AND pc.part_type='oem'
        AND NOT EXISTS (SELECT 1 FROM supplier_parts sp WHERE sp.part_id = pc.id) ORDER BY pc.id LIMIT 1"""))).scalar_one()
    await db.execute(sa.text("UPDATE parts_catalog SET base_price = 145, min_price_ils = 100 WHERE id = CAST(:p AS uuid)"), {"p": pid})
    other = (await db.execute(sa.text("SELECT id::text, name, country FROM suppliers WHERE is_active AND name <> 'AliExpress' AND country IS NOT NULL ORDER BY name LIMIT 1"))).fetchone()
    other_sp = str(uuid.uuid4())
    await db.execute(sa.text("""INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_ils, price_usd, is_available, availability, supplier_url, part_type)
        VALUES (CAST(:i AS uuid), CAST(:s AS uuid), CAST(:p AS uuid), :sku, 100, 33, TRUE, 'in_stock', 'https://x/y', NULL)"""),
        {"i": other_sp, "s": other[0], "p": pid, "sku": "OEMOFFER" + pid[:6]})
    ali = (await db.execute(sa.text("SELECT id::text FROM suppliers WHERE name='AliExpress'"))).scalar_one()
    await db.execute(sa.text("UPDATE suppliers SET is_active = TRUE WHERE id = CAST(:a AS uuid)"), {"a": ali})
    return pid, other, other_sp, ali


async def _snapshot(db, pid):
    offers = (await db.execute(sa.text("SELECT id::text, supplier_id::text, price_ils, part_type, is_available FROM supplier_parts WHERE part_id=CAST(:p AS uuid) AND supplier_id <> (SELECT id FROM suppliers WHERE name='AliExpress') ORDER BY id"), {"p": pid})).fetchall()
    cat = (await db.execute(sa.text("SELECT base_price, min_price_ils, importer_price_ils, updated_at FROM parts_catalog WHERE id=CAST(:p AS uuid)"), {"p": pid})).fetchone()
    return (hashlib.sha256(json.dumps([list(map(str, o)) for o in offers]).encode()).hexdigest()[:12], tuple(map(str, cat)))


async def test_aliexpress_offer_coexists_with_oem_offer_and_touches_nothing_else(db):
    pid, other, other_sp, ali = await _priced_oem_part(db)
    before = await _snapshot(db, pid)
    out = await svc._write_supplier_part(db, supplier_id=ali, part_id=pid, part_number="ALI-" + pid[:8], price_usd=Decimal("10"), price_ils=Decimal("30.30"),
                                         ils_per_usd_rate=3.03, item_url="https://www.aliexpress.com/item/1.html", image_urls=[],
                                         warranty=(12, "platform_default"), offer_part_type="aftermarket", touch_catalog=False)
    assert out is not None
    assert await _snapshot(db, pid) == before                                           # other suppliers' rows AND the catalog: untouched
    rows = (await db.execute(sa.text("""SELECT s.name, sp.part_type, sp.price_ils FROM supplier_parts sp JOIN suppliers s ON s.id=sp.supplier_id
        WHERE sp.part_id=CAST(:p AS uuid) ORDER BY sp.price_ils"""), {"p": pid})).fetchall()
    assert [(r[0], r[1]) for r in rows] == [("AliExpress", "aftermarket"), (other[1], None)]     # BOTH offers, own classification each


async def test_no_duplicate_aliexpress_offer_on_rerun(db):
    pid, other, other_sp, ali = await _priced_oem_part(db)
    kw = dict(supplier_id=ali, part_id=pid, part_number="ALI-" + pid[:8], ils_per_usd_rate=3.03, item_url="u", image_urls=[],
              warranty=(12, "platform_default"), offer_part_type="aftermarket", touch_catalog=False)
    await svc._write_supplier_part(db, price_usd=Decimal("10"), price_ils=Decimal("30.30"), **kw)
    await svc._write_supplier_part(db, price_usd=Decimal("9"), price_ils=Decimal("27.27"), **kw)          # price moved
    rows = (await db.execute(sa.text("SELECT price_ils FROM supplier_parts WHERE part_id=CAST(:p AS uuid) AND supplier_id=CAST(:a AS uuid)"), {"p": pid, "a": ali})).fetchall()
    assert len(rows) == 1 and float(rows[0][0]) == 27.27


async def test_defaults_never_substitute_the_cheaper_aftermarket_offer_for_the_oem_one(db, monkeypatch):
    pid, other, other_sp, ali = await _priced_oem_part(db)
    await svc._write_supplier_part(db, supplier_id=ali, part_id=pid, part_number="ALI-" + pid[:8], price_usd=Decimal("10"), price_ils=Decimal("30.30"),
                                   ils_per_usd_rate=3.03, item_url="u", image_urls=[], warranty=(12, "platform_default"),
                                   offer_part_type="aftermarket", touch_catalog=False)
    oem_price = parts._customer_unit_price(100.0, other[1], other[2])
    ali_price = parts._customer_unit_price(30.30, "AliExpress", "CN")
    assert ali_price < oem_price                                                          # AliExpress IS cheaper

    async def _gc(user_id, d): return SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(cart, "_get_or_create_cart", _gc)
    # default cart line: the class-compatible OEM offer, priced by policy, labeled oem
    fake = _FakePII()
    resp = await cart.add_cart_item(CartAddRequest(part_id=pid, quantity=1), SimpleNamespace(id=uuid.uuid4()), fake, db)
    assert str(fake.added[0].supplier_part_id) == other_sp
    assert resp["items"][0]["price"] == oem_price and resp["items"][0]["offerPartType"] == "oem"
    # explicit choice: the customer picks the AliExpress aftermarket offer -> policy price, labeled aftermarket
    ali_sp = (await db.execute(sa.text("SELECT id::text FROM supplier_parts WHERE part_id=CAST(:p AS uuid) AND supplier_id=CAST(:a AS uuid)"), {"p": pid, "a": ali})).scalar_one()
    fake2 = _FakePII()
    resp2 = await cart.add_cart_item(CartAddRequest(part_id=pid, quantity=1, supplier_part_id=ali_sp), SimpleNamespace(id=uuid.uuid4()), fake2, db)
    assert str(fake2.added[0].supplier_part_id) == ali_sp
    assert resp2["items"][0]["price"] == ali_price != 30.30 and resp2["items"][0]["offerPartType"] == "aftermarket"
    # watch/wishlist price basis: the OEM offer, not the cheaper aftermarket one
    assert (await parts._current_part_price(db, pid))[0] == oem_price
    wl = await cart._wishlist_item_to_response(SimpleNamespace(id=uuid.uuid4(), part_id=uuid.UUID(pid), added_at=datetime.datetime.utcnow()), db)
    assert wl["price"] == oem_price


async def test_only_aftermarket_offer_exists_is_the_default_and_is_labeled(db, monkeypatch):
    pid = (await db.execute(sa.text("""SELECT pc.id::text FROM parts_catalog pc WHERE pc.is_active AND pc.oem_number<>'' AND pc.part_type='oem'
        AND NOT EXISTS (SELECT 1 FROM supplier_parts sp WHERE sp.part_id = pc.id) ORDER BY pc.id LIMIT 1"""))).scalar_one()
    ali = (await db.execute(sa.text("SELECT id::text FROM suppliers WHERE name='AliExpress'"))).scalar_one()
    await db.execute(sa.text("UPDATE suppliers SET is_active = TRUE WHERE id = CAST(:a AS uuid)"), {"a": ali})
    await svc._write_supplier_part(db, supplier_id=ali, part_id=pid, part_number="ALI-" + pid[:8], price_usd=Decimal("10"), price_ils=Decimal("30.30"),
                                   ils_per_usd_rate=3.03, item_url="u", image_urls=[], warranty=(12, "platform_default"), offer_part_type="aftermarket")
    async def _gc(user_id, d): return SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(cart, "_get_or_create_cart", _gc)
    resp = await cart.add_cart_item(CartAddRequest(part_id=pid, quantity=1), SimpleNamespace(id=uuid.uuid4()), _FakePII(), db)
    assert resp["items"][0]["price"] == parts._customer_unit_price(30.30, "AliExpress", "CN") and resp["items"][0]["offerPartType"] == "aftermarket"


async def test_images_only_added_when_part_has_none_and_never_primary_over_existing(db):
    pid, other, other_sp, ali = await _priced_oem_part(db)
    await db.execute(sa.text("""INSERT INTO parts_images (id, part_id, url, is_primary, sort_order, embedding_generated, created_at)
        VALUES (gen_random_uuid(), CAST(:p AS uuid), 'https://existing/img.jpg', TRUE, 0, FALSE, NOW())"""), {"p": pid})
    out = await svc._write_supplier_part(db, supplier_id=ali, part_id=pid, part_number="ALI-" + pid[:8], price_usd=Decimal("10"), price_ils=Decimal("30.30"),
                                         ils_per_usd_rate=3.03, item_url="u", image_urls=["https://ae/x.jpg"], warranty=(12, "platform_default"),
                                         offer_part_type="aftermarket", touch_catalog=False)
    assert out["images"] == 0
    n = (await db.execute(sa.text("SELECT count(*), count(*) FILTER (WHERE is_primary) FROM parts_images WHERE part_id=CAST(:p AS uuid)"), {"p": pid})).fetchone()
    assert tuple(n) == (1, 1)


# ---------------------------------------------------------------- dual-population eligibility
async def test_select_targets_dual_population_rules(db, monkeypatch):
    monkeypatch.setenv("ALIEXPRESS_PRICED_SHARE", "0.5")
    monkeypatch.setenv("ALIEXPRESS_PRICED_CATEGORIES", "")            # no category filter: test the structural rules
    monkeypatch.setattr(svc, "_ALIEXPRESS_SYNC_LAST_ID", svc._LAST_ID_ZERO)
    monkeypatch.setattr(svc, "_ALIEXPRESS_SYNC_LAST_ID_PRICED", svc._LAST_ID_ZERO)
    ali = (await db.execute(sa.text("SELECT id::text FROM suppliers WHERE name='AliExpress'"))).scalar_one()
    targets = await svc._select_blind_targets(db, ali, 6)
    pops = {t.population for t in targets}
    assert pops == {"unpriced", "priced"} and sum(t.population == "priced" for t in targets) == 3
    for t in targets:
        r = (await db.execute(sa.text("""SELECT pc.base_price,
              EXISTS (SELECT 1 FROM supplier_parts sp JOIN suppliers s ON s.id=sp.supplier_id WHERE sp.part_id=pc.id AND s.is_active AND s.name<>'AliExpress' AND sp.is_available AND sp.price_ils>0) other_offer
              FROM parts_catalog pc WHERE pc.id = :i"""), {"i": t.id})).fetchone()
        if t.population == "unpriced":
            assert not r.base_price
        else:
            assert r.base_price and r.base_price > 0 and r.other_offer           # priced AND already has another supplier's offer
    # a priced part with a FRESH AliExpress check is not re-selected
    priced = [t for t in targets if t.population == "priced"][0]
    await db.execute(sa.text("""INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_ils, price_usd, is_available, availability, last_checked_at)
        VALUES (gen_random_uuid(), CAST(:a AS uuid), :p, 'FRESH-TEST', 10, 3, TRUE, 'in_stock', NOW())"""), {"a": ali, "p": priced.id})
    monkeypatch.setattr(svc, "_ALIEXPRESS_SYNC_LAST_ID", svc._LAST_ID_ZERO)
    monkeypatch.setattr(svc, "_ALIEXPRESS_SYNC_LAST_ID_PRICED", svc._LAST_ID_ZERO)
    again = await svc._select_blind_targets(db, ali, 6)
    assert priced.id not in {t.id for t in again if t.population == "priced"}


async def test_share_zero_is_unpriced_only_and_category_filter_applies(db, monkeypatch):
    monkeypatch.setenv("ALIEXPRESS_PRICED_SHARE", "0")
    monkeypatch.setattr(svc, "_ALIEXPRESS_SYNC_LAST_ID", svc._LAST_ID_ZERO)
    ali = (await db.execute(sa.text("SELECT id::text FROM suppliers WHERE name='AliExpress'"))).scalar_one()
    assert {t.population for t in await svc._select_blind_targets(db, ali, 4)} == {"unpriced"}
    monkeypatch.setenv("ALIEXPRESS_PRICED_SHARE", "1")
    monkeypatch.setenv("ALIEXPRESS_PRICED_CATEGORIES", "filters")
    monkeypatch.setattr(svc, "_ALIEXPRESS_SYNC_LAST_ID_PRICED", svc._LAST_ID_ZERO)
    got = await svc._select_blind_targets(db, ali, 3)
    assert got and all(t.population == "priced" for t in got)
    cats = {(await db.execute(sa.text("SELECT category FROM parts_catalog WHERE id=:i"), {"i": t.id})).scalar_one() for t in got}
    assert cats == {"filters"}


async def test_priced_population_requires_a_customer_visible_existing_offer(db, monkeypatch):
    """Found by the live production sample: existing offers WITHOUT supplier_url (IL importer price lists) are not listed
    by the customer search, so a URL-bearing AliExpress offer would become the ONLY listed offer and hide the OEM price.
    Such parts must not be eligible for the priced population; parts with a visible existing offer must be."""
    monkeypatch.setenv("ALIEXPRESS_PRICED_SHARE", "1")
    monkeypatch.setenv("ALIEXPRESS_PRICED_CATEGORIES", "")
    ali = (await db.execute(sa.text("SELECT id::text FROM suppliers WHERE name='AliExpress'"))).scalar_one()
    other = (await db.execute(sa.text("SELECT id::text FROM suppliers WHERE is_active AND name NOT IN ('AliExpress','Official Manufacturer Sites','Sandbox Supplier QA') AND country IS NOT NULL ORDER BY name LIMIT 1"))).scalar_one()
    rows = (await db.execute(sa.text("""SELECT pc.id::text FROM parts_catalog pc WHERE pc.is_active AND pc.oem_number<>'' AND NOT EXISTS
        (SELECT 1 FROM supplier_parts sp WHERE sp.part_id = pc.id) ORDER BY pc.id LIMIT 2"""))).fetchall()
    no_url, with_url = rows[0][0], rows[1][0]
    for pid, url, sku in ((no_url, None, "NOURL"), (with_url, "https://x/y", "WITHURL")):
        await db.execute(sa.text("UPDATE parts_catalog SET base_price = 145 WHERE id = CAST(:p AS uuid)"), {"p": pid})
        await db.execute(sa.text("""INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_ils, price_usd, is_available, availability, supplier_url)
            VALUES (gen_random_uuid(), CAST(:s AS uuid), CAST(:p AS uuid), :sku, 100, 33, TRUE, 'in_stock', :u)"""), {"s": other, "p": pid, "sku": sku + pid[:6], "u": url})
    prev = lambda pid: str(uuid.UUID(int=uuid.UUID(pid).int - 1))
    monkeypatch.setattr(svc, "_ALIEXPRESS_SYNC_LAST_ID_PRICED", prev(with_url))
    got = [str(t.id) for t in await svc._select_blind_targets(db, ali, 1) if t.population == "priced"]
    assert got == [with_url]                                    # visible existing offer: eligible (next id after the cursor)
    monkeypatch.setattr(svc, "_ALIEXPRESS_SYNC_LAST_ID_PRICED", prev(no_url))
    got = [str(t.id) for t in await svc._select_blind_targets(db, ali, 1) if t.population == "priced"]
    assert got != [no_url]                                      # URL-less existing offer: NOT eligible
