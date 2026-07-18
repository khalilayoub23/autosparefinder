from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup
from sqlalchemy import text

from BACKEND_DATABASE_MODELS import async_session_factory
from categories import guess_category_by_text

FEBEST_CATALOG_URL = "https://febest.de/en/catalog"
USER_AGENT = "Mozilla/5.0 (compatible; REX-Febest-Enrichment/1.0)"
TIMEOUT_S = 20


@dataclass
class FebestRow:
    code: str
    name: str
    compatible_oem: str


def _clean_token(token: str) -> str:
    val = (token or "").strip()
    val = val.replace("\u00a0", " ")
    val = re.sub(r"\s+", " ", val)
    # Footnote markers used by Febest catalog rows.
    val = val.replace("#", "").replace("*", "").strip()
    return val


def _split_oem_values(raw: str) -> List[str]:
    raw = _clean_token(raw)
    if not raw:
        return []
    parts = re.split(r"[,;/]|\s{2,}", raw)
    out: List[str] = []
    seen = set()
    for p in parts:
        token = _clean_token(p)
        if not token:
            continue
        # Keep typical OEM punctuation while removing obvious junk.
        token = re.sub(r"[^A-Za-z0-9\-./]", "", token)
        if len(token) < 4:
            continue
        key = token.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(token[:100])
    return out


def _fetch_febest_rows(query_key: str, query_value: str) -> List[FebestRow]:
    params = {query_key: query_value, "find": "Find"}
    url = f"{FEBEST_CATALOG_URL}?{urlencode(params)}"
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        timeout=TIMEOUT_S,
        allow_redirects=True,
    )
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    target_table = None
    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
        if "code" in headers and ("compatible oem" in headers or "oem" in headers):
            target_table = table
            break
    if target_table is None:
        return []

    rows: List[FebestRow] = []
    for tr in target_table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 3:
            continue
        code = _clean_token(cells[0].get_text(" ", strip=True))
        name = _clean_token(cells[1].get_text(" ", strip=True))
        oem = _clean_token(cells[2].get_text(" ", strip=True))
        if not code and not oem:
            continue
        rows.append(FebestRow(code=code[:100], name=name[:255], compatible_oem=oem[:255]))
    return rows


async def _load_candidates(db, brands: List[str], per_brand: int) -> List[Dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                """
                SELECT id, manufacturer, manufacturer_id, name, category, sku, oem_number
                FROM parts_catalog
                WHERE is_active = TRUE
                  AND manufacturer = ANY(:brands)
                  AND (
                        oem_number IS NOT NULL AND btrim(oem_number) <> ''
                     OR sku IS NOT NULL AND btrim(sku) <> ''
                  )
                ORDER BY manufacturer, updated_at DESC NULLS LAST, created_at DESC NULLS LAST
                LIMIT :lim
                """
            ),
            {"brands": brands, "lim": max(1, per_brand) * max(1, len(brands))},
        )
    ).mappings().all()

    grouped: Dict[str, int] = {b: 0 for b in brands}
    out: List[Dict[str, Any]] = []
    for r in rows:
        m = str(r["manufacturer"])
        if grouped.get(m, 0) >= per_brand:
            continue
        grouped[m] = grouped.get(m, 0) + 1
        out.append(dict(r))
    return out


async def _ensure_aftermarket_brand_id(db, name: str) -> Optional[str]:
    row = (
        await db.execute(
            text(
                """
                SELECT id
                FROM aftermarket_brands
                WHERE LOWER(TRIM(name)) = LOWER(TRIM(:name))
                LIMIT 1
                """
            ),
            {"name": name},
        )
    ).fetchone()
    if row and row[0]:
        return str(row[0])

    created = (
        await db.execute(
            text(
                """
                INSERT INTO aftermarket_brands (id, name, tier, is_active, created_at, updated_at)
                VALUES (gen_random_uuid(), :name, 'generic', TRUE, NOW(), NOW())
                ON CONFLICT (name) DO UPDATE SET updated_at = NOW()
                RETURNING id
                """
            ),
            {"name": name},
        )
    ).fetchone()
    return str(created[0]) if created and created[0] else None


async def _upsert_cross_ref(
    db,
    *,
    part_id: str,
    ref_number: str,
    manufacturer: str,
    ref_type: str,
    manufacturer_id: Optional[str] = None,
    aftermarket_brand_id: Optional[str] = None,
) -> int:
    result = await db.execute(
        text(
            """
            INSERT INTO part_cross_reference
                (
                    id,
                    part_id,
                    ref_number,
                    manufacturer,
                    manufacturer_id,
                    aftermarket_brand_id,
                    ref_type,
                    created_at
                )
            SELECT
                gen_random_uuid(),
                CAST(:pid AS uuid),
                CAST(:num AS varchar(100)),
                CAST(:mfr AS varchar(100)),
                CAST(:manufacturer_id AS uuid),
                CAST(:aftermarket_brand_id AS uuid),
                CAST(:rtype AS varchar(20)),
                NOW()
            WHERE NOT EXISTS (
                SELECT 1
                FROM part_cross_reference
                WHERE part_id = CAST(:pid AS uuid)
                  AND ref_number = CAST(:num AS varchar(100))
            )
            """
        ),
        {
            "pid": str(part_id),
            "num": ref_number[:100],
            "mfr": manufacturer[:100],
            "manufacturer_id": str(manufacturer_id) if manufacturer_id else None,
            "aftermarket_brand_id": str(aftermarket_brand_id) if aftermarket_brand_id else None,
            "rtype": ref_type[:20],
        },
    )
    return 1 if (result.rowcount or 0) > 0 else 0


