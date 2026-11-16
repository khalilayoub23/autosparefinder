"""
Script:  maintenance/cpie_product_page_harvest.py
Purpose: Targeted OWNER-BRAND price recovery for the wrong Car-Parts.ie offers (2026-11-16 mission). For each classified offer it
         fetches the OWNER part's own car-parts.ie page (`parts_catalog.specifications.product_url`) and records what the page itself
         says (brand, article, EUR price, availability). The harvester only visits model/category LISTING pages, so owner parts that
         were created from a product page are never re-priced by it - this is the missing targeted path.
Process:  1. read the classification (sha256 printed)  2. READ-ONLY lookup of each owner part's product_url
          3. fetch through the harvester's own cf_clearance path (shared on-disk cookie cache, no second solver)  4. parse the page
          (product page: JSON-LD Product; listing page: the block whose article == the offer's article)  5. a row is VERIFIED only when
          the page's brand == the owner brand AND its article == the offer's article AND the price is a positive EUR figure
          6. append one JSON line per offer to --out (resumable; failures are recorded with their exact reason, never dropped).
Data Imported/Modified: NONE in the database. Writes only --out (JSONL). The repair itself goes through cpie_overwrite_repair.py with a
         pinned manifest - this script never writes an offer.
Data Sources: https://www.car-parts.ie/ (the owner brand's own product / listing page).
Missing Data Delegation: an unverified row stays UNRESOLVED (no price is invented, no foreign-brand page is ever used as proof).
Last Updated: 2026-11-16
"""
import argparse, asyncio, hashlib, html as _html, json, os, re, sys, time

EUR_ILS = float(os.getenv("EUR_ILS_RATE", "3.48"))   # the importer's own rate (importers/car_parts_ie_import_generic.py)
OWNER_GROUP_PREFIX = "H|creator=owner"


def _norm(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def parse_product_page(body):
    """Pure. JSON-LD Product of a /p-<id> page -> dict(kind, brand, sku, mpn, price_eur, currency, availability, url) or None."""
    for m in re.finditer(r'<script type="application/ld\+json">(\{.*?\})</script>', body or "", re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if d.get("@type") != "Product":
            continue
        offer = d.get("offers") or {}
        if isinstance(offer, list):
            offer = offer[0] if offer else {}
        try:
            price = float(str(offer.get("price")).replace(",", ""))
        except (TypeError, ValueError):
            price = None
        brand = d.get("brand")
        brand = brand.get("name") if isinstance(brand, dict) else brand
        return {"kind": "product_page", "brand": _html.unescape(brand or ""), "sku": str(d.get("sku") or ""),
                "mpn": str(d.get("mpn") or ""), "price_eur": price, "currency": offer.get("priceCurrency"),
                "availability": (offer.get("availability") or "").rsplit("/", 1)[-1], "url": offer.get("url")}
    return None


def parse_listing_row(parts, article, owner_brand):
    """Pure. From harvester `parse_parts` rows: the one block whose article == `article` and whose title starts with the owner brand
    (multi-word brands included). Returns dict or None; >1 distinct prices for the same brand+article => None (ambiguous)."""
    hit = [p for p in parts if _norm(p.get("sku")) == _norm(article) and _norm(p.get("name")).startswith(_norm(owner_brand))]
    if not hit or len({p.get("price_eur") for p in hit}) != 1:
        return None
    p = hit[0]
    return {"kind": "listing_row", "brand": owner_brand, "sku": p["sku"], "mpn": p["sku"], "price_eur": p.get("price_eur"),
            "currency": "EUR", "availability": None, "url": p.get("source_url")}


def verify(row, parsed):
    """Pure. (verified, reason). Identity = page brand == owner brand AND page article == offer article; price must be positive EUR."""
    if not parsed:
        return False, "page_has_no_parsable_owner_product"
    if _norm(parsed["brand"]) != _norm(row["owner_brand"]):
        return False, f"brand_mismatch:{parsed['brand']}"
    if _norm(row["article"]) not in (_norm(parsed["sku"]), _norm(parsed["mpn"])):
        return False, f"article_mismatch:{parsed['sku']}"
    if parsed.get("currency") not in (None, "EUR"):
        return False, f"currency:{parsed.get('currency')}"
    if not parsed.get("price_eur") or parsed["price_eur"] <= 0:
        return False, "no_positive_price"
    return True, "ok"


def target_price_ils(price_eur):
    return round(float(price_eur) * EUR_ILS, 2)


async def _product_urls(part_ids):
    import asyncpg
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    c = await asyncpg.connect(dsn)
    try:
        await c.execute("SET default_transaction_read_only=on")
        rs = await c.fetch("SELECT id::text id, specifications->>'product_url' u FROM parts_catalog WHERE id=ANY($1::uuid[])", part_ids)
    finally:
        await c.close()
    return {r["id"]: r["u"] for r in rs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--classification", required=True)
    ap.add_argument("--out", required=True, help="JSONL, resumable")
    ap.add_argument("--groups", default=OWNER_GROUP_PREFIX, help="consultant_group prefix to fetch")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.6)
    ap.add_argument("--max-consecutive-fail", type=int, default=8)
    a = ap.parse_args()

    raw = open(a.classification, "rb").read()
    print(json.dumps({"classification_sha256": hashlib.sha256(raw).hexdigest()}), flush=True)
    rows = [r for r in json.loads(raw) if r["verdict"].startswith("UNRESOLVED") and r["consultant_group"].startswith(a.groups)]
    done = set()
    if os.path.exists(a.out):
        done = {json.loads(l)["offer_id"] for l in open(a.out) if l.strip()}
    todo = [r for r in rows if r["offer_id"] not in done]
    if a.limit:
        todo = todo[: a.limit]
    urls = asyncio.run(_product_urls(list({r["part_id"] for r in todo})))

    import car_parts_ie_flaresolverr_harvester as h
    h.ensure_clearance()
    fails = 0
    with open(a.out, "a") as out:
        for i, r in enumerate(todo, 1):
            url = urls.get(r["part_id"])
            rec = {"offer_id": r["offer_id"], "part_id": r["part_id"], "article": r["article"], "owner_brand": r["owner_brand"],
                   "page_url": url, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            if not url or "car-parts.ie" not in url:
                rec.update(fetched=False, verified=False, reason="owner_part_has_no_product_url")
            else:
                body = h.http_get(url)
                rec["fetched"] = bool(body)
                if not body:
                    rec.update(verified=False, reason="fetch_failed_or_blocked")
                    fails += 1
                else:
                    fails = 0
                    rec["body_sha256"] = hashlib.sha256(body.encode()).hexdigest()
                    parsed = parse_product_page(body) if re.search(r"/p-\d+", url) else \
                        parse_listing_row(h.parse_parts(body, url.split("car-parts.ie", 1)[1]), r["article"], r["owner_brand"])
                    ok, why = verify(r, parsed)
                    rec.update(parsed=parsed, verified=ok, reason=why)
                    if ok:
                        rec["target_price_ils"] = target_price_ils(parsed["price_eur"])
            out.write(json.dumps(rec) + "\n"); out.flush()
            if i % 25 == 0:
                print(f"{i}/{len(todo)} verified-so-far fails_in_row={fails}", flush=True)
            if fails >= a.max_consecutive_fail:
                print("STOP: consecutive fetch failures - clearance likely lost; resumable", flush=True)
                break
            time.sleep(a.sleep)


if __name__ == "__main__":
    main()
