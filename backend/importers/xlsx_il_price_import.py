#!/usr/bin/env python3
"""
Universal IL importer price import from xlsx catalog exports.
Handles both column formats found in our catalog xlsx files.
Pricing policy: treat Importer Price (or Base Price when no importer col) as excl-VAT cost.
  importer_price_ils = cost
  max_price_ils      = cost * 1.17
  base_price         = cost * 1.45
Usage:
  python3 xlsx_il_price_import.py --file /app/uploads/honda_parts_catalog.xlsx --brand Honda
  python3 xlsx_il_price_import.py --file /app/uploads/volvo_parts_catalog.xlsx --brand Volvo
"""
import asyncio, os, sys, time, json, argparse
import asyncpg

try:
    import openpyxl
except ImportError:
    print("pip install openpyxl"); sys.exit(1)

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
VAT = 0.18

def load_xlsx(path, brand):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    headers = [str(h).strip() if h else "" for h in rows[0]]
    hl = [h.lower() for h in headers]

    # Find column indices
    def idx(*names):
        for n in names:
            for i, h in enumerate(hl):
                if n in h:
                    return i
        return -1

    i_sku   = idx("sku")
    i_oem   = idx("oem number", "oem_number", "oem")
    i_imp   = idx("importer price", "importer_price")
    i_base  = idx("base price", "base_price")
    i_name  = idx("name he", "name_he", "name (hebrew)")
    i_eng   = idx("name")

    print(f"Columns: SKU={i_sku} OEM={i_oem} IMP={i_imp} BASE={i_base} NAME_HE={i_name}")

    parts = []
    for row in rows[1:]:
        def cell(i):
            return row[i] if i >= 0 and i < len(row) and row[i] is not None else None

        sku  = str(cell(i_sku) or "").strip()
        oem  = str(cell(i_oem) or sku).strip() or sku
        imp  = cell(i_imp)
        base = cell(i_base)
        name = str(cell(i_name) or cell(i_eng) or "").strip()

        # Determine cost (importer_price_ils = excl. VAT)
        imp_val  = None
        base_val = None
        if imp is not None:
            try: imp_val = float(imp)
            except: pass
        if base is not None:
            try: base_val = float(base)
            except: pass

        # If base and importer differ by ~18% VAT, base is the excl-VAT cost
        cost = None
        consumer = None
        if imp_val and base_val and imp_val > 0 and base_val > 0:
            ratio = imp_val / base_val
            if 1.14 < ratio < 1.22:
                cost = base_val      # excl. VAT cost
                consumer = imp_val   # incl. VAT consumer price
            else:
                cost = imp_val or base_val
        else:
            cost = imp_val or base_val

        if not oem or not cost or cost <= 0:
            continue

        parts.append({"oem": oem, "sku": sku, "name": name, "cost": cost, "consumer": consumer})

    wb.close()
    print(f"Parsed {len(parts):,} parts from {os.path.basename(path)}")
    return parts


async def run(brand, xlsx_path):
    parts = load_xlsx(xlsx_path, brand)
    if not parts:
        print("No parts parsed"); return

    conn = await asyncpg.connect(DB)
    t0 = time.monotonic()
    spec = json.dumps({"importer": f"{brand} IL official", "source": os.path.basename(xlsx_path),
                       "vat_rate": VAT, "vat_included": False}, ensure_ascii=False)

    updated = inserted = skipped = 0
    for p in parts:
        cost    = round(p["cost"], 2)
        # If we have a consumer price from the xlsx, use it as max_price; else derive from cost
        retail  = round(p["consumer"], 2) if p.get("consumer") else round(cost * (1 + VAT), 2)
        selling = round(cost * 1.45, 2)
        oem = p["oem"]

        res = await conn.execute("""
            UPDATE parts_catalog SET
                importer_price_ils = $1,
                max_price_ils      = $2,
                base_price         = $3,
                specifications     = COALESCE(specifications,'{}')::jsonb || $4::jsonb,
                updated_at         = NOW()
            WHERE oem_number = $5 AND manufacturer = $6 AND is_active = true
        """, cost, retail, selling, spec, oem, brand)
        n = int(res.split()[-1])
        if n > 0:
            updated += n
        else:
            # Try SKU as OEM fallback
            if p["sku"] and p["sku"] != oem:
                res2 = await conn.execute("""
                    UPDATE parts_catalog SET
                        importer_price_ils=$1, max_price_ils=$2, base_price=$3,
                        specifications=COALESCE(specifications,'{}')::jsonb||$4::jsonb, updated_at=NOW()
                    WHERE oem_number=$5 AND manufacturer=$6 AND is_active=true
                """, cost, retail, selling, spec, p["sku"], brand)
                n2 = int(res2.split()[-1])
                if n2 > 0:
                    updated += n2
                    continue
            # Insert new
            try:
                await conn.execute("""
                    INSERT INTO parts_catalog(
                        id, sku, oem_number, name, name_he, manufacturer, category,
                        base_price, importer_price_ils, max_price_ils, min_price_ils,
                        part_type, part_condition, is_active,
                        needs_oem_lookup, master_enriched, specifications,
                        created_at, updated_at
                    ) VALUES(
                        gen_random_uuid(),$1,$2,$3,$3,$4,'accessories',
                        $5,$6,$7,$7,
                        'Original','new',true,
                        true,false,$8::jsonb,
                        NOW(),NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        importer_price_ils=EXCLUDED.importer_price_ils,
                        max_price_ils=EXCLUDED.max_price_ils,
                        base_price=EXCLUDED.base_price,
                        specifications=EXCLUDED.specifications,
                        updated_at=NOW()
                """, oem, oem, p["name"], brand, selling, cost, retail, spec)
                inserted += 1
            except Exception:
                skipped += 1

    elapsed = time.monotonic() - t0
    pct = 100 * updated // max(len(parts), 1)
    print(f"[{brand}] Done {elapsed:.0f}s: updated={updated:,} inserted={inserted:,} skipped={skipped:,} match={pct}%")

    r = await conn.fetchrow(
        "SELECT COUNT(*) t, COUNT(*) FILTER (WHERE importer_price_ils>0) p "
        "FROM parts_catalog WHERE manufacturer=$1 AND is_active=true", brand)
    print(f"[{brand}] Coverage: {r['p']:,}/{r['t']:,} ({100*r['p']//(r['t'] or 1)}%)")
    await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--brand", required=True)
    args = ap.parse_args()
    asyncio.run(run(args.brand, args.file))
