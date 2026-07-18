"""
Manufacturer registry cleaner.

Goals:
- Populate passenger car manufacturers in car_brands.
- Populate truck/commercial manufacturers in truck_brands.
- Keep part/supplier brands out of both registries.
- Move misclassified entries from car_brands -> truck_brands.

Run:
    cd backend && python clean_manufacturers_registry.py

This module is also imported by db_update_agent to keep registries clean
after deploy/restart and periodic maintenance runs.
"""

from __future__ import annotations

import asyncio
import re
from typing import Dict, Iterable, Tuple

from sqlalchemy import select, text, func

from BACKEND_DATABASE_MODELS import (
    CarBrand,
    TruckBrand,
    PartsCatalog,
    Vehicle,
    async_session_factory,
)


PARTS_BRANDS = {
    "bosch",
    "brembo",
    "champion",
    "fram",
    "mann",
    "ngk",
    "valeo",
    "denso",
    "luk",
    "sachs",
    "delphi",
    "mahle",
    "hella",
    "mando",
    "trw",
    "aisin",
}

TRUCK_BRANDS = {
    "man": "MAN",
    "hino": "Hino",
    "scania": "Scania",
    "daf": "DAF",
    "iveco": "Iveco",
    "kenworth": "Kenworth",
    "peterbilt": "Peterbilt",
    "freightliner": "Freightliner",
    "mack": "Mack",
    "western star": "Western Star",
    "volvo trucks": "Volvo Trucks",
    "renault trucks": "Renault Trucks",
    "isuzu trucks": "Isuzu Trucks",
}

BASELINE_TRUCK_BRANDS = [
    "MAN",
    "Hino",
    "Scania",
    "DAF",
    "Iveco",
    "Kenworth",
    "Peterbilt",
    "Freightliner",
    "Mack",
    "Western Star",
    "Volvo Trucks",
    "Renault Trucks",
    "Isuzu Trucks",
]

# Keep these in DB for reference if needed, but never as active display manufacturers.
NON_DISPLAY_CAR_BRANDS = {
    "Stellantis",
    "General Motors",
    "Volkswagen Group",
    "BMW Group",
    "Toyota Group",
    "Honda Group",
    "Hyundai Motor Group",
    "Geely Group",
    "Tata Motors",
    "SAIC",
    "GAC",
    "GEN",
    "Renault Samsung",
    "Citroën",
    "JAECOO",
}

CANONICAL_OVERRIDES = {
    "Citroën": "Citroen",
    "JAECOO": "Jaecoo",
    "GEN": "Genesis",
}

CAR_LOGOS = {
    "Renault": "https://upload.wikimedia.org/wikipedia/commons/4/49/Renault_2021.svg",
    "Mercedes-Benz": "https://upload.wikimedia.org/wikipedia/commons/9/90/Mercedes-Logo.svg",
    "Chevrolet": "https://upload.wikimedia.org/wikipedia/commons/6/6e/Chevrolet-logo.png",
    "Hyundai": "https://upload.wikimedia.org/wikipedia/commons/4/44/Hyundai_Motor_Company_logo.svg",
    "Mitsubishi": "https://upload.wikimedia.org/wikipedia/commons/5/5a/Mitsubishi_logo.svg",
    "Genesis": "https://upload.wikimedia.org/wikipedia/commons/4/46/Genesis_Motors_logo.svg",
    "Suzuki": "https://upload.wikimedia.org/wikipedia/commons/1/12/Suzuki_logo_2.svg",
    "Porsche": "https://upload.wikimedia.org/wikipedia/en/8/8c/Porsche_logo.svg",
    "Smart": "https://upload.wikimedia.org/wikipedia/commons/a/ae/Smart_logo.svg",
    "ORA": "https://upload.wikimedia.org/wikipedia/commons/4/4e/GWM_logo.svg",
    "Jaecoo": "https://upload.wikimedia.org/wikipedia/commons/f/f0/Chery_logo.svg",
    "Citroen": "https://upload.wikimedia.org/wikipedia/commons/7/79/Citro%C3%ABn_2016_logo.svg",
    "Peugeot": "https://upload.wikimedia.org/wikipedia/commons/f/f5/Peugeot_2021_Logo.svg",
    "Toyota": "https://upload.wikimedia.org/wikipedia/commons/9/9d/Toyota_carlogo.svg",
    "Mazda": "https://upload.wikimedia.org/wikipedia/commons/1/18/Mazda_logo_with_emblem.svg",
    "Honda": "https://upload.wikimedia.org/wikipedia/commons/7/7b/Honda-logo.svg",
    "Nissan": "https://upload.wikimedia.org/wikipedia/commons/7/75/Nissan_2020_logo.svg",
    "Kia": "https://upload.wikimedia.org/wikipedia/commons/0/09/Kia_logo3.svg",
    "Volkswagen": "https://upload.wikimedia.org/wikipedia/commons/6/6d/Volkswagen_logo_2019.svg",
    "Audi": "https://upload.wikimedia.org/wikipedia/commons/9/92/Audi-Logo_2016.svg",
    "BMW": "https://upload.wikimedia.org/wikipedia/commons/4/44/BMW.svg",
}

