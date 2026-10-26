"""The catalog population the pricing / offer / AliExpress tests select from — created INSIDE each test's own
rolled-back transaction (FIXES_TRACKER #88).

Root cause it fixes: test_base_price_classification, test_customer_price_boundary, test_offer_classification,
test_aliexpress_discovery and test_aliexpress_price_sync never created their data. They SELECTed "the first active,
offer-less catalog part with an OEM number ORDER BY id" and "an active supplier other than AliExpress with a
country", i.e. they BORROWED real rows — so on the (empty) isolated sandbox every one of them failed with
NoResultFound / empty populations (62 permanent failures), and against a populated database they would UPDATE real
rows (inside a rollback, but holding their locks).

seed_population() inserts exactly that shape — offer-less active parts with OEM numbers of the lengths the services
accept, part types oem / aftermarket, a few categories, priced parts that already carry a customer-visible offer, an
"AliExpress" supplier if missing and active fixture suppliers with a country — inside the SAME session/transaction the test uses, so it is rolled back with the test and
never persists. Part ids start with "000" so they sort FIRST: the existing `ORDER BY id`
selections pick them in an empty database and in a populated one alike. Test bodies and assertions are unchanged.
"""
import random

import sqlalchemy as sa

N_PARTS = 40              # the largest single selection (test_rate_limit_…: 2n+1 = 19) fits twice
N_SUPPLIERS = 4           # test_E_… puts four offers on one part, one supplier each
N_PRICED = 8              # priced parts with a visible offer (two per category)
FIXTURE_SUPPLIER = "AAA Fixture Supplier (tests)"     # sorts before every real supplier name
CATEGORIES = ("filters", "brakes", "engine", "lighting")
MAKERS = ("Toyota", "Kia", "Mazda", "Honda")        # parts_catalog.manufacturer (discovery brands, #96)


def _low_id(i: int, salt: int) -> str:
    # sorts first ("000…") while the first 8 characters still differ per part — some tests derive SKUs from pid[:8]
    return f"000{i:02x}{salt % 0xFFF:03x}-0000-4000-8000-{salt:06x}{i:06x}"


async def seed_population(db) -> dict:
    """Create the population in the test's session. Returns the created ids (for tests that want them)."""
    salt = random.randrange(0x100000, 0xFFFFFF)
    brand = (await db.execute(sa.text(
        "INSERT INTO car_brands (id, name) VALUES (gen_random_uuid(), :n) RETURNING id"),
        {"n": f"zz-fixture-brand-{salt:06x}"})).scalar_one()
    ali = (await db.execute(sa.text("SELECT id::text FROM suppliers WHERE name = 'AliExpress'"))).scalar()
    if ali is None:
        ali = (await db.execute(sa.text(
            "INSERT INTO suppliers (id, name, country, is_active) VALUES (gen_random_uuid(), 'AliExpress', 'CN', TRUE) "
            "RETURNING id::text"))).scalar_one()
    others = []
    for k in range(N_SUPPLIERS):
        others.append((await db.execute(sa.text(
            "INSERT INTO suppliers (id, name, country, is_active) VALUES (gen_random_uuid(), :n, 'IL', TRUE) "
            "RETURNING id::text"), {"n": f"{FIXTURE_SUPPLIER} {k} {salt:06x}"})).scalar_one())
    other = others[0]
    parts = []
    for i in range(N_PARTS):
        pid = _low_id(i, salt)
        oem = f"{salt % 90 + 10}{i:02d}{'A' if i % 2 else 'B'}{salt:06d}"[:11]      # 11 alnum chars, has digits
        await db.execute(sa.text(
            "INSERT INTO parts_catalog (id, sku, name, manufacturer_id, manufacturer, oem_number, part_type, category, "
            "is_active) VALUES (CAST(:i AS uuid), :sku, :n, :m, :mk, :o, :t, :c, TRUE)"),
            {"i": pid, "sku": f"FIX-{salt:06x}-{i}", "n": f"Fixture part {i}", "m": brand, "mk": MAKERS[i % len(MAKERS)],
             "o": oem, "t": "oem" if i % 2 == 0 else "aftermarket", "c": CATEGORIES[i % len(CATEGORIES)]})
        parts.append(pid)
    # the PRICED population (aliexpress_price_sync._select_blind_targets "priced"): base_price > 0 and an existing,
    # customer-visible offer from another active supplier (available, priced, with a supplier_url)
    priced = []
    for j in range(N_PRICED):
        pid = _low_id(0x80 + j, salt)
        await db.execute(sa.text(
            "INSERT INTO parts_catalog (id, sku, name, manufacturer_id, oem_number, part_type, category, is_active, "
            "base_price, min_price_ils) VALUES (CAST(:i AS uuid), :sku, :n, :m, :o, 'oem', :c, TRUE, 145, 100)"),
            {"i": pid, "sku": f"FIXP-{salt:06x}-{j}", "n": f"Fixture priced part {j}", "m": brand,
             "o": f"{salt % 90 + 10}{j:02d}P{salt:06d}"[:11], "c": CATEGORIES[j % len(CATEGORIES)]})
        await db.execute(sa.text(
            "INSERT INTO supplier_parts (id, supplier_id, part_id, supplier_sku, price_ils, price_usd, is_available, "
            "availability, supplier_url) VALUES (gen_random_uuid(), CAST(:s AS uuid), CAST(:p AS uuid), :k, 100, 33, "
            "TRUE, 'in_stock', 'https://fixture.invalid/offer')"),
            {"s": others[j % len(others)], "p": pid, "k": f"FIXP-{salt:06x}-{j}"})
        priced.append(pid)
    return {"parts": parts, "priced": priced, "aliexpress": ali, "other_supplier": other, "other_suppliers": others,
            "brand": str(brand)}
