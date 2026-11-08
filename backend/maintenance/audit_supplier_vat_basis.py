"""
Script: audit_supplier_vat_basis.py
Purpose: READ-ONLY audit of every Israeli (IL) supplier's stored-price VAT basis (2026-11-05).
Process: per supplier -> offer counts (all/priced/customer-visible) and the median ratio of the stored offer cost
         (supplier_parts.price_ils) to the part's importer cost (parts_catalog.importer_price_ils, the ex-VAT cost):
         ~1.00 => stored cost is ex-VAT (policy basis 'exclusive'); ~1.18 => stored cost contains VAT ('inclusive');
         anything else => not a source price ('unknown', needs verification). Writes nothing.
Data Imported/Modified: none. Data Sources: suppliers, supplier_parts, parts_catalog (sampled, 400 offers/supplier).
Missing Data Delegation: suppliers with no priced offers are reported with basis 'unknown' / no evidence.
Last Updated: 2026-11-05
"""
import asyncio, json, os, statistics
import asyncpg

SQL_SUP = "SELECT id, name, country, is_active FROM suppliers WHERE LOWER(BTRIM(COALESCE(country,''))) IN ('il','israel','ישראל') ORDER BY name"
SQL_CNT = """SELECT count(*) n, count(*) FILTER (WHERE price_ils > 0) priced,
  count(*) FILTER (WHERE price_ils > 0 AND is_available AND NULLIF(BTRIM(supplier_url),'') IS NOT NULL) visible
  FROM supplier_parts WHERE supplier_id = $1"""
SQL_SAMPLE = """SELECT sp.price_ils / NULLIF(pc.importer_price_ils, 0) AS r FROM supplier_parts sp
  JOIN parts_catalog pc ON pc.id = sp.part_id WHERE sp.supplier_id = $1 AND sp.price_ils > 0 AND pc.importer_price_ils > 0 LIMIT 400"""


def classify(med, n):
    if n < 20 or med is None:
        return "unknown", "too few comparable offers"
    if abs(med - 1.0) <= 0.02:
        return "exclusive", f"median stored/importer-cost ratio {med:.3f} (stored cost = ex-VAT cost)"
    if abs(med - 1.18) <= 0.02:
        return "inclusive", f"median ratio {med:.3f} (stored cost contains 18% VAT)"
    return "unknown", f"median ratio {med:.3f} matches neither 1.00 nor 1.18 (not a source price)"


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    await c.execute("SET statement_timeout = '20s'; SET default_transaction_read_only = on")
    out = []
    for s in await c.fetch(SQL_SUP):
        cnt = await c.fetchrow(SQL_CNT, s["id"])
        try:
            rs = [float(r["r"]) for r in await c.fetch(SQL_SAMPLE, s["id"]) if r["r"] is not None]
        except Exception as e:  # noqa: BLE001
            rs = []
        med = statistics.median(rs) if rs else None
        basis, why = classify(med, len(rs))
        out.append({"supplier": s["name"], "id": str(s["id"]), "active": s["is_active"], "offers": cnt["n"],
                    "priced": cnt["priced"], "visible": cnt["visible"], "sample": len(rs),
                    "median_ratio": None if med is None else round(med, 4), "stored_basis": basis, "evidence": why})
    print(json.dumps(out, ensure_ascii=False, indent=1))
    await c.close()

asyncio.run(main())
