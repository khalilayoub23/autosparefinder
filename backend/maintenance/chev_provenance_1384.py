#!/usr/bin/env python3
"""
Script: chev_provenance_1384.py
Purpose: Record, for the 1,384 Chevrolet parts whose description says "IL retail (incl VAT)", WHERE their price came from and
         what is (not) known about its VAT basis - WITHOUT changing any price (FIXES_TRACKER 2026-11-11 closure, Issue 1).
Process: read /app/uploads/chevrolet.xlsx (col 0 catalog number, col 4 'מחיר לצרכן') -> match each part by catalog number ->
         merge into parts_catalog.specifications: source_file, source_price_ils (the file price that reproduces the stored
         importer cost = price/1.18; every distinct file price is listed when a number repeats), price_basis='unverified',
         candidate_cost_if_inclusive / candidate_cost_if_exclusive, evidence list, run id. Dry-run by default.
         --rollback RUN_ID removes exactly the keys it added.
Data Imported/Modified: parts_catalog.specifications (jsonb merge) ONLY. importer/max/min/base prices, updated_at: untouched.
Data Sources: backend/uploads/chevrolet.xlsx (Chevrolet IL / UMI consumer price list, docProps modified 2026-05-18).
Missing Data Delegation: owner/UMI: a written statement of the VAT basis of the list (then set the basis and re-derive).
Last Updated: 2026-11-11
"""
import argparse, asyncio, collections, json, os, re, uuid
import asyncpg, openpyxl

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
XLSX = os.environ.get("CHEV_XLSX", "/app/uploads/chevrolet.xlsx")
KEYS = ("source_file", "source_price_ils", "source_prices_in_file", "price_basis", "candidate_cost_if_inclusive",
        "candidate_cost_if_exclusive", "price_basis_evidence", "provenance_run")
EVIDENCE = [
    "xlsx header 'מחיר לצרכן' carries no VAT wording and the file has no VAT note (checked all cells, sheet names, docProps)",
    "chevrolet.co.il / cadillac.co.il / umi.co.il return HTTP 403 (Cloudflare) to this server; web.archive.org has no snapshot (2026-11-11)",
    "Ministry of Transport procedure 01/2013 s5.2 (car-importers.org.il/assets/files/14341592816852.pdf) requires importer parts price lists to show the consumer price WITHOUT VAT and state so - the file has no such statement; other IL importers publish VAT-inclusive lists, so the rule alone does not establish this file's basis",
]


def load_xlsx(path):
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
    out = collections.defaultdict(list)
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i < 2 or not r[0] or r[4] is None or str(r[0]).strip() == "-":
            continue
        try:
            out[str(r[0]).strip()].append(round(float(r[4]), 2))
        except (TypeError, ValueError):
            pass
    return out


def provenance(file_prices, stored_cost, run, src=os.path.basename(XLSX)):
    """Pure: the keys to merge for one part. The chosen source price is the file price that reproduces the stored cost."""
    chosen = next((p for p in file_prices if abs(round(p / 1.18, 2) - float(stored_cost)) < 0.011), None)
    if chosen is None:
        return None
    return {"source_file": src, "source_price_ils": chosen, "source_prices_in_file": sorted(set(file_prices)),
            "price_basis": "unverified", "candidate_cost_if_inclusive": round(chosen / 1.18, 2),
            "candidate_cost_if_exclusive": chosen, "price_basis_evidence": EVIDENCE, "provenance_run": run}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", metavar="RUN_ID")
    a = ap.parse_args()
    c = await asyncpg.connect(DB)
    if a.rollback:
        n = await c.execute("UPDATE parts_catalog SET specifications = specifications - $2::text[] "
                            "WHERE specifications->>'provenance_run' = $1", a.rollback, list(KEYS))
        print("rolled back:", n); return
    X = load_xlsx(XLSX)
    rows = await c.fetch("SELECT id, sku, oem_number, importer_price_ils FROM parts_catalog "
                         "WHERE sku LIKE 'CHEV-%' AND description ILIKE '%IL retail (incl VAT)%' ORDER BY sku")
    run = f"chev1384-{uuid.uuid4().hex[:8]}"; ok = []; bad = []
    for r in rows:
        oem = r["sku"][5:]
        fp = X.get(oem) or X.get(oem.lstrip("0")) or X.get(r["oem_number"] or "")
        pv = provenance(fp, r["importer_price_ils"], run) if fp and r["importer_price_ils"] is not None else None
        (ok if pv else bad).append((r["id"], pv, r["sku"]))
    print(f"rows={len(rows)} reproducible_from_file={len(ok)} not_reproducible={len(bad)} run={run}")
    for _, _, sku in bad[:10]: print("  unmatched:", sku)
    if a.apply:
        for i in range(0, len(ok), 500):
            async with c.transaction():
                for pid, pv, _ in ok[i:i + 500]:
                    await c.execute("UPDATE parts_catalog SET specifications = COALESCE(specifications,'{}'::jsonb) || $2::jsonb WHERE id=$1",
                                    pid, json.dumps(pv, ensure_ascii=False))
        print("applied", len(ok))
    await c.close()

if __name__ == "__main__":
    asyncio.run(main())
