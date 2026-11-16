"""READ-ONLY (run in autospare_backend). Build classification_1043_final_v3.json from v2 + owner product-page harvest results.

Owner page = current truth (JSON-LD brand == owner brand AND sku == article, verified by cpie_product_page_harvest.verify).
Eligible: v2 H rows whose group is 'H|creator=owner(WRONG_PRICE on right part)' (part created from the owner's own page).
Per row, against a FRESH snapshot of the live offer:
  live price == target             -> G  (PASS - PRESERVED, healed by an owner re-harvest)
  else                             -> REPAIR (price -> EUR x rate; url -> the verified owner page), disposition C_WRONG_PRICE[_URL]
Everything else (harvest unverified / other groups) keeps its H verdict with the exact reason. Nothing is invented."""
import asyncio, json, os, sys, hashlib, time, collections
import asyncpg
V2, HARV, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
CPIE = "88f6ce24-ab5f-4f99-8b41-03cf6828cf3d"
async def main():
    v2 = json.load(open(V2)); raw_v2 = open(V2, 'rb').read()
    harv = {}
    for f in HARV.split(','):
        for l in open(f):
            r = json.loads(l); harv[r['offer_id']] = r          # later file wins
    ids = [d['offer_id'] for d in v2 if d['verdict'].startswith('UNRESOLVED')]
    c = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://'))
    await c.execute("SET default_transaction_read_only=on")
    rows = await c.fetch("SELECT id::text id, part_id::text pid, supplier_id::text sid, supplier_sku sku, price_ils price, supplier_url url, "
                         "to_char(updated_at,'YYYY-MM-DD\"T\"HH24:MI:SS.US') ua FROM supplier_parts WHERE id=ANY($1::uuid[])", ids)
    await c.close()
    live = {r['id']: dict(r) for r in rows}
    out, cnt = [], collections.Counter()
    for d in v2:
        d = dict(d)
        if not d['verdict'].startswith('UNRESOLVED'):
            out.append(d); cnt['carried:' + d['verdict']] += 1; continue
        h, l = harv.get(d['offer_id']), live.get(d['offer_id'])
        if l is None or l['pid'] != d['part_id'] or l['sku'] != d['article'] or l['sid'] != CPIE:
            d['v3_note'] = 'live offer missing/identity changed - not touched'; out.append(d); cnt['H:identity_changed'] += 1; continue
        if not d['consultant_group'].startswith('H|creator=owner'):
            d['v3_note'] = 'part-level identity decision required (part_identity.classify) - excluded from owner-price UPDATE'
            out.append(d); cnt['H:part_level_decision'] += 1; continue
        if not h:
            d['v3_note'] = 'no owner page fetched yet'; out.append(d); cnt['H:not_fetched'] += 1; continue
        if not h['verified']:
            d['v3_note'] = 'owner page not usable: ' + h['reason']; d['harvest_attempt'] = {k: h.get(k) for k in ('page_url', 'fetched_at', 'reason', 'fetched')}
            out.append(d); cnt['H:owner_page_unverified'] += 1; continue
        tp = float(h['target_price_ils']); cur = float(l['price'])
        ev = {'source': 'owner brand product/listing page', 'page_url': h['page_url'], 'fetched_at': h['fetched_at'], 'body_sha256': h.get('body_sha256'),
              'brand': h['parsed']['brand'], 'sku': h['parsed']['sku'], 'price_eur': h['parsed']['price_eur'], 'availability': h['parsed'].get('availability'),
              'eur_ils_rate': float(os.getenv('EUR_ILS_RATE', '3.48')), 'vat_basis': 'foreign (IE) supplier price, 0% VAT; policy adds margin/VAT at display'}
        if cur == tp and l['url'] == h['page_url']:
            d['verdict'] = 'PASS - PRESERVED'; d['disposition'] = 'G_VALID_CURRENT_STATE'; d['v3_note'] = 'live value already equals the owner page (healed by owner re-harvest)'
            d['owner_page_evidence'] = ev; cnt['G:healed'] += 1
        else:
            d['verdict'] = 'REPAIR'
            d['disposition'] = 'C_WRONG_PRICE' if l['url'] == h['page_url'] else 'C_WRONG_PRICE_AND_URL'
            d['target'] = {'price': tp, 'url': h['page_url'], 'evidence': ev}
            d['current_at_snapshot'] = {'price': cur, 'url': l['url'], 'updated_at': l['ua']}
            cnt['REPAIR:' + d['disposition']] += 1
        out.append(d)
    json.dump(out, open(OUT, 'w'))
    rep = [d for d in out if d['verdict'] == 'REPAIR']
    ratios = sorted(d['target']['price'] / d['current_at_snapshot']['price'] for d in rep if d['current_at_snapshot']['price'] > 0)
    print(json.dumps({'v2_sha256': hashlib.sha256(raw_v2).hexdigest(), 'v3_sha256': hashlib.sha256(open(OUT, 'rb').read()).hexdigest(),
                      'counts': cnt, 'repair_rows': len(rep),
                      'target_over_current_ratio_p1_p50_p99': [ratios[len(ratios)//100], ratios[len(ratios)//2], ratios[-1 - len(ratios)//100]] if ratios else None,
                      'run_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, indent=1))
asyncio.run(main())
