"""READ-ONLY (run in autospare_backend). classification v4 + NEW owner product-page evidence -> classification v5 (ADDITIONAL repair rows only).

Promotion standard = the 787 (v4): the page is a /p-<id> PRODUCT page, JSON-LD brand == owner brand, sku == article, positive EUR price
(cpie_product_page_harvest.verify), checked against a FRESH snapshot of the live offer (same part, same sku, same supplier).
Identity cases (v3_note 'part-level identity decision required') additionally need part_identity evidence: the part's own title brand
== the owner brand (identity_cases json). Rows already in the v4 manifest are carried with verdict 'REPAIR_IN_V4_MANIFEST' so the manifest
builder (which takes verdict == 'REPAIR') emits ONLY the new rows. Nothing is invented; an unverified row keeps its H verdict + exact reason."""
import asyncio, json, os, sys, hashlib, time, collections
import asyncpg
V4, IDENT, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
HARV = sys.argv[4].split(',')
CPIE = "88f6ce24-ab5f-4f99-8b41-03cf6828cf3d"
async def main():
    v4 = json.load(open(V4)); raw = open(V4, 'rb').read()
    ident = {o['offer_id']: o for o in json.load(open(IDENT))}
    harv = {}
    for f in HARV:
        for l in open(f):
            r = json.loads(l); harv[r['offer_id']] = r
    ids = [d['offer_id'] for d in v4 if d['disposition'] == 'H_UNRESOLVED']
    c = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://'))
    await c.execute("SET default_transaction_read_only=on")
    rows = await c.fetch("SELECT id::text id, part_id::text pid, supplier_id::text sid, supplier_sku sku, price_ils price, supplier_url url, "
                         "to_char(updated_at,'YYYY-MM-DD\"T\"HH24:MI:SS.US') ua FROM supplier_parts WHERE id=ANY($1::uuid[])", ids)
    await c.close()
    live = {r['id']: dict(r) for r in rows}
    out, cnt = [], collections.Counter()
    for d in v4:
        d = dict(d)
        if d['disposition'] == 'C_WRONG_PRICE_AND_URL' or d['disposition'] == 'C_WRONG_PRICE':
            d['verdict'] = 'REPAIR_IN_V4_MANIFEST' if d['disposition'] == 'C_WRONG_PRICE_AND_URL' and d['verdict'] == 'REPAIR' else d['verdict']
            out.append(d); cnt['carried:' + d['disposition']] += 1; continue
        if d['disposition'] != 'H_UNRESOLVED':
            out.append(d); cnt['carried:' + d['disposition']] += 1; continue
        h, l = harv.get(d['offer_id']), live.get(d['offer_id'])
        if l is None or l['pid'] != d['part_id'] or l['sku'] != d['article'] or l['sid'] != CPIE:
            d['v5_note'] = 'live offer missing/identity changed - not touched'; out.append(d); cnt['H:identity_changed'] += 1; continue
        is_ident = 'part-level identity' in (d.get('v3_note') or '')
        if is_ident:
            io = ident.get(d['offer_id'])
            d['v5_identity'] = {'title_brand': (io or {}).get('title_brand'), 'owner_brand': d['owner_brand'],
                                'siblings': collections.Counter(s['verdict'] for s in (io or {}).get('siblings', []))}
            if not io or (io.get('title_brand') or '').upper().replace(' ', '') != d['owner_brand'].upper().replace(' ', ''):
                d['v5_note'] = 'identity: part title brand != owner brand - offer stays unresolved'; out.append(d); cnt['H:identity_not_owner_part'] += 1; continue
        if not h:
            d['v5_note'] = 'no new owner page evidence'; out.append(d); cnt['H:no_new_evidence'] += 1; continue
        if not h['verified']:
            d['v5_note'] = 'owner page not usable: ' + h['reason']; d['v5_attempt'] = {k: h.get(k) for k in ('page_url', 'fetched_at', 'reason', 'fetched', 'page_url_original')}
            out.append(d); cnt['H:owner_page_unverified'] += 1; continue
        if (h.get('parsed') or {}).get('kind') != 'product_page':
            d['v5_note'] = 'owner evidence is still a listing row (weaker identity proof)'; out.append(d); cnt['H:listing_row_evidence_only'] += 1; continue
        tp = float(h['target_price_ils']); cur = float(l['price'])
        ev = {'source': 'owner brand product page', 'page_url': h['page_url'], 'fetched_at': h['fetched_at'], 'body_sha256': h.get('body_sha256'),
              'brand': h['parsed']['brand'], 'sku': h['parsed']['sku'], 'price_eur': h['parsed']['price_eur'], 'availability': h['parsed'].get('availability'),
              'eur_ils_rate': float(os.getenv('EUR_ILS_RATE', '3.48')), 'vat_basis': 'foreign (IE) supplier price, 0% VAT; policy adds margin/VAT at display'}
        if cur == tp and l['url'] == h['page_url']:
            d['verdict'] = 'PASS - PRESERVED'; d['disposition'] = 'G_VALID_CURRENT_STATE'; d['owner_page_evidence'] = ev; cnt['G:already_correct'] += 1
        else:
            d['verdict'] = 'REPAIR'
            d['disposition'] = 'C_WRONG_PRICE' if l['url'] == h['page_url'] else 'C_WRONG_PRICE_AND_URL'
            d['target'] = {'price': tp, 'url': h['page_url'], 'evidence': ev}
            d['current_at_snapshot'] = {'price': cur, 'url': l['url'], 'updated_at': l['ua']}
            d['v5_group'] = 'identity' if is_ident else ('listing_upgrade' if 'listing row' in (d.get('v3_note') or '') else 'page_retry')
            cnt['REPAIR:' + d['v5_group']] += 1
        out.append(d)
    json.dump(out, open(OUT, 'w'))
    print(json.dumps({'v4_sha256': hashlib.sha256(raw).hexdigest(), 'v5_sha256': hashlib.sha256(open(OUT, 'rb').read()).hexdigest(), 'counts': cnt,
                      'run_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, indent=1, default=dict))
asyncio.run(main())
