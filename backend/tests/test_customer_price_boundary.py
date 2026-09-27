"""Customer-price boundary regression suite.

Rule (CLAUDE.md pricing contract): a raw SUPPLIER cost — `supplier_parts.price_ils`, `parts_catalog.min_price_ils`
/`importer_price_ils`, or a live external result's `price`/`total_cost` — is INTERNAL and must never be shown as, or
stand in for, the customer price. Every customer-visible per-part price goes through `_customer_price_fields`
(cost x 1.45 + conditional VAT). Found 2026-09-21 while activating AliExpress:
  * cart stored `unit_price = sp.price_ils` (RAW cost) and displayed it;
  * wishlist displayed `min_price_ils` (cost, later cost x 1.18);
  * `/api/v1/parts/search` returned raw external supplier results (`external_suppliers`);
  * `/api/suppliers/search|search/oem|compare` returned raw supplier results to anonymous callers;
  * cart, wishlist and `GET /api/v1/parts/{id}` returned RAW `parts_images` URLs (supplier CDN hosts, e.g.
    `ae-pic-a1.aliexpress-media.com`) instead of bucket thumbnails.
DB-backed tests use a synthetic AliExpress offer inside a transaction that is ROLLED BACK.
"""
import datetime
import inspect
import re
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import routes.cart as cart
import routes.parts as parts
import routes.suppliers as sup_routes
import services.aliexpress_price_sync as svc
from BACKEND_DATABASE_MODELS import engine
from routes.schemas import CartAddRequest

ROOT = Path(__file__).resolve().parent.parent
RAW_COST = 100.00
POLICY_FOREIGN = round(RAW_COST * 1.45, 2)          # 145.00  (AliExpress / CN: VAT 0%)


# ---------------------------------------------------------------- pure boundary helpers
def test_customer_unit_price_applies_margin_and_conditional_vat():
    assert parts._customer_unit_price(RAW_COST, "AliExpress", "CN") == POLICY_FOREIGN             # foreign: 0% VAT
    assert parts._customer_unit_price(RAW_COST, "AutoParts Pro IL", "IL") == round(145.0 * 1.18, 2)   # local: 18%
    for cost in (RAW_COST, 6.64, 188.68):
        shown = parts._customer_unit_price(cost, "AliExpress", "CN")
        assert shown != cost and shown == round(cost * 1.45, 2)                                     # never the raw cost


@pytest.mark.parametrize("cost", [None, 0, -5])
def test_no_usable_cost_yields_no_customer_price(cost):
    assert parts._customer_unit_price(cost, "AliExpress", "CN") is None


def _raw_external(**over):
    d = {"supplier": "aliexpress", "item_id": "1005001", "title": "Oil Filter 15400-PLM-A01 Honda", "price": 10.06,
         "currency": "USD", "shipping_cost": 0.0, "total_cost": 10.06, "condition": "New", "seller": "SomeSeller",
         "seller_rating": 4.8, "item_url": "https://www.aliexpress.com/item/1005001.html", "image_url": "//x/y.jpg",
         "location": "CN", "estimated_delivery_days": 20, "ships_to_israel": True, "image_urls": ["//x/y.jpg"],
         "tech_specs": {"part_origin": "aftermarket"}, "warranty_text": None, "warranty_months": None}
    d.update(over)
    return d


def test_external_offer_is_sanitized_and_priced_by_policy():
    raw = _raw_external(image_url="https://ae-pic-a1.aliexpress-media.com/kf/abc.jpg")     # real AliExpress CDN host
    safe = parts._sanitize_external_offer(raw, 3.03)
    cost_ils = 10.06 * 3.03
    assert safe["customer_price_ils"] == round(cost_ils * 1.45, 2) != round(cost_ils, 2)
    assert safe["customer_vat_ils"] == 0.0                                    # foreign supplier
    for leaked in ("price", "total_cost", "item_url", "seller", "seller_rating", "currency", "shipping_cost", "item_id",
                   "image_url", "image_urls", "tech_specs"):
        assert leaked not in safe
    assert safe["supplier"] != "aliexpress" and "aliexpress" not in str(safe).lower()
    assert safe["title"] == raw["title"] and safe["condition"] == "New"


@pytest.mark.parametrize("over", [{"currency": "GBP"}, {"currency": ""}, {"price": 0}, {"price": None}])
def test_external_offer_without_trustworthy_price_is_dropped_not_guessed(over):
    assert parts._sanitize_external_offer(_raw_external(**over), 3.03) is None


def test_search_route_serves_external_results_only_through_the_sanitizer():
    src = inspect.getsource(parts.search_parts)
    assert "_sanitize_external_offer(" in src
    assert "external_supplier_results = _json.loads(_cached_ext)" not in src          # the raw pass-through


