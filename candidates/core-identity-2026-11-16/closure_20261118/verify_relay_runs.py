# READ-ONLY verification of N relay runs (CORE closure checklist, section 7). Adapted from the 2026-11-17 consultant script.
# Usage (inside the container, read-only DB session):
#   docker exec -i -w /app autospare_backend python3 - <START_UTC> <END_UTC> < verify_relay_runs.py 2>/dev/null
#   e.g.  ... python3 - 2026-11-27T07:00 2026-11-27T12:00 < verify_relay_runs.py     (UTC = IL - 2h in winter)
# Runs = every /app/state/relay/results/*.json written in the window whose state is ok; payloads are /app/state/relay/payloads/*__<run>.json
# PASS criteria: offer_check == only price_eq_owner_brand; item_check has no own_offer_price_MISMATCH / no_own_offer / no_part_row;
#   dup_* == 0; brand_unresolved only for brands without catalogue evidence (list them).
import sys, glob as _g, os as _o, datetime as _d2
import asyncio, asyncpg, os, json, glob, re, sys
from collections import defaultdict, Counter
sys.path.insert(0, '/app')
import cpie_identity, cpie_fitment_guard
from category_map import categorize_on_ingest, categorize_slug

DB = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')
R = '/app/state/relay'
import datetime as _dt
W0, W1 = _dt.datetime.fromisoformat(sys.argv[1]), _dt.datetime.fromisoformat(sys.argv[2])
import json as _j
RUNS = []
for _f in sorted(_g.glob(R + '/results/*.json')):
    _t = _dt.datetime.utcfromtimestamp(_o.path.getmtime(_f))
    if W0 <= _t <= W1 and _j.load(open(_f)).get('state') == 'ok':
        RUNS.append(_o.path.basename(_f).rsplit('__', 1)[1].split('.')[0])
print('runs in window:', len(RUNS), file=sys.stderr)
RATE = 3.48

def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

