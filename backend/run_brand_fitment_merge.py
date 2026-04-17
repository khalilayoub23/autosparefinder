from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any, Dict, List

from sqlalchemy import text

from BACKEND_DATABASE_MODELS import async_session_factory
from manufacturer_normalization import (
    canonicalize_vehicle_model_for_manufacturer,
    normalize_manufacturer_name,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a brand-scoped fitment merge from part_vehicle_fitment into parts_catalog.compatible_vehicles."
    )
    parser.add_argument("--brand", required=True, help="Canonical manufacturer to merge, e.g. Suzuki")
    return parser.parse_args()


async def _load_aliases(db, brand: str) -> List[str]:
    row = (
        await db.execute(
            text(
                """
                SELECT name, name_he, aliases
                FROM car_brands
                WHERE LOWER(TRIM(name)) = LOWER(TRIM(:brand))
                   OR LOWER(TRIM(name_he)) = LOWER(TRIM(:brand))
                   OR EXISTS (
                       SELECT 1
                       FROM unnest(COALESCE(aliases, ARRAY[]::text[])) a
                       WHERE LOWER(TRIM(a)) = LOWER(TRIM(:brand))
                   )
                LIMIT 1
                """
            ),
            {"brand": brand},
        )
    ).fetchone()

    aliases: List[str] = [brand]
    if row:
        aliases.extend([str(row[0] or ""), str(row[1] or "")])
        aliases.extend([str(a or "") for a in (row[2] or [])])

    # Ensure normalized canonical is also included.
    aliases.append(normalize_manufacturer_name(brand, brand) or brand)

    deduped: List[str] = []
    seen = set()
    for value in aliases:
        token = str(value or "").strip()
        if not token:
            continue
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(token)
    return deduped


async def run_brand_merge(brand: str) -> Dict[str, Any]:
    t0 = time.monotonic()
    async with async_session_factory() as db:
        aliases = await _load_aliases(db, brand)

        rows = (
            await db.execute(
                text(
                    """
                    SELECT
                        pc.id,
                        pc.compatible_vehicles,
                        pvf.manufacturer,
                        pvf.model,
                        pvf.year_from,
                        pvf.year_to,
                        pvf.engine_type
                    FROM parts_catalog pc
                    JOIN part_vehicle_fitment pvf
                      ON pvf.part_id = pc.id
                    WHERE pc.is_active = TRUE
                      AND LOWER(TRIM(pc.manufacturer)) = LOWER(TRIM(:brand))
                      AND (
                            LOWER(TRIM(pvf.manufacturer)) = ANY(:aliases)
                         OR LOWER(TRIM(pc.manufacturer)) = ANY(:aliases)
                      )
                      AND pvf.manufacturer IS NOT NULL
                      AND TRIM(pvf.manufacturer) <> ''
                      AND pvf.model IS NOT NULL
                      AND TRIM(pvf.model) <> ''
                    """
                ),
                {
                    "brand": brand,
                    "aliases": [a.casefold() for a in aliases],
                },
            )
        ).fetchall()

        part_existing: Dict[str, List[Dict[str, Any]]] = {}
        part_fitments: Dict[str, List[Dict[str, Any]]] = {}
        scanned_rows = 0

        for part_id, compat, manufacturer, model, year_from, year_to, engine_type in rows:
            scanned_rows += 1
            pid = str(part_id)
            if pid not in part_existing:
                part_existing[pid] = list(compat or []) if isinstance(compat, list) else []

            canonical_manufacturer = normalize_manufacturer_name(str(manufacturer or ""), str(manufacturer or ""))
            canonical_model = canonicalize_vehicle_model_for_manufacturer(canonical_manufacturer, model)
            if not canonical_manufacturer or not canonical_model:
                continue

            fitment: Dict[str, Any] = {
                "manufacturer": canonical_manufacturer,
                "model": canonical_model,
                "source": "part_vehicle_fitment",
            }

            if engine_type:
                fitment["engine"] = str(engine_type).strip()[:50]

            try:
                yf = int(year_from or 0)
            except Exception:
                yf = 0
            try:
                yt = int(year_to or 0)
            except Exception:
                yt = 0

            if yf and not yt:
                yt = yf
            if yf and yt and 1990 <= yf <= yt <= 2027:
                fitment["year_from"] = yf
                fitment["year_to"] = yt

            part_fitments.setdefault(pid, []).append(fitment)

        updated_parts = 0
        merged_fitment_rows = 0

        for part_id, entries in part_fitments.items():
            preserved = [
                item
                for item in part_existing.get(part_id, [])
                if not (isinstance(item, dict) and item.get("source") == "part_vehicle_fitment")
            ]

            merged: List[Dict[str, Any]] = []
            seen_json = set()
            for item in preserved + entries:
                if not isinstance(item, dict):
                    continue
                key = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if key in seen_json:
                    continue
                seen_json.add(key)
                merged.append(item)

            existing_json = json.dumps(part_existing.get(part_id, []), sort_keys=True, ensure_ascii=False)
            merged_json = json.dumps(merged, sort_keys=True, ensure_ascii=False)
            if existing_json == merged_json:
                continue

            await db.execute(
                text(
                    """
                    UPDATE parts_catalog
                    SET compatible_vehicles = CAST(:compat AS jsonb),
                        updated_at = NOW()
                    WHERE id = CAST(:part_id AS uuid)
                    """
                ),
                {
                    "part_id": part_id,
                    "compat": json.dumps(merged, ensure_ascii=False),
                },
            )
            updated_parts += 1
            merged_fitment_rows += len(entries)

        await db.commit()

        return {
            "task": "merge_catalog_fitment_from_part_vehicle_fitment_brand_scoped",
            "status": "ok",
            "brand": brand,
            "aliases": aliases,
            "scanned_rows": scanned_rows,
            "parts_with_fitment": len(part_fitments),
            "updated_parts": updated_parts,
            "merged_fitment_rows": merged_fitment_rows,
            "elapsed_s": round(time.monotonic() - t0, 2),
        }


def main() -> None:
    args = _parse_args()
    report = asyncio.run(run_brand_merge(args.brand))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