async def run_febest_enrichment(brands: List[str], per_brand: int) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "brands": brands,
        "per_brand": per_brand,
        "parts_examined": 0,
        "febest_rows_found": 0,
        "crossrefs_inserted": 0,
        "oem_filled": 0,
        "category_updates": 0,
        "errors": [],
    }

    async with async_session_factory() as db:
        febest_aftermarket_brand_id = await _ensure_aftermarket_brand_id(db, "FEBEST")
        candidates = await _load_candidates(db, brands=brands, per_brand=per_brand)
        report["parts_examined"] = len(candidates)

        for part in candidates:
            part_id = str(part["id"])
            manufacturer = str(part.get("manufacturer") or "")
            manufacturer_id = str(part.get("manufacturer_id") or "").strip() or None
            current_name = str(part.get("name") or "")
            current_cat = str(part.get("category") or "").strip()
            sku = str(part.get("sku") or "").strip()
            oem_number = str(part.get("oem_number") or "").strip()

            queries: List[tuple[str, str]] = []
            if oem_number:
                queries.append(("oem", oem_number))
            if sku:
                # SKU often includes prefixes; try full and suffix token.
                queries.append(("code", sku))
                if "-" in sku:
                    queries.append(("code", sku.split("-", 1)[1].strip()))

            seen_row_keys = set()
            matched_rows: List[FebestRow] = []
            for qk, qv in queries:
                try:
                    rows = _fetch_febest_rows(qk, qv)
                except Exception as exc:
                    report["errors"].append(f"part={part_id} query={qk}:{qv} err={exc}")
                    continue
                for row in rows:
                    key = (row.code.upper(), row.compatible_oem.upper(), row.name.upper())
                    if key in seen_row_keys:
                        continue
                    seen_row_keys.add(key)
                    matched_rows.append(row)

            if not matched_rows:
                continue

            report["febest_rows_found"] += len(matched_rows)

            inferred_category = None
            if current_cat in {"", "כללי"}:
                # Use Febest product title only for currently generic rows.
                inferred_category = guess_category_by_text(matched_rows[0].name)

            for row in matched_rows:
                if row.code:
                    report["crossrefs_inserted"] += await _upsert_cross_ref(
                        db,
                        part_id=part_id,
                        ref_number=row.code,
                        manufacturer="FEBEST",
                        ref_type="AFTERMARKET",
                        manufacturer_id=None,
                        aftermarket_brand_id=febest_aftermarket_brand_id,
                    )
                oem_tokens = _split_oem_values(row.compatible_oem)
                for token in oem_tokens:
                    report["crossrefs_inserted"] += await _upsert_cross_ref(
                        db,
                        part_id=part_id,
                        ref_number=token,
                        manufacturer=manufacturer or "UNKNOWN",
                        ref_type="OEM_EQUIVALENT",
                        manufacturer_id=manufacturer_id,
                        aftermarket_brand_id=None,
                    )
                    if not oem_number:
                        await db.execute(
                            text(
                                """
                                UPDATE parts_catalog
                                SET oem_number = :oem,
                                    needs_oem_lookup = FALSE,
                                    updated_at = NOW()
                                WHERE id = CAST(:pid AS uuid)
                                  AND (oem_number IS NULL OR btrim(oem_number) = '')
                                """
                            ),
                            {"pid": part_id, "oem": token[:100]},
                        )
                        oem_number = token
                        report["oem_filled"] += 1

            if inferred_category and inferred_category != "כללי" and current_cat in {"", "כללי"}:
                await db.execute(
                    text(
                        """
                        UPDATE parts_catalog
                        SET category = :cat,
                            updated_at = NOW()
                        WHERE id = CAST(:pid AS uuid)
                          AND (category IS NULL OR btrim(category) = '' OR category = 'כללי')
                        """
                    ),
                    {"pid": part_id, "cat": inferred_category[:100]},
                )
                report["category_updates"] += 1

        await db.commit()

    return report


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="REX Febest enrichment: import OEM/aftermarket refs into catalog.")
    p.add_argument("--brands", required=True, help="Comma-separated manufacturer names.")
    p.add_argument("--per-brand", type=int, default=50, help="How many parts to process per brand.")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    brands = [b.strip() for b in str(args.brands or "").split(",") if b.strip()]
    report = asyncio.run(run_febest_enrichment(brands=brands, per_brand=max(1, int(args.per_brand))))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
