"""
Script: apply_supplier_vat_basis.py
Purpose: (#129) give every Israeli supplier an EXPLICIT stored-price VAT basis in suppliers.vat_price_basis, with evidence.
Process: dry run by default (prints what would change). --apply: ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS
         (lock_timeout 3s, nothing else touched), then one UPDATE per supplier recording prev value + evidence in
         suppliers.vat_basis_evidence. --rollback restores exactly the recorded previous values. Idempotent.
         Bases: inclusive = stored supplier_parts.price_ils contains 18% VAT; exclusive = it is the ex-VAT cost;
         unknown = no usable evidence (a LOCAL supplier with 'unknown' is never priced - price_policy.vat_basis_blocks).
Data Imported/Modified: suppliers.vat_price_basis, suppliers.vat_basis_evidence (new columns). No offer price is touched.
Data Sources: audit_supplier_vat_basis.py live ratios (stored offer cost / importer cost, 2026-11-05), importer source
         code (import_delek_brands stores ex-VAT; samelet/champion stored base_price = cost x1.45 -> repaired by
         repair_double_margin_129.py to the ex-VAT cost => exclusive), owner statement for Motorstore (2026-11-05).
Missing Data Delegation: suppliers without priced offers stay 'unknown' (nothing to price).
Last Updated: 2026-11-05
"""
import argparse
import asyncio
import json
import os

import asyncpg

INC, EXC, UNK = "inclusive", "exclusive", "unknown"

# name -> (basis, evidence). Evidence for 'exclusive (after repair)' = the stored cost WAS cost x1.45 (importer wrote
# base_price into supplier_parts.price_ils); repair_double_margin_129.py restores price_ils = importer_price_ils.
_RATIO_INC = "stored/importer-cost median ~1.18 over 400 sampled offers (stored cost contains 18% VAT)"
_RATIO_EXC = "stored/importer-cost median ~1.00 over 400 sampled offers (stored cost is ex-VAT)"
_DOUBLE = ("stored price_ils was importer cost x1.45 (importer wrote base_price) - repaired to the ex-VAT cost by "
           "repair_double_margin_129.py; ex-VAT thereafter")
CLASSIFICATION = {
    **{n: (INC, _RATIO_INC) for n in (
        "Colmobil - Genesis Israel", "Colmobil - Hyundai Israel", "Colmobil - JAECOO Israel",
        "Colmobil - Mitsubishi Israel", "Colmobil - ORA Israel", "Colmobil - Smart Israel",
        "Delek Motors Ford IL", "Delek Motors MAXUS IL", "Delek Motors Voyah IL", "BMW Delek Motors",
        "Mazda Israel - Delek Motors", "MCT (Mayer Group)", "Kia Israel", "Champion Motors IL",
        "NIO Delek Motors IL")},
    "Motorstore IL": (INC, "owner confirmed 2026-11-05: motorstore.co.il displayed prices include VAT. NOTE: its 2,325 "
                           "stored offers are NOT Motorstore source prices (bulk-derived 2026-07-28 from other price "
                           "lists, no supplier_url, 0 customer-visible) - classification only, prices untouched"),
    **{n: (EXC, _RATIO_EXC) for n in (
        "Delek Motors IL", "Elite Parts", "Ford Delek Motors", "Geo Mobility - Zeekr Israel",
        "Inbar Group - Land Rover Israel", "JLR Delek Motors", "Kia Official Importer Israel",
        "M-HERO 2 Delek Motors", "M-HERO Delek Motors", "MINI Delek Motors", "Toyota Israel - Union Motors")},
    "NIO Delek Motors": (EXC, "import_delek_brands.py stores retail EXCL. VAT in supplier_parts.price_ils (code); live "
                              "ratio vs importer cost is polluted by parts shared with the newer NIO importer"),
    "VOYAH Delek Motors": (EXC, "import_delek_brands.py stores retail EXCL. VAT (code); ratio 0.98 on shared parts"),
    **{n: (EXC, _DOUBLE) for n in (
        "Abarth Official Importer Israel", "Alfa Romeo Official Importer Israel", "BYD IL (Shlomo Motors)",
        "Champion Motors", "Fiat Official Importer Israel", "Jeep Official Importer Israel",
        "RAM Official Importer Israel", "Subaru Official Importer Israel", "WEY Official Importer Israel",
        "Subaru Israel - Samelet Motors", "Lexus Official Importer Israel")},
    "AutoParts Pro IL": (UNK, "115,116 synthetic placeholder offers (flat price_ils 100.00, no supplier_url, "
                              "0 customer-visible): not a source price, no basis can be derived"),
    **{n: (UNK, "no priced offers - nothing to price; classify when an importer exists") for n in (
        "home - autoworks", "Hongqi Official Importer Israel", "Iveco Official Importer Israel",
        "Jaguar Land Rover Delek Motors", "Leapmotor Israel", "PORSCHE Official Importer Israel",
        "Sandbox Supplier QA")},
}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"))
    has_col = await c.fetchval("SELECT count(*) FROM information_schema.columns WHERE table_name='suppliers' "
                               "AND column_name='vat_price_basis'")
    names = {r["name"]: r for r in await c.fetch(
        "SELECT name, country, " + ("vat_price_basis, vat_basis_evidence" if has_col else "NULL::text vat_price_basis, "
                                    "NULL::jsonb vat_basis_evidence") + " FROM suppliers")}
    missing = [n for n in CLASSIFICATION if n not in names]
    il = [n for n, r in names.items() if (r["country"] or "").strip().lower() in ("il", "israel", "ישראל")]
    unclassified = [n for n in il if n not in CLASSIFICATION]
    print(f"column_exists={bool(has_col)} classified={len(CLASSIFICATION)} IL_suppliers={len(il)} "
          f"missing_in_db={missing} IL_unclassified={unclassified}")
    if a.rollback:
        if not has_col:
            print("nothing to roll back"); return
        async with c.transaction():
            n = await c.execute("UPDATE suppliers SET vat_price_basis = NULLIF(vat_basis_evidence->>'prev',''), "
                                "vat_basis_evidence = NULL WHERE vat_basis_evidence->>'by' = '#129'")
        print("rolled back:", n); return
    plan = []
    for n, (basis, ev) in CLASSIFICATION.items():
        if n in names:
            cur = names[n]["vat_price_basis"]
            plan.append((n, cur, basis, cur != basis))
    for n, cur, basis, ch in plan:
        print(f"  {'CHANGE' if ch else 'same  '} {n[:38]:38} {cur} -> {basis}")
    if not a.apply:
        print("dry run - nothing written"); return
    await c.execute("SET lock_timeout = '3s'")
    await c.execute("ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS vat_price_basis varchar(16), "
                    "ADD COLUMN IF NOT EXISTS vat_basis_evidence jsonb")
    async with c.transaction():
        for n, cur, basis, ch in plan:
            if not ch:
                continue
            ev = {"by": "#129", "prev": cur, "basis": basis, "evidence": CLASSIFICATION[n][1], "date": "2026-11-05"}
            await c.execute("UPDATE suppliers SET vat_price_basis=$2, vat_basis_evidence=$3::jsonb WHERE name=$1",
                            n, basis, json.dumps(ev, ensure_ascii=False))
    print("applied:", sum(1 for p in plan if p[3]))
    await c.close()


asyncio.run(main())