TRUCK_LOGOS = {
    "MAN": "https://upload.wikimedia.org/wikipedia/commons/7/72/MAN_logo.svg",
    "Hino": "https://upload.wikimedia.org/wikipedia/commons/a/a6/Hino_logo.svg",
    "Scania": "https://upload.wikimedia.org/wikipedia/commons/0/0e/Scania_logo.svg",
    "DAF": "https://upload.wikimedia.org/wikipedia/commons/6/65/DAF_logo.svg",
    "Iveco": "https://upload.wikimedia.org/wikipedia/commons/7/74/Iveco_logo.svg",
    "Kenworth": "https://upload.wikimedia.org/wikipedia/commons/e/e0/Kenworth_logo.svg",
    "Peterbilt": "https://upload.wikimedia.org/wikipedia/commons/8/85/Peterbilt_logo.svg",
    "Freightliner": "https://upload.wikimedia.org/wikipedia/commons/1/1d/Freightliner_logo.svg",
    "Mack": "https://upload.wikimedia.org/wikipedia/commons/8/8b/Mack_Trucks_logo.svg",
    "Western Star": "https://upload.wikimedia.org/wikipedia/commons/2/2c/Western_Star_Trucks_logo.svg",
    "Volvo Trucks": "https://upload.wikimedia.org/wikipedia/commons/2/2b/Volvo-Wordmark.svg",
    "Renault Trucks": "https://upload.wikimedia.org/wikipedia/commons/4/49/Renault_2021.svg",
    "Isuzu Trucks": "https://upload.wikimedia.org/wikipedia/commons/5/57/Isuzu_logo.svg",
}

CANONICAL_CAR_BY_ALIAS = {
    "renault": "Renault",
    "mercedes": "Mercedes-Benz",
    "mercedes benz": "Mercedes-Benz",
    "chevrolet": "Chevrolet",
    "hyundai": "Hyundai",
    "mitsubishi": "Mitsubishi",
    "genesis": "Genesis",
    "suzuki": "Suzuki",
    "porsche": "Porsche",
    "smart": "Smart",
    "ora": "ORA",
    "jaecoo": "Jaecoo",
    "citroen": "Citroen",
    "peugeot": "Peugeot",
    "toyota": "Toyota",
    "mazda": "Mazda",
    "honda": "Honda",
    "nissan": "Nissan",
    "kia": "Kia",
    "volkswagen": "Volkswagen",
    "audi": "Audi",
    "bmw": "BMW",
    # Hebrew/dirty labels seen in this dataset
    "מרצדס": "Mercedes-Benz",
    "מרצדס חלפים": "Mercedes-Benz",
    "יונדאי": "Hyundai",
    "מיצובישי": "Mitsubishi",
    "ג נסיס": "Genesis",
    "ג'נסיס": "Genesis",
    "סוזוקי": "Suzuki",
    "סמארט": "Smart",
    "סיטרואן": "Citroen",
    "סיטרואן ספרד": "Citroen",
    "מותג": "",
}