async def main():
    c = await asyncpg.connect(DB, server_settings={'default_transaction_read_only': 'on', 'statement_timeout': '300000'})
    assert (await c.fetchval("show default_transaction_read_only")) == 'on'
    out = {}
    sid = await c.fetchval("select id from suppliers where name='Car-Parts.ie'")
    out['supplier'] = dict(await c.fetchrow("select id::text, name, country, is_active, vat_price_basis from suppliers where id=$1", sid))
    items = []
    for run in RUNS:
        p = glob.glob(f'{R}/payloads/*__{run}.json')[0]
        tag = os.path.basename(p).split('__')[0]
        d = json.load(open(p))
        manufacturer = clean(d.get('manufacturer') or tag).title()
        prefix = re.sub(r"[^A-Z0-9]", "", manufacturer.upper())[:5]
        vs = d.get('vehicle_slug')
        n = len(d['parts'])
        for it in d['parts']:
            sku = clean(it.get('sku'))
            name = clean(it.get('name') or sku)
            br = cpie_identity.brand_of(name, sku)
            slug = cpie_fitment_guard.slug_of(it.get('source_url') or '')
            items.append(dict(run=run, vs=vs, prefix=prefix, sku=sku, name=name, brand=br, price_eur=it.get('price_eur'),
                              exp_ils=round(float(it['price_eur']) * RATE, 2) if it.get('price_eur') else None,
                              url=clean(it.get('product_url') or it.get('source_url')), slug=slug, n=n,
                              block=bool(cpie_fitment_guard._BLOCK_TITLE_RE.match(name)),
                              guard_now=cpie_fitment_guard.fitment_allowed(n, name, slug, '')))
    out['items'] = len(items)
    # resolve part rows
    for it in items:
        if not it['brand']:
            it['part'] = None; continue
        leg = f"{it['prefix']}-{it['sku']}"
        r = await c.fetchrow("select id::text, name, base_price, updated_at, is_active, category from parts_catalog where sku=$1", leg)
        if r and cpie_identity.brand_of(r['name'], it['sku']) == it['brand']:
            it['part'] = dict(r); it['part_sku'] = leg
        else:
            k = f"{it['prefix']}-{cpie_identity._tok(it['brand'])}{cpie_identity.SEP}{it['sku']}"
            r2 = await c.fetchrow("select id::text, name, base_price, updated_at, is_active, category from parts_catalog where sku=$1", k)
            it['part'] = dict(r2) if r2 else None; it['part_sku'] = k if r2 else None
            it['legacy_brand'] = cpie_identity.brand_of(r['name'], it['sku']) if r else None
    # offers on each item's part
    for it in items:
        if not it.get('part'):
            it['own'] = []; continue
        rows = await c.fetch("select id::text, supplier_sku, price_ils::float as p, updated_at, is_available, supplier_url from supplier_parts where supplier_id=$1 and part_id=$2::uuid", sid, it['part']['id'])
        it['own'] = [dict(x) for x in rows]
    # offer-centric: offers updated in window
    offs = await c.fetch("""select sp.id::text, sp.supplier_sku, sp.price_ils::float p, sp.part_id::text, sp.updated_at, sp.created_at, sp.supplier_url, sp.is_available,
                               pc.name pname, pc.sku psku from supplier_parts sp join parts_catalog pc on pc.id=sp.part_id
                            where sp.supplier_id=$1 and sp.updated_at between $2 and $3""", sid, W0, W1)
    by_art = defaultdict(list)
    for it in items:
        by_art[it['sku']].append(it)
    res = Counter(); bad = []
    scoped_new = 0; scoped = 0
    for o in offs:
        tokb, art = cpie_identity.article_of(o['supplier_sku'])
        if '@' in o['supplier_sku']:
            scoped += 1
        pb = cpie_identity.brand_of(o['pname'], art)
        cands = by_art.get(art, [])
        own_prices = {i['exp_ils'] for i in cands if i['brand'] == pb}
        other_prices = {i['exp_ils'] for i in cands if i['brand'] != pb}
        if not cands:
            res['no_payload_item'] += 1; bad.append(('no_payload_item', o['supplier_sku'], o['pname'], o['p'])); continue
        if o['p'] in own_prices:
            res['price_eq_owner_brand'] += 1
        elif o['p'] in other_prices:
            res['price_eq_FOREIGN_brand'] += 1; bad.append(('foreign', o['supplier_sku'], o['pname'], o['p'], sorted(other_prices)))
        else:
            res['price_not_in_payload'] += 1; bad.append(('nomatch', o['supplier_sku'], o['pname'], o['p'], sorted(own_prices), sorted(other_prices)))
        if tokb is not None and pb and tokb != cpie_identity._tok(pb):
            res['token_ne_part_brand'] += 1; bad.append(('token', o['supplier_sku'], o['pname']))
        if not pb:
            res['offer_part_brand_unresolved'] += 1
    out['offers_window'] = len(offs); out['offers_scoped_in_window'] = scoped
    out['offers_created_in_window'] = sum(1 for o in offs if o['created_at'] >= W0)
    out['offer_check'] = dict(res); out['offer_bad'] = bad[:40]
    # item-centric: each imported item has its own offer with its own price?
    ic = Counter(); ic_ex = defaultdict(list)
    for it in items:
        if not it['brand']:
            ic['brand_unresolved'] += 1; ic_ex['brand_unresolved'].append((it['vs'], it['sku'], it['name'], it['price_eur'])); continue
        if not it.get('part'):
            ic['no_part_row'] += 1; ic_ex['no_part_row'].append((it['vs'], it['sku'], it['name'], it.get('legacy_brand'))); continue
        own = [o for o in it['own'] if cpie_identity.article_of(o['supplier_sku'])[1] == it['sku']]
        if not own:
            ic['no_own_offer'] += 1
            h = await c.fetchrow("select sp.part_id::text, pc.name, pc.sku from supplier_parts sp join parts_catalog pc on pc.id=sp.part_id where sp.supplier_id=$1 and sp.supplier_sku=$2", sid, it['sku'])
            ic_ex['no_own_offer'].append((it['vs'], it['sku'], it['name'], it['part_sku'], [o['supplier_sku'] for o in it['own']],
                                          (h['sku'], h['name'], cpie_identity.brand_of(h['name'], it['sku'])) if h else None))
            continue
        o = own[0]
        same_art_prices = {i['exp_ils'] for i in by_art[it['sku']] if i['brand'] == it['brand']}
        if o['p'] == it['exp_ils']:
            ic['own_offer_price_eq_item'] += 1
        elif o['p'] in same_art_prices:
            ic['own_offer_price_eq_other_run_same_brand'] += 1
        else:
            ic['own_offer_price_MISMATCH'] += 1; ic_ex['mismatch'].append((it['vs'], it['sku'], it['name'], o['supplier_sku'], o['p'], it['exp_ils']))
        if '@' in o['supplier_sku']:
            ic['own_offer_scoped'] += 1
        if not (W0 <= o['updated_at'] <= W1):
            ic['own_offer_not_updated_in_window'] += 1
    out['item_check'] = dict(ic); out['item_examples'] = {k: v[:40] for k, v in ic_ex.items()}
    # duplicates
    out['dup_part_supplier_untyped'] = await c.fetchval("select count(*) from (select part_id from supplier_parts where supplier_id=$1 and part_type is null group by part_id having count(*)>1) x", sid)
    out['dup_supplier_sku'] = await c.fetchval("select count(*) from (select supplier_sku from supplier_parts where supplier_id=$1 group by supplier_sku having count(*)>1) x", sid)
    out['scoped_total'] = await c.fetchval("select count(*) from supplier_parts where supplier_id=$1 and supplier_sku ~ '^[A-Z0-9]{1,5}@'", sid)
    out['scoped_created_in_window'] = await c.fetchval("select count(*) from supplier_parts where supplier_id=$1 and supplier_sku ~ '^[A-Z0-9]{1,5}@' and created_at between $2 and $3", sid, W0, W1)
    out['offers_created_window'] = await c.fetchval("select count(*) from supplier_parts where supplier_id=$1 and created_at between $2 and $3", sid, W0, W1)
    out['price_history_window'] = [dict(x) for x in await c.fetch("select source, count(*) n from price_history where created_at between $1 and $2 group by 1", W0, W1)] if await c.fetchval("select count(*) from information_schema.columns where table_name='price_history' and column_name='created_at'") else 'no created_at'
    # persist items for later steps
    json.dump(items, open('/dev/stdout', 'w') if False else sys.stderr, default=str)
    print(json.dumps(out, default=str, indent=1))

asyncio.run(main())
