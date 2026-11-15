"""Workstream C sandbox DB test: two brands, one article number, same vehicle make, repeated imports, both arrival orders.
Runs ONLY against a throw-away database (sandbox_c*), never production."""
import asyncio, json, os, sys, subprocess, tempfile, pathlib
import asyncpg
DBN = sys.argv[1]; ORDER = sys.argv[2]            # database name, 'AB' or 'BA'
base = os.environ['SANDBOX_DSN_BASE']             # postgresql://u:p@sandbox_postgres:5432
os.environ['DATABASE_URL'] = f"{base}/{DBN}"
A = {"name": "MAPCO 75659 Toyota C-HR timing chain", "inferred_sku": "75659", "price_eur": 10.0, "product_url": "https://www.car-parts.ie/car-parts/toyota/c-hr/timing-chain", "brand": "MAPCO"}
B = {"name": "FAE 75659 Toyota C-HR lambda sensor", "inferred_sku": "75659", "price_eur": 20.0, "product_url": "https://www.car-parts.ie/car-parts/toyota/c-hr/lambda-sensor", "brand": "FAE"}
order = [A, B] if ORDER == 'AB' else [B, A]
tmp = pathlib.Path(tempfile.mkdtemp())
def run(parts, n):
    f = tmp / f"{n}.json"; f.write_text(json.dumps({"manufacturer": "Toyota", "vehicle_slug": "toyota/c-hr", "parts": parts}))
    r = subprocess.run([sys.executable, "/work/t/car_parts_ie_import_generic.py", "--brand", "toyota", "--file", str(f)], capture_output=True, text=True, env=os.environ)
    return r.returncode, (r.stdout + r.stderr)[-400:]
async def state():
    c = await asyncpg.connect(os.environ['DATABASE_URL'])
    parts = await c.fetch("select sku,name from parts_catalog where oem_number='75659' order by sku")
    offs = await c.fetch("select sp.supplier_sku, sp.price_ils::float p, p.name from supplier_parts sp join parts_catalog p on p.id=sp.part_id join suppliers s on s.id=sp.supplier_id where s.name='Car-Parts.ie' and sp.supplier_sku like '%75659' order by 1")
    await c.close(); return [tuple(x) for x in parts], [tuple(x) for x in offs]
async def main():
    snaps = []
    for i, parts in enumerate([[order[0]], [order[1]], [order[0], order[1]], [order[1], order[0]]]):  # 1st/2nd arrival, then repeats in both orders
        rc, out = run(parts, i); snaps.append(await state())
        if rc != 0: print('IMPORT RC', rc, out)
    parts, offs = snaps[-1]
    print('PARTS', parts); print('OFFERS', offs)
    assert len(parts) == 2, f"expected 2 distinct part rows, got {parts}"
    assert len(offs) == 2, f"expected 2 offers, got {offs}"
    by_brand = {('MAPCO' if 'MAPCO' in o[2] else 'FAE'): o[1] for o in offs}
    assert by_brand == {'MAPCO': 34.8, 'FAE': 69.6}, by_brand
    assert abs(by_brand['MAPCO'] - by_brand['FAE']) > 1, "prices collapsed"
    assert snaps[2] == snaps[3] == snaps[1], "repeated imports changed state"
    print('OK order', ORDER)
asyncio.run(main())
