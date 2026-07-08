#!/usr/bin/env python3
"""
Script: car_parts_ie_import_generic.py
Purpose: Import parts extracted from car-parts.ie for ANY car brand.

Handles the flat JSON exported by the in-browser crawler (window._cpieData):
  {
    "source": "car-parts.ie",
    "maker": "volkswagen",
    "maker_id": 121,
    "manufacturer": "Volkswagen",
    "model_text": "Golf IV (1J1) (08.1997 - 06.2006)",
    "engine_text": "1.4 16V (55 KW)",
    "car_id": 1234,
    "parts": [
      {
        "name": "Brake disc",
        "product_url": "https://...",
        "inferred_sku": "7701207718",
        "price_eur": 12.99,
        "product_id": "12345",
        "category": "brake-discs"
      }, ...
    ]
  }

Or the legacy flat-array format (window._opelParts / window._cpieResults[brand]):
  [{ name, product_url, inferred_sku, price_eur, product_id, category }, ...]

Usage:
  python3 car_parts_ie_import_generic.py --brand volkswagen --file /tmp/vw_cpie.json
  python3 car_parts_ie_import_generic.py --brand toyota --file /tmp/toyota_cpie.json \\
      --model "Corolla (E12) (01.2002 - 12.2007)" --engine "1.4 VVT-i (71 KW)"
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import re
import signal
from datetime import datetime
from pathlib import Path

import asyncpg

# ── Graceful shutdown support ─────────────────────────────────────────────────
_shutdown_flag = False

def _handle_sigterm(signum, frame):
    global _shutdown_flag
    _shutdown_flag = True
    print("[car_parts_ie_import] SIGTERM received — will checkpoint after current part and exit")


DB_DSN = (
    "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43"
    "@postgres_catalog:5432/autospare"
)
SUPPLIER_NAME = "Car-Parts.ie"
SUPPLIER_URL = "https://www.car-parts.ie"

# Known manufacturer_ids for common brands (must match car_brands.id)
MANUFACTURER_IDS: dict[str, str] = {
    "toyota":       "01954786-65c7-4ff4-a6ad-4836b31da9f4",
    "honda":        "6034f4f4-6dfa-4c88-998c-e62f45956ea9",
    "nissan":       "98ca408f-19b6-48f9-a756-18b2387b0b90",
    "ford":         "73fc77ef-5414-4270-9476-2444d8b7eb41",
    "bmw":          "caa6ba39-02aa-4394-969d-a15f3f19104c",
    "hyundai":      "eb828e88-d955-45a8-8b53-3b3677238b5a",
    "kia":          "626947bf-be3f-4dd1-a52e-fbcff8168cfc",
    "mazda":        "fde0f2dc-c6fb-4ab6-b699-765044fbc073",
    "subaru":       "88a04aee-d7d5-45ff-8308-4c6b50c67c0e",
    "mitsubishi":   "0e31fccd-8abf-4eb9-ac0c-5584f626a20f",
    "volvo":        "2714a52d-fb43-4af5-bf96-6e677f0f8a25",
    "jaguar":       "fde0f2dc-c6fb-4ab6-b699-765044fbc073",
    "land rover":   "7f060acf-2382-42e1-8413-f9b045cb0836",
    "landrover":    "7f060acf-2382-42e1-8413-f9b045cb0836",
    "porsche":      "6129ed2e-3f88-4025-9f66-5bf8ab97a8c1",
    "mercedes-benz":"c8ae1952-9e77-4acb-bf79-88271cf9bbce",
    "mercedes":     "c8ae1952-9e77-4acb-bf79-88271cf9bbce",
    "volkswagen":   "04877cea-0889-4b57-978a-cff0a8f1ed25",
    "vw":           "04877cea-0889-4b57-978a-cff0a8f1ed25",
    "opel":         "86106424-41ba-434b-b107-4b6db23523b7",
    "audi":         "4a718e3c-5b47-478d-9c62-0b6b5135593e",
    "renault":      "d193f27e-f0c4-4de8-b7a6-8ecb24589c6d",
    "peugeot":      "2b6a2687-8227-4307-9c88-500545fc96ca",
    "citroen":      "c9fba999-3265-4d99-99ec-3a959ac0ac66",
    "citroën":      "c9fba999-3265-4d99-99ec-3a959ac0ac66",
    "skoda":        "e062ba07-930c-489f-b43e-48bf90a42d11",
    "fiat":         "471408c0-527b-49d3-a964-fb108916d586",
    "chevrolet":    "a0b9a4d9-6334-40c3-8e2a-f84f9fdd11a1",
    "suzuki":       "f2f6913a-7ea6-4a99-b65a-c9b1d548e38c",
    # car-parts.ie brands
    "rover":        "9e3b7475-c104-4f75-a006-26c5858b9c37",
    "saab":         "c21a289d-5b3e-411f-afbb-5f7a4d022a88",
    "daewoo":       "3b2a1c64-76ef-4960-ab3d-8b2337ea3409",
    "chrysler":     "e7b5ae95-649c-4da8-81ad-cc565a86582a",
    "mg":           "341be223-5852-4f29-bd96-085ef2c5d07b",
    "haval":        "1f0da611-bb17-4749-91bd-90c7a6db54fe",
    "ssangyong":    "588b0288-fb17-499e-83a8-750e3be2d318",
}

# category slug → system category id
CATEGORY_SLUG_MAP: dict[str, str] = {
    "brake-discs": "brakes", "brake-pads": "brakes", "brake-drums": "brakes",
    "brake-calipers": "brakes", "brake-hoses": "brakes", "brake-master-cylinder": "brakes",
    "wheel-cylinders": "brakes", "handbrake-cables": "brakes",
    "shock-absorbers": "suspension-steering", "springs": "suspension-steering",
    "control-arms": "suspension-steering", "ball-joints": "suspension-steering",
    "tie-rod-ends": "suspension-steering", "steering-rack": "suspension-steering",
    "anti-roll-bar": "suspension-steering", "suspension-bushes": "suspension-steering",
    "wheel-bearings": "wheels-bearings", "wheel-hub": "wheels-bearings",
    "drive-shafts": "clutch-drivetrain", "cv-joints": "clutch-drivetrain",
    "clutch-kit": "clutch-drivetrain", "flywheel": "clutch-drivetrain",
    "gearbox-oil": "gearbox", "manual-gearbox": "gearbox", "automatic-gearbox": "gearbox",
    "engine-oil": "fluids", "coolant": "fluids", "brake-fluid": "fluids",
    "oil-filter": "filters", "air-filter": "filters", "fuel-filter": "filters",
    "pollen-filter": "filters", "cabin-filter": "filters",
    "alternator": "electrical-sensors", "starter-motor": "electrical-sensors",
    "sensors": "electrical-sensors", "lambda-sensor": "electrical-sensors",
    "abs-sensor": "electrical-sensors", "camshaft-sensor": "electrical-sensors",
    "battery": "electrical-sensors",
    "radiator": "cooling", "thermostat": "cooling", "water-pump": "cooling",
    "cooling-fan": "cooling", "coolant-pipe": "cooling",
    "control-valve-coolant": "cooling", "heater-valve": "cooling",
    "fuel-pump": "fuel-air", "injectors": "fuel-air", "carburettor": "fuel-air",
    "intake-manifold": "fuel-air", "throttle-body": "fuel-air",
    "catalytic-converter": "exhaust", "exhaust-pipe": "exhaust",
    "muffler": "exhaust", "dpf": "exhaust", "egr-valve": "exhaust",
    "timing-belt": "engine", "timing-chain": "engine", "camshaft": "engine",
    "crankshaft": "engine", "pistons": "engine", "engine-mount": "engine",
    "cylinder-head-gasket": "engine", "rocker-cover-gasket": "engine",
    "spark-plug": "engine", "glow-plugs": "engine",
    "headlights": "lighting", "tail-lights": "lighting", "fog-lights": "lighting",
    "bulbs": "lighting", "indicators": "lighting",
    "wiper-blades": "wipers-washers", "wiper-motor": "wipers-washers",
    "washer-pump": "wipers-washers", "washer-reservoir": "wipers-washers",
    "bonnet": "body-exterior", "bumper": "body-exterior", "wing": "body-exterior",
    "door": "body-exterior", "boot-lid": "body-exterior", "mirror": "body-exterior",
    "windscreen": "body-exterior", "window-regulator": "body-exterior",
    "air-conditioning": "air-conditioning-heating", "ac-compressor": "air-conditioning-heating",
    "heater-matrix": "air-conditioning-heating",
    "seat": "interior-comfort", "interior-trim": "interior-comfort",
}


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _extract_years(text: str) -> tuple[int | None, int | None]:
    m = re.search(r"(\d{2})\.(\d{4})\s*[-–]\s*(?:(\d{2})\.(\d{4}|\.\.\.)|present)", text, re.I)
    if m:
        y1 = int(m.group(2))
        y2 = int(m.group(4)) if m.group(4) and m.group(4).isdigit() else 2099
        return y1, y2
    years = [int(y) for y in re.findall(r"(19\d{2}|20\d{2})", text)]
    if len(years) >= 2:
        return years[0], years[1]
    if years:
        return years[0], years[0]
    return None, None


def _resolve_manufacturer_id(brand_key: str) -> str:
    mid = MANUFACTURER_IDS.get(brand_key.lower())
    if mid:
        return mid
    # Generate stable UUID from brand name
    return str(
        __import__("uuid").UUID(hashlib.md5(brand_key.lower().encode()).hexdigest())
    )


def _map_category(slug: str) -> str:
    if not slug:
        return "service-general"
    slug_clean = slug.lower().replace("_", "-")
    if slug_clean in CATEGORY_SLUG_MAP:
        return CATEGORY_SLUG_MAP[slug_clean]
    # Try prefix match
    for key, cat in CATEGORY_SLUG_MAP.items():
        if slug_clean.startswith(key[:6]):
            return cat
    # Try word in slug
    for word, cat in [
        ("brake", "brakes"), ("shock", "suspension-steering"), ("spring", "suspension-steering"),
        ("steering", "suspension-steering"), ("bearing", "wheels-bearings"),
        ("clutch", "clutch-drivetrain"), ("gear", "gearbox"), ("filter", "filters"),
        ("engine", "engine"), ("exhaust", "exhaust"), ("fuel", "fuel-air"),
        ("cool", "cooling"), ("electric", "electrical-sensors"), ("sensor", "electrical-sensors"),
        ("light", "lighting"), ("wiper", "wipers-washers"), ("body", "body-exterior"),
        ("air-con", "air-conditioning-heating"), ("interior", "interior-comfort"),
    ]:
        if word in slug_clean:
            return cat
    return "service-general"


async def _ensure_supplier(conn: asyncpg.Connection) -> str:
    row = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if row:
        return str(row["id"])
    sid = str(__import__("uuid").uuid4())
    await conn.execute(
        "INSERT INTO suppliers(id,name,country,website,reliability_score,is_active,"
        "priority,supports_express,rate_limit_per_minute,is_manufacturer,created_at,updated_at)"
        " VALUES($1,$2,'IE',$3,0.85,TRUE,5,FALSE,30,FALSE,NOW(),NOW())",
        sid, SUPPLIER_NAME, SUPPLIER_URL,
    )
    return sid


def _parse_vehicle_slug(slug: str) -> tuple[str, str, int | None, int | None]:
    """
    Parse a car-parts.ie vehicle slug into (manufacturer, model, year_from, year_to).
    Example: "mercedes-benz/vito-bus-w639" → ("Mercedes-Benz", "Vito W639", None, None)
             "honda/jazz-iv-gk"            → ("Honda", "Jazz IV GK", None, None)
    """
    if not slug:
        return ("", "", None, None)
    parts = slug.strip("/").split("/")
    brand_slug = parts[0] if parts else ""
    model_slug = parts[1] if len(parts) > 1 else ""
    # Convert brand slug to title
    brand_map = {
        "mercedes-benz": "Mercedes-Benz", "land-rover": "Land Rover",
        "alfa-romeo": "Alfa Romeo", "aston-martin": "Aston Martin",
    }
    manufacturer = brand_map.get(brand_slug, brand_slug.replace("-", " ").title())
    # Convert model slug to readable name
    model_clean = model_slug.replace("-", " ").title()
    # Extract years from slug if present (e.g. "transit-mk7-2006-2014")
    year_match = re.findall(r"\b(19|20)\d{2}\b", model_slug)
    year_from = int(year_match[0]) if year_match else None
    year_to   = int(year_match[-1]) if len(year_match) > 1 else year_from
    return (manufacturer, model_clean, year_from, year_to)


async def import_file(
    path: Path,
    brand_arg: str | None = None,
    model_arg: str | None = None,
    engine_arg: str | None = None,
    vehicle_slug: str | None = None,
    start_from: int = 0,
) -> dict:
    raw = json.loads(path.read_text())

    # Support two formats: wrapped dict or bare list
    if isinstance(raw, list):
        parts_list = raw
        manufacturer = (brand_arg or "").title() or "Unknown"
        maker_key = (brand_arg or "unknown").lower()
        model_text = model_arg or ""
        engine_text = engine_arg or ""
    else:
        parts_list = raw.get("parts") or raw.get("products") or []
        manufacturer = _clean(raw.get("manufacturer") or brand_arg or raw.get("maker") or "Unknown").title()
        maker_key = (raw.get("maker") or brand_arg or manufacturer).lower()
        model_text = model_arg or _clean(raw.get("model_text") or raw.get("model") or "")
        engine_text = engine_arg or _clean(raw.get("engine_text") or raw.get("engine") or "")
        # Pick up vehicle_slug from JSON if not passed as arg
        vehicle_slug = vehicle_slug or raw.get("vehicle_slug", "")

    manufacturer_id = _resolve_manufacturer_id(maker_key)
    sku_prefix = re.sub(r"[^A-Z0-9]", "", manufacturer.upper())[:5] or "CPIE"
    model_name = re.sub(r"\(\d{2}\.\d{4}.*?\)", "", model_text).strip()
    year_from, year_to = _extract_years(f"{model_text} {engine_text}")

    # If no model_text but we have a vehicle slug, parse it for fitment
    slug_manufacturer, slug_model, slug_year_from, slug_year_to = _parse_vehicle_slug(vehicle_slug or "")
    if not model_name and slug_model:
        model_name = slug_model
        manufacturer = slug_manufacturer or manufacturer
        if not year_from and slug_year_from:
            year_from = slug_year_from
            year_to = slug_year_to

    conn = await asyncpg.connect(DB_DSN)
    try:
        supplier_id = await _ensure_supplier(conn)
        inserted = updated = fitment_rows = supplier_rows = 0
        checkpoint_at: int | None = None

        if start_from > 0:
            print(f"[car_parts_ie_import] Resuming from index {start_from} (skipping {start_from} parts)")
            parts_list = parts_list[start_from:]

        for idx, product in enumerate(parts_list):
            if _shutdown_flag:
                checkpoint_at = start_from + idx
                print(f"[car_parts_ie_import] Checkpointing at index {checkpoint_at}")
                break

            sku_raw = _clean(product.get("inferred_sku") or product.get("sku") or "")
            if not sku_raw:
                url = _clean(product.get("product_url") or "")
                sku_raw = "CP-" + hashlib.sha1(url.encode()).hexdigest()[:10].upper()

            sku = f"{sku_prefix}-{sku_raw}"
            name = _clean(product.get("name") or sku_raw)
            category = _map_category(product.get("category") or "")
            url = _clean(product.get("product_url") or product.get("source_url") or "")
            price_eur = product.get("price_eur") or 0.0
            brand_part = _clean(product.get("brand") or "")
            description_text = _clean(product.get("description") or "")
            image_url = _clean(product.get("image_url") or "")
            in_stock = bool(product.get("in_stock", True))
            # EUR→ILS: 1 EUR ≈ 3.9 ILS; treat as reference market price (incl. VAT equiv)
            # cost = price_ils / 1.18, base_price = cost * 1.45 (CLAUDE.md: 45% margin)
            price_ils = round(float(price_eur) * 3.9, 2) if price_eur else None
            cost_ils = round(price_ils / 1.18, 2) if price_ils else None
            base_price_ils = round(cost_ils * 1.45, 2) if cost_ils else None

            # Build compatible_vehicles from top-level model OR per-part fitment array
            compatible_vehicles = []
            # Use year_from=1990 as fallback when slug has no year info — manufacturer+model is enough for filtering
            eff_year_from = year_from or 1990
            eff_year_to = year_to or 2030
            if model_name:
                compatible_vehicles.append({
                    "manufacturer": manufacturer,
                    "model": model_name,
                    "year_from": eff_year_from,
                    "year_to": eff_year_to,
                    "engine": engine_text or None,
                })
            elif product.get("fitment"):
                for fit in (product["fitment"] if isinstance(product["fitment"], list) else []):
                    fit_maker = _clean(fit.get("manufacturer") or manufacturer)
                    fit_model = _clean(fit.get("model") or "")
                    fit_yf = fit.get("year_from") or year_from
                    fit_yt = fit.get("year_to") or year_to or fit_yf
                    if fit_model:
                        compatible_vehicles.append({
                            "manufacturer": fit_maker,
                            "model": fit_model,
                            "year_from": fit_yf,
                            "year_to": fit_yt,
                            "engine": _clean(fit.get("engine") or engine_text or ""),
                        })

            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO parts_catalog(
                        id, sku, oem_number, name, name_he,
                        manufacturer, manufacturer_id,
                        category, description, specifications, compatible_vehicles,
                        part_type, part_condition, aftermarket_tier,
                        importer_price_ils, base_price, online_price_ils, min_price_ils, max_price_ils,
                        is_safety_critical, needs_oem_lookup, master_enriched,
                        is_active, created_at, updated_at
                    ) VALUES(
                        gen_random_uuid(), $1, $2, $3, $3,
                        $4, $5::uuid,
                        $6, $12, $7::jsonb, $8::jsonb,
                        'aftermarket', 'new', 'OE_equivalent',
                        $10, $11, $9, $9, $9,
                        FALSE, FALSE, FALSE,
                        TRUE, NOW(), NOW()
                    )
                    ON CONFLICT (sku) DO UPDATE SET
                        name            = EXCLUDED.name,
                        category        = EXCLUDED.category,
                        compatible_vehicles = COALESCE(
                            parts_catalog.compatible_vehicles, EXCLUDED.compatible_vehicles
                        ),
                        importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0
                            THEN EXCLUDED.importer_price_ils
                            ELSE parts_catalog.importer_price_ils END,
                        base_price = CASE WHEN EXCLUDED.base_price > 0
                            THEN EXCLUDED.base_price
                            ELSE parts_catalog.base_price END,
                        updated_at = NOW()
                    RETURNING id, (xmax = 0) AS is_insert
                    """,
                    sku, sku_raw, name,
                    manufacturer, manufacturer_id,
                    category,
                    json.dumps({
                        "source": SUPPLIER_NAME,
                        "product_url": url,
                        "part_brand": brand_part,
                        "description": description_text,
                        "image_url": image_url,
                        "in_stock": in_stock,
                    }),
                    json.dumps(compatible_vehicles),
                    price_ils, cost_ils, base_price_ils,
                    description_text or None,
                )
            except Exception as e:
                print(f"  [warn] skip {sku}: {e}", flush=True)
                continue

            part_id = str(row["id"])
            if row["is_insert"]:
                inserted += 1
            else:
                updated += 1

            for cv in compatible_vehicles:
                try:
                    fit_maker = cv["manufacturer"]
                    fit_mid = _resolve_manufacturer_id(fit_maker.lower())
                    await conn.execute(
                        """
                        INSERT INTO part_vehicle_fitment(
                            id, part_id, manufacturer, manufacturer_id,
                            model, year_from, year_to, engine_type, notes,
                            created_at, updated_at
                        ) VALUES(
                            gen_random_uuid(), $1::uuid, $2, $3::uuid,
                            $4, $5, $6, $7, NULL, NOW(), NOW()
                        )
                        ON CONFLICT (part_id, manufacturer, model, year_from)
                        DO UPDATE SET year_to=EXCLUDED.year_to, engine_type=EXCLUDED.engine_type, updated_at=NOW()
                        """,
                        part_id, fit_maker, fit_mid,
                        cv["model"], cv.get("year_from"), cv.get("year_to") or cv.get("year_from"),
                        cv.get("engine") or None,
                    )
                    fitment_rows += 1
                except Exception:
                    pass

            try:
                await conn.execute(
                    """
                    INSERT INTO supplier_parts(
                        id, supplier_id, part_id, supplier_sku,
                        price_usd, price_ils, availability, is_available,
                        estimated_delivery_days, warranty_months,
                        supplier_url, created_at, updated_at
                    ) VALUES(
                        gen_random_uuid(), $1::uuid, $2::uuid, $3,
                        0.0, $4, 'in_stock', TRUE, 10, 12, $5, NOW(), NOW()
                    )
                    ON CONFLICT (supplier_id, supplier_sku)
                    DO UPDATE SET
                        price_ils=EXCLUDED.price_ils,
                        is_available=EXCLUDED.is_available,
                        supplier_url=EXCLUDED.supplier_url,
                        updated_at=NOW()
                    """,
                    supplier_id, part_id, sku_raw, price_ils, url,
                )
                supplier_rows += 1
            except Exception:
                pass

        return {
            "manufacturer": manufacturer,
            "model": model_name,
            "parts_scanned": len(parts_list),
            "parts_inserted": inserted,
            "parts_updated": updated,
            "fitment_rows": fitment_rows,
            "supplier_rows": supplier_rows,
            "checkpoint_at": checkpoint_at,
        }
    finally:
        await conn.close()


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_sigterm)

    ap = argparse.ArgumentParser(description="Import car-parts.ie parts for any brand")
    ap.add_argument("--brand", required=True,
                    help="Brand slug: toyota, volkswagen, ford, bmw, etc.")
    ap.add_argument("--file", required=True, help="Path to JSON exported by crawler")
    ap.add_argument("--model", default="", help="Model text (e.g. 'Golf IV (1J1) (08.1997 - 06.2006)')")
    ap.add_argument("--engine", default="", help="Engine text (e.g. '1.4 16V (55 KW)')")
    ap.add_argument("--vehicle-slug", default="",
                    help="Vehicle slug from harvester (e.g. 'mercedes-benz/vito-bus-w639') — used for fitment")
    ap.add_argument("--resume-from", type=int, default=0,
                    help="Resume from this part index (overrides checkpoint file)")
    args = ap.parse_args()

    brand = args.brand.lower()
    checkpoint_file = Path(args.file).with_suffix(".checkpoint.json")

    start_from = args.resume_from
    if start_from == 0 and checkpoint_file.exists():
        try:
            cp = json.loads(checkpoint_file.read_text())
            if cp.get("file") == args.file and cp.get("next_index", 0) > 0:
                start_from = cp["next_index"]
                print(f"[car_parts_ie_import] Auto-resuming from checkpoint index {start_from}")
        except Exception:
            pass

    # Serialize concurrent harvester-triggered runs — without this, several vehicles
    # of the same brand finishing close together spawn overlapping processes that
    # lock-contend upserting the same shared SKUs in parts_catalog for many minutes.
    lock_fp = open("/tmp/car_parts_ie_import.lock", "w")
    fcntl.flock(lock_fp, fcntl.LOCK_EX)
    try:
        report = asyncio.run(import_file(Path(args.file), brand, args.model, args.engine,
                                         vehicle_slug=args.vehicle_slug,
                                         start_from=start_from))
    finally:
        fcntl.flock(lock_fp, fcntl.LOCK_UN)
        lock_fp.close()

    if report.get("checkpoint_at") is not None:
        checkpoint_file.write_text(json.dumps({
            "file": args.file,
            "brand": brand,
            "next_index": report["checkpoint_at"],
            "saved_at": datetime.utcnow().isoformat() + "Z",
        }))
        print(f"[car_parts_ie_import] Checkpointed at index {report['checkpoint_at']} → {checkpoint_file}")
    else:
        if checkpoint_file.exists():
            checkpoint_file.unlink()

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