REGION_BY_BRAND = {
    "Renault": "Europe",
    "Mercedes-Benz": "Europe",
    "Chevrolet": "America",
    "Hyundai": "Asia",
    "Mitsubishi": "Asia",
    "Genesis": "Asia",
    "Suzuki": "Asia",
    "Porsche": "Europe",
    "Smart": "Europe",
    "ORA": "Asia",
    "Jaecoo": "Asia",
    "Citroen": "Europe",
    "Peugeot": "Europe",
    "Toyota": "Asia",
    "Mazda": "Asia",
    "Honda": "Asia",
    "Nissan": "Asia",
    "Kia": "Asia",
    "Volkswagen": "Europe",
    "Audi": "Europe",
    "BMW": "Europe",
}


def norm(v: str) -> str:
    v = (v or "").strip().lower()
    v = re.sub(r"[^\w\u0590-\u05FF]+", " ", v)
    v = re.sub(r"\b(parts?|spare\s*parts?)\b", "", v)
    v = re.sub(r"\bחלפים\b", "", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v


def classify(raw: str) -> Tuple[str, str]:
    """Return (bucket, canonical). bucket in {car, truck, ignore}."""
    raw_clean = (raw or "").strip()
    n = norm(raw_clean)

    if not n:
        return "ignore", ""

    if n in PARTS_BRANDS:
        return "ignore", ""

    if n in TRUCK_BRANDS:
        return "truck", TRUCK_BRANDS[n]

    canonical = CANONICAL_CAR_BY_ALIAS.get(raw_clean) or CANONICAL_CAR_BY_ALIAS.get(n)
    if canonical is not None:
        if not canonical:
            return "ignore", ""
        return "car", canonical

    # Generic truck keyword catch
    if "truck" in n or "משאית" in n:
        return "truck", raw_clean

    # Default: treat unknown brand-like values as car manufacturers.
    return "car", raw_clean


async def _upsert_car(db, name: str, aliases: Iterable[str]):
    # Case-INSENSITIVE lookup: the unique index is ux_car_brands_name_ci_active
    # on lower(btrim(name)). A case-sensitive `name == 'Gms'` check missed the
    # existing 'gms' row and tried to INSERT a duplicate → UniqueViolationError
    # that failed sync_manufacturer_registries every cycle (fixed 2026-07-11).
    row = (await db.execute(
        select(CarBrand).where(func.lower(func.btrim(CarBrand.name)) == name.strip().lower())
        .order_by(CarBrand.is_active.desc()).limit(1)
    )).scalar_one_or_none()
    merged_aliases = sorted({a for a in aliases if a and a.strip() and a.strip() != name})

    if row is None:
        db.add(
            CarBrand(
                name=name,
                region=REGION_BY_BRAND.get(name),
                is_active=True,
                aliases=merged_aliases,
                logo_url=CAR_LOGOS.get(name),
            )
        )
        return "insert"

    row.is_active = True
    row.region = row.region or REGION_BY_BRAND.get(name)
    row.aliases = sorted(set((row.aliases or []) + merged_aliases))
    if not row.logo_url and CAR_LOGOS.get(name):
        row.logo_url = CAR_LOGOS[name]
    return "update"


async def _upsert_truck(db, name: str, aliases: Iterable[str]):
    # Case-insensitive lookup — same reason as _upsert_car (avoid duplicate INSERT
    # against a case-insensitive unique index).
    row = (await db.execute(
        select(TruckBrand).where(func.lower(func.btrim(TruckBrand.name)) == name.strip().lower())
        .order_by(TruckBrand.is_active.desc()).limit(1)
    )).scalar_one_or_none()
    merged_aliases = sorted({a for a in aliases if a and a.strip() and a.strip() != name})

    if row is None:
        db.add(
            TruckBrand(
                name=name,
                is_active=True,
                aliases=merged_aliases,
                logo_url=TRUCK_LOGOS.get(name),
            )
        )
        return "insert"

    row.is_active = True
    row.aliases = sorted(set((row.aliases or []) + merged_aliases))
    if not row.logo_url and TRUCK_LOGOS.get(name):
        row.logo_url = TRUCK_LOGOS[name]
    return "update"


async def sync_manufacturer_registries(db) -> Dict[str, object]:
    # 1) Gather all raw manufacturer values from vehicles + parts catalog.
    v_rows = (await db.execute(text("""
            SELECT DISTINCT manufacturer
            FROM vehicles
            WHERE manufacturer IS NOT NULL AND manufacturer <> ''
    """))).fetchall()

    p_rows = (await db.execute(text("""
            SELECT DISTINCT manufacturer
            FROM parts_catalog
            WHERE is_active = TRUE
              AND manufacturer IS NOT NULL
              AND manufacturer <> ''
    """))).fetchall()

    raw_values = sorted({(r[0] or "").strip() for r in (v_rows + p_rows) if (r[0] or "").strip()})

    # 2) Classify and build canonical alias maps.
    car_aliases: Dict[str, set] = {}
    truck_aliases: Dict[str, set] = {}
    ignored = []

    for raw in raw_values:
        bucket, canonical = classify(raw)
        if bucket == "ignore":
            ignored.append(raw)
            continue
        if bucket == "truck":
            truck_aliases.setdefault(canonical, set()).add(raw)
            continue
        car_aliases.setdefault(canonical, set()).add(raw)

    # 3) Move wrong entries from car_brands to truck_brands or deactivate parts-brands.
    moved_car_to_truck = 0
    deactivated_parts = 0
    merged_alias_rows = 0
    deactivated_non_display = 0

    existing_cars = (await db.execute(select(CarBrand))).scalars().all()
    for cb in existing_cars:
        if cb.name in NON_DISPLAY_CAR_BRANDS:
            if cb.is_active:
                cb.is_active = False
                deactivated_non_display += 1
            continue

        canonical = CANONICAL_OVERRIDES.get(cb.name)
        if canonical and canonical != cb.name:
            await _upsert_car(db, canonical, [cb.name, *((cb.aliases or []))])
            if cb.is_active:
                cb.is_active = False
            merged_alias_rows += 1
            continue

        n = norm(cb.name)
        if n in PARTS_BRANDS:
            cb.is_active = False
            deactivated_parts += 1
            continue
        if n in TRUCK_BRANDS:
            await _upsert_truck(db, TRUCK_BRANDS[n], [cb.name, *((cb.aliases or []))])
            cb.is_active = False
            moved_car_to_truck += 1

    # 4) Upsert cleaned registries.
    car_inserts = 0
    car_updates = 0
    for cname, aliases in car_aliases.items():
        action = await _upsert_car(db, cname, aliases)
        car_inserts += int(action == "insert")
        car_updates += int(action == "update")

    truck_inserts = 0
    truck_updates = 0
    for tname, aliases in truck_aliases.items():
        action = await _upsert_truck(db, tname, aliases)
        truck_inserts += int(action == "insert")
        truck_updates += int(action == "update")

    # Ensure a baseline truck registry exists even if current raw data has no truck rows.
    for tname in BASELINE_TRUCK_BRANDS:
        action = await _upsert_truck(db, tname, [])
        truck_inserts += int(action == "insert")
        truck_updates += int(action == "update")

    await db.commit()

    active_cars = (await db.execute(text("SELECT COUNT(*) FROM car_brands WHERE is_active = TRUE"))).scalar_one()
    active_trucks = (await db.execute(text("SELECT COUNT(*) FROM truck_brands WHERE is_active = TRUE"))).scalar_one()

    return {
        "raw_values": len(raw_values),
        "ignored": len(ignored),
        "car_inserts": car_inserts,
        "car_updates": car_updates,
        "truck_inserts": truck_inserts,
        "truck_updates": truck_updates,
        "moved_car_to_truck": moved_car_to_truck,
        "merged_alias_rows": merged_alias_rows,
        "deactivated_non_display": deactivated_non_display,
        "deactivated_parts_brands": deactivated_parts,
        "active_car_brands": active_cars,
        "active_truck_brands": active_trucks,
    }


async def main():
    async with async_session_factory() as db:
        report = await sync_manufacturer_registries(db)

        print("clean_manufacturers_registry completed")
        print("  raw_values:", report["raw_values"])
        print("  ignored:", report["ignored"])
        print("  car inserts/updates:", report["car_inserts"], report["car_updates"])
        print("  truck inserts/updates:", report["truck_inserts"], report["truck_updates"])
        print("  moved car->truck:", report["moved_car_to_truck"])
        print("  deactivated parts-brands:", report["deactivated_parts_brands"])
        print("  active car_brands:", report["active_car_brands"])
        print("  active truck_brands:", report["active_truck_brands"])


if __name__ == "__main__":
    asyncio.run(main())
