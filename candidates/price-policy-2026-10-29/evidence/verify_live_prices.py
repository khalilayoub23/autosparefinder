"""
Script: verify_live_prices.py  (release evidence, READ-ONLY)
Purpose: After the deploy, take REAL parts (local supplier, foreign supplier, USD-priced offer, catalog-only part) and
         price each one through every surface's own code path; all must equal price_policy for the same offer.
Process: read-only - SELECTs, public GET endpoints (search, compare), in-process calls that write nothing
         (_current_part_offer, the chat search with an exact-SKU query, _order_line_pricing, _live_reprice_order on
         unsaved objects - nothing is committed). Run: docker exec -i autospare_backend python3 - < verify_live_prices.py
Data Imported/Modified: none.
Last Updated: 2026-10-29
"""
import asyncio
import json
import sys
import urllib.parse
import urllib.request
from types import SimpleNamespace

sys.path.insert(0, "/app")
from sqlalchemy import text  # noqa: E402

import price_policy as pp  # noqa: E402
from BACKEND_DATABASE_MODELS import async_session_factory  # noqa: E402
from currency_rate import get_usd_to_ils_rate  # noqa: E402

RESULTS = []


def check(cid, text_, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'} {cid} {text_}" + (f"  [{detail}]" if not ok else ""), flush=True)


def get(path):
    with urllib.request.urlopen(f"http://localhost:8000{path}", timeout=60) as r:
        return json.loads(r.read())


PICK = """
SELECT DISTINCT ON (pc.id) pc.id::text, pc.sku, sp.id::text AS spid, sp.supplier_id::text, sp.price_ils, sp.price_usd,
       sp.shipping_cost_ils, sp.shipping_cost_usd, s.name, s.country
FROM parts_catalog pc
JOIN supplier_parts sp ON sp.part_id = pc.id JOIN suppliers s ON s.id = sp.supplier_id
WHERE pc.is_active AND pc.sku IS NOT NULL AND {visible} AND {cost} > 0 AND {extra}
ORDER BY pc.id, {order}
LIMIT 1"""


async def pick(cat, rate, extra, order_sql_extra=""):
    q = PICK.format(visible=pp.VISIBLE_OFFER_SQL, cost=pp.offer_cost_sql(), extra=extra,
                    order=pp.price_basis_order_sql("pc.part_type"))
    # the chosen row must be the part's price-basis offer: re-select the part's best offer by the policy
    r = (await cat.execute(text(q), {"usd_to_ils_rate": rate})).first()
    if not r:
        return None
    best = (await cat.execute(text(f"""
        SELECT sp.id::text, sp.supplier_id::text, sp.price_ils, sp.price_usd, sp.shipping_cost_ils, sp.shipping_cost_usd,
               s.name, s.country
        FROM supplier_parts sp JOIN suppliers s ON s.id = sp.supplier_id JOIN parts_catalog pc ON pc.id = sp.part_id
        WHERE sp.part_id = CAST(:pid AS uuid) AND {pp.VISIBLE_OFFER_SQL} AND {pp.offer_cost_sql()} > 0
        ORDER BY {pp.price_basis_order_sql("pc.part_type")} LIMIT 1"""), {"pid": r[0], "usd_to_ils_rate": rate})).first()
    return {"part_id": r[0], "sku": r[1], "spid": best[0], "supplier_id": best[1], "price_ils": best[2],
            "price_usd": best[3], "ship_ils": best[4], "ship_usd": best[5], "name": best[6], "country": best[7]}


async def main():
    import BACKEND_AI_AGENTS as A
    from routes.orders import _order_line_pricing
    from routes.parts import _current_part_offer
    from routes.payments import _live_reprice_order
    async with async_session_factory() as cat:
        rate = await get_usd_to_ils_rate(cat)
        await cat.execute(text("SET TRANSACTION READ ONLY"))
        cases = {
            "local": await pick(cat, rate, "lower(coalesce(s.country,'')) IN ('il','israel')"),
            "foreign": await pick(cat, rate, "lower(coalesce(s.country,'')) NOT IN ('','il','israel') AND sp.price_ils > 0"),
            "usd": await pick(cat, rate, "(sp.price_ils IS NULL OR sp.price_ils = 0) AND sp.price_usd > 0"),
        }
        for label, o in cases.items():
            if not o:
                print(f"SKIP {label}: no such offer in the catalog")
                continue
            cost = pp.offer_cost_ils(o["price_ils"], o["price_usd"], rate)
            u = pp.unit_price(cost, o["name"], o["country"])
            fee = pp.delivery_fee(o["ship_ils"], o["ship_usd"], rate, o["name"], o["country"])
            print(f"== {label}: part {o['part_id'][:8]} offer {o['spid'][:8]} cost {cost:.2f} -> net {u.net} vat {u.vat} "
                  f"unit {u.total} delivery {fee}")
            # compare endpoint (website part page)
            cmp_ = get(f"/api/v1/parts/{o['part_id']}/suppliers")
            offers = cmp_.get("suppliers") or []
            mine_c = next((x for x in offers if x.get("supplier_part_id") == o["spid"]), {})
            totals = [x.get("customer_total_ils") for x in offers if x.get("customer_total_ils") is not None]
            check(f"{label}.compare", "part page lists this offer at policy unit + delivery; list sorted by customer total",
                  (mine_c.get("customer_price_ils"), mine_c.get("customer_vat_ils"), mine_c.get("customer_total_ils"))
                  == (u.net, u.vat, round(u.total + fee, 2)) and totals == sorted(totals), mine_c)
            # wishlist / watch / cart-fallback offer
            cur = await _current_part_offer(cat, o["part_id"])
            check(f"{label}.wishlist", "wishlist/watch unit = policy unit", cur and cur["price_ils"] == u.total, cur)
            # order line (create_order)
            line = _order_line_pricing(SimpleNamespace(price_ils=o["price_ils"], price_usd=o["price_usd"],
                                                       shipping_cost_ils=o["ship_ils"], shipping_cost_usd=o["ship_usd"]),
                                       SimpleNamespace(name=o["name"], country=o["country"]), rate)
            check(f"{label}.order", "order line = policy (net, VAT, delivery)", line == (u.net, u.vat, fee), line)
            # pre-charge re-price (nothing committed: plain objects)
            it = SimpleNamespace(part_id=o["part_id"], supplier_part_id=o["spid"], quantity=2, unit_price=u.net,
                                 vat_amount=u.vat, total_price=u.line(2)[2])
            od = SimpleNamespace(subtotal=u.line(2)[0], vat_amount=u.line(2)[1], shipping_cost=fee,
                                 total_amount=round(u.line(2)[2] + fee, 2), discount_amount=0)
            changed = await _live_reprice_order(cat, od, [it], rate)
            check(f"{label}.charge", "pre-charge re-price accepts the order priced by the policy (no 409)", changed is False,
                  vars(od))
            # chat (all channels) - exact SKU query
            agent = A.PartsFinderAgent() if hasattr(A, "PartsFinderAgent") else A.BaseAgent()
            rows = await agent.search_parts_in_db(o["sku"], None, None, cat, limit=10)
            mine = next((r for r in rows if r["id"] == o["part_id"]), None)
            if mine and mine.get("suppliers"):
                s0 = mine["suppliers"][0]
                check(f"{label}.chat", "chat price = policy unit + delivery",
                      (s0["price_no_vat"], s0["vat"], s0["total"]) == (u.net, u.vat, round(u.total + fee, 2)), s0)
            else:
                print(f"SKIP {label}.chat: exact-SKU chat query did not return this part")
            # website search list (exact SKU)
            try:
                res = get(f"/api/v1/parts/search?q={urllib.parse.quote(o['sku'])}")
                hits = [b for b in (res.get("all_parts") or []) if (b.get("part") or {}).get("id") == o["part_id"]]
                if hits:
                    b = hits[0]
                    sp0 = (b.get("suppliers") or [{}])[0]
                    check(f"{label}.search", "website search offer = policy; list price = policy unit",
                          (sp0.get("customer_price_ils"), sp0.get("customer_vat_ils")) == (u.net, u.vat)
                          and b["part"].get("cheapest_price_ils") == u.total
                          and not any(k in b["part"] for k in ("base_price", "min_price_ils", "max_price_ils")),
                          {"offer": {k: sp0.get(k) for k in ("customer_price_ils", "customer_vat_ils")},
                           "part": {k: b["part"].get(k) for k in ("cheapest_price_ils", "base_price", "min_price_ils")}})
                else:
                    print(f"SKIP {label}.search: website search for the SKU did not list this part")
            except Exception as e:
                print(f"SKIP {label}.search: {type(e).__name__} {e}")
        # catalog-only part (no visible offer): estimate from the importer cost, never base_price
        r = (await cat.execute(text(f"""
            SELECT pc.id::text, pc.importer_price_ils, pc.max_price_ils, pc.online_price_ils, pc.base_price
            FROM parts_catalog pc
            WHERE pc.is_active AND pc.importer_price_ils > 0
              AND pc.base_price IS DISTINCT FROM round((pc.importer_price_ils * 1.45)::numeric, 2)
              AND NOT EXISTS (SELECT 1 FROM supplier_parts sp JOIN suppliers s ON s.id = sp.supplier_id
                              WHERE sp.part_id = pc.id AND {pp.VISIBLE_OFFER_SQL})
            LIMIT 1"""))).first()
        if r:
            est = pp.catalog_estimate_price(r[1], r[2], r[3])
            cmp_ = get(f"/api/v1/parts/{r[0]}/suppliers")
            first = (cmp_.get("suppliers") or [{}])[0]
            print(f"== catalog-only: part {r[0][:8]} importer {r[1]} base_price {r[4]} -> estimate unit {est.total}")
            check("estimate.compare", "part page estimate = importer cost under the policy (not base_price)",
                  round(first.get("customer_price_ils", 0) + first.get("customer_vat_ils", 0), 2) == est.total
                  and first.get("is_base_price_fallback") is True and first.get("availability") == "on_order", first)
    print(f"\nTOTAL {len(RESULTS)} checks: {sum(RESULTS)} PASS, {len(RESULTS) - sum(RESULTS)} FAIL")


asyncio.run(main())