def test_cart_and_wishlist_never_read_min_price_or_store_raw_cost():
    code = "\n".join(l for l in (ROOT / "routes" / "cart.py").read_text().splitlines() if not l.strip().startswith("#"))
    assert "part.min_price_ils" not in code
    assert code.count("_customer_unit_price(") >= 2 and "_current_part_offer(" in code
    assert "unit_price = float(sp.price_ils" not in code                                # the old raw-cost store


# ---------------------------------------------------------------- raw diagnostic routes are admin-only
@pytest.mark.parametrize("path", ["/api/suppliers/search?query=x", "/api/suppliers/search/oem?oem_number=12345678",
                                  "/api/suppliers/compare?part=x"])
async def test_raw_supplier_routes_reject_anonymous(path):
    app = FastAPI()
    app.include_router(sup_routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(path)
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------- DB-backed (rolled back)
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


async def _part_with_aliexpress_offer(db, min_price=None):
    """A real catalog part with NO other offers + a synthetic AliExpress offer of raw cost 100.00."""
    pid = (await db.execute(sa.text("""SELECT id::text FROM parts_catalog pc WHERE is_active AND oem_number<>''
        AND NOT EXISTS (SELECT 1 FROM supplier_parts sp WHERE sp.part_id = pc.id) ORDER BY id LIMIT 1"""))).scalar_one()
    ali = (await db.execute(sa.text("SELECT id::text FROM suppliers WHERE name='AliExpress'"))).scalar_one()
    await db.execute(sa.text("UPDATE suppliers SET is_active = TRUE WHERE id = CAST(:a AS uuid)"), {"a": ali})
    await svc._write_supplier_part(db, supplier_id=ali, part_id=pid, part_number="TESTOEM" + pid[:6],
                                   price_usd=Decimal("33.00"), price_ils=Decimal(str(RAW_COST)), ils_per_usd_rate=3.03,
                                   item_url="https://www.aliexpress.com/item/1.html", image_urls=[], warranty=(12, "platform_default"))
    await db.execute(sa.text("UPDATE parts_catalog SET min_price_ils = :m WHERE id = CAST(:p AS uuid)"), {"m": min_price, "p": pid})
    return pid


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


@pytest.mark.parametrize("min_price", [None, 0, 118.0, 100.0])       # NULL / 0 / cost x 1.18 (what the agent writes) / raw cost
async def test_cart_wishlist_and_watch_show_policy_price_never_raw_or_min_price(db, monkeypatch, min_price):
    pid = await _part_with_aliexpress_offer(db, min_price)

    # cart WRITE (real add_cart_item): stored value and response are the customer price
    async def _gc(user_id, d): return SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(cart, "_get_or_create_cart", _gc)
    fake = _FakePII()
    resp = await cart.add_cart_item(CartAddRequest(part_id=pid, quantity=1), SimpleNamespace(id=uuid.uuid4()), fake, db)
    assert float(fake.added[0].unit_price) == POLICY_FOREIGN != RAW_COST
    assert resp["items"][0]["price"] == POLICY_FOREIGN

    # cart READ of a LEGACY row that stored the RAW cost
    sp_id = (await db.execute(sa.text("SELECT id FROM supplier_parts WHERE part_id = CAST(:p AS uuid) AND supplier_id = (SELECT id FROM suppliers WHERE name='AliExpress')"), {"p": pid})).scalar_one()
    legacy = SimpleNamespace(id=uuid.uuid4(), part_id=uuid.UUID(pid), supplier_part_id=sp_id, quantity=1, unit_price=RAW_COST)
    assert (await cart._cart_to_response([legacy], db))[0]["price"] == POLICY_FOREIGN

    # wishlist + watch basis
    wl = await cart._wishlist_item_to_response(SimpleNamespace(id=uuid.uuid4(), part_id=uuid.UUID(pid), added_at=datetime.datetime.utcnow()), db)
    assert wl["price"] == POLICY_FOREIGN and wl["price"] not in (RAW_COST, min_price)
    assert (await parts._current_part_price(db, pid))[0] == POLICY_FOREIGN


async def test_sync_min_price_never_reaches_a_customer_surface_and_base_price_is_untouched(db):
    pid = await _part_with_aliexpress_offer(db, 0)
    await svc._update_catalog_min_price(db, part_id=pid, candidate_min_price_ils=RAW_COST)
    row = (await db.execute(sa.text("SELECT min_price_ils, base_price FROM parts_catalog WHERE id = CAST(:p AS uuid)"), {"p": pid})).fetchone()
    assert float(row.min_price_ils) == RAW_COST                       # internal aggregate now holds the raw cost ...
    wl = await cart._wishlist_item_to_response(SimpleNamespace(id=uuid.uuid4(), part_id=uuid.UUID(pid), added_at=datetime.datetime.utcnow()), db)
    assert wl["price"] == POLICY_FOREIGN                              # ... and the customer still sees cost x 1.45


async def test_dearer_aliexpress_offer_does_not_change_the_customer_price(db):
    """Another supplier is cheaper: adding a dearer AliExpress offer must not alter the price."""
    pid = (await db.execute(sa.text("""SELECT pc.id::text FROM parts_catalog pc WHERE pc.is_active AND pc.oem_number<>''
        AND NOT EXISTS (SELECT 1 FROM supplier_parts sp WHERE sp.part_id = pc.id) ORDER BY pc.id LIMIT 1"""))).scalar_one()
    other = (await db.execute(sa.text("SELECT id::text, name, country FROM suppliers WHERE is_active AND name <> 'AliExpress' AND country IS NOT NULL ORDER BY name LIMIT 1"))).fetchone()
    await db.execute(sa.text("""INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_ils, price_usd, is_available, availability)
        VALUES (gen_random_uuid(), CAST(:s AS uuid), CAST(:p AS uuid), :sku, 50, 16.5, TRUE, 'in_stock')"""), {"s": other[0], "p": pid, "sku": "OTHER" + pid[:6]})
    before = (await parts._current_part_price(db, pid))[0]
    ali = (await db.execute(sa.text("SELECT id::text FROM suppliers WHERE name='AliExpress'"))).scalar_one()
    await db.execute(sa.text("UPDATE suppliers SET is_active = TRUE WHERE id = CAST(:a AS uuid)"), {"a": ali})
    await svc._write_supplier_part(db, supplier_id=ali, part_id=pid, part_number="AL" + pid[:8], price_usd=Decimal("40"),
                                   price_ils=Decimal("120"), ils_per_usd_rate=3.03, item_url="u", image_urls=[], warranty=(12, "platform_default"))
    after = (await parts._current_part_price(db, pid))[0]
    assert before == after == parts._customer_unit_price(50.0, other[1], other[2]) and after not in (50.0, 120.0)


# ---------------------------------------------------------------- customer images: bucket thumbnails only
RAW_CDN = "https://ae-pic-a1.aliexpress-media.com/kf/Sabc123.jpg"
THUMB = "https://autosparefinder.co.il/api/v1/thumbnails/thumbs/ab/abc.jpg"


async def _seed_images(db, pid, thumb_status):
    await db.execute(sa.text("""INSERT INTO parts_images (id, part_id, url, is_primary, sort_order, embedding_generated, created_at)
        VALUES (gen_random_uuid(), CAST(:p AS uuid), :u, TRUE, 0, FALSE, NOW())"""), {"p": pid, "u": RAW_CDN})
    if thumb_status:
        await db.execute(sa.text("DELETE FROM part_thumbnails WHERE part_id = CAST(:p AS uuid)"), {"p": pid})
        await db.execute(sa.text("INSERT INTO part_thumbnails (part_id, url, status, updated_at) VALUES (CAST(:p AS uuid), :u, :s, NOW())"),
                         {"p": pid, "u": THUMB, "s": thumb_status})


@pytest.mark.parametrize("thumb_status,expected", [("ok", THUMB), ("rejected_ad", None), (None, None)])
async def test_cart_wishlist_and_part_detail_never_return_raw_supplier_image_urls(db, monkeypatch, thumb_status, expected):
    pid = await _part_with_aliexpress_offer(db)
    await _seed_images(db, pid, thumb_status)

    async def _gc(user_id, d): return SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(cart, "_get_or_create_cart", _gc)
    fake = _FakePII()
    resp = await cart.add_cart_item(CartAddRequest(part_id=pid, quantity=1), SimpleNamespace(id=uuid.uuid4()), fake, db)
    wl = await cart._wishlist_item_to_response(SimpleNamespace(id=uuid.uuid4(), part_id=uuid.UUID(pid), added_at=datetime.datetime.utcnow()), db)
    detail = await parts.get_part(pid, db)

    assert resp["items"][0]["imageUrl"] == expected
    assert wl["imageUrl"] == expected
    assert detail["primary_image"] == expected and detail["images"] == ([expected] if expected else [])
    blob = str([resp, wl, detail["images"], detail["primary_image"]]).lower()
    assert "aliexpress" not in blob and RAW_CDN.lower() not in blob                     # no supplier CDN anywhere


def test_no_customer_route_reads_raw_parts_images_directly():
    code = "\n".join(l for l in (ROOT / "routes" / "cart.py").read_text().splitlines() if not l.strip().startswith("#"))
    assert "PartImage" not in code and "parts_images" not in code
    detail_src = inspect.getsource(parts.get_part)
    assert "PartImage" not in detail_src and "_customer_thumbnail_map(" in detail_src
