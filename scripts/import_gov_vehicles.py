#!/usr/bin/env python3
"""Import Israeli government vehicle datasets into vehicle_market_il."""

import asyncio
import json
import os
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import asyncpg


API_URL = "https://data.gov.il/api/3/action/datastore_search"
RESOURCE_WLTP = "142afde2-6228-49f9-8a29-9b6c3a0cbe40"
RESOURCE_QUANTITIES = "5e87a7a1-2f6f-41c1-8aec-7216d52a6cf6"
PAGE_SIZE = 1000
REQUEST_DELAY_SEC = 0.3
PROGRESS_EVERY = 5000
USER_AGENT = "AutoSpareFinder/1.0"


def _normalize_db_url(raw_url: str) -> str:
    return raw_url.replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _to_text(value: Any, max_len: int | None = None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if max_len is not None:
        return text[:max_len]
    return text


def _fetch_page(resource_id: str, offset: int) -> list[dict[str, Any]]:
    params = urlencode({"resource_id": resource_id, "limit": PAGE_SIZE, "offset": offset})
    request = Request(
        url=f"{API_URL}?{params}",
        headers={"User-Agent": USER_AGENT},
        method="GET",
    )
    with urlopen(request, timeout=90) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not payload.get("success"):
        raise RuntimeError(f"Government API request failed for resource {resource_id} at offset {offset}")

    return payload.get("result", {}).get("records", [])


async def _iterate_records(resource_id: str) -> Iterable[list[dict[str, Any]]]:
    offset = 0
    while True:
        records = await asyncio.to_thread(_fetch_page, resource_id, offset)
        if not records:
            break
        yield records
        offset += PAGE_SIZE
        await asyncio.sleep(REQUEST_DELAY_SEC)


async def import_quantities(conn: asyncpg.Connection) -> int:
    upsert_sql = """
    INSERT INTO vehicle_market_il (
        tozeret_cd,
        manufacturer,
        manufacturer_nm,
        country,
        degem_cd,
        degem_nm,
        kinuy_mishari,
        shnat_yitzur,
        sug_degem,
        mispar_rechavim_pailim,
        mispar_rechavim_le_pailim
    )
    VALUES (
        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11
    )
    ON CONFLICT (tozeret_cd, degem_cd, shnat_yitzur)
    DO UPDATE SET
        mispar_rechavim_pailim = EXCLUDED.mispar_rechavim_pailim,
        mispar_rechavim_le_pailim = EXCLUDED.mispar_rechavim_le_pailim,
        updated_at = NOW()
    """

    total = 0
    async for batch in _iterate_records(RESOURCE_QUANTITIES):
        rows = [
            (
                _to_int(rec.get("tozeret_cd")),
                _to_text(rec.get("tozar"), 150),
                _to_text(rec.get("tozeret_nm"), 150),
                _to_text(rec.get("tozeret_eretz_nm"), 100),
                _to_int(rec.get("degem_cd")),
                _to_text(rec.get("degem_nm"), 100),
                _to_text(rec.get("kinuy_mishari"), 150),
                _to_int(rec.get("shnat_yitzur")),
                _to_text(rec.get("sug_degem"), 10),
                _to_int(rec.get("mispar_rechavim_pailim")),
                _to_int(rec.get("mispar_rechavim_le_pailim")),
            )
            for rec in batch
        ]

        await conn.executemany(upsert_sql, rows)
        total += len(rows)
        if total % PROGRESS_EVERY == 0:
            print(f"[quantities] imported {total} records")

    print(f"[quantities] done, total imported: {total}")
    return total


async def import_wltp(conn: asyncpg.Connection) -> int:
    upsert_sql = """
    INSERT INTO vehicle_market_il (
        tozeret_cd,
        degem_cd,
        shnat_yitzur,
        nefah_manoa,
        koah_sus,
        delek_nm,
        technologiat_hanaa_nm,
        sug_tkina_nm,
        ramat_gimur,
        kvutzat_zihum,
        madad_yarok,
        automatic_ind
    )
    VALUES (
        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12
    )
    ON CONFLICT (tozeret_cd, degem_cd, shnat_yitzur)
    DO UPDATE SET
        nefah_manoa = EXCLUDED.nefah_manoa,
        koah_sus = EXCLUDED.koah_sus,
        delek_nm = EXCLUDED.delek_nm,
        technologiat_hanaa_nm = EXCLUDED.technologiat_hanaa_nm,
        sug_tkina_nm = EXCLUDED.sug_tkina_nm,
        ramat_gimur = EXCLUDED.ramat_gimur,
        kvutzat_zihum = EXCLUDED.kvutzat_zihum,
        madad_yarok = EXCLUDED.madad_yarok,
        automatic_ind = EXCLUDED.automatic_ind,
        updated_at = NOW()
    """

    total = 0
    async for batch in _iterate_records(RESOURCE_WLTP):
        rows = [
            (
                _to_int(rec.get("tozeret_cd")),
                _to_int(rec.get("degem_cd")),
                _to_int(rec.get("shnat_yitzur")),
                _to_int(rec.get("nefah_manoa")),
                _to_int(rec.get("koah_sus")),
                _to_text(rec.get("delek_nm"), 50),
                _to_text(rec.get("technologiat_hanaa_nm"), 100),
                _to_text(rec.get("sug_tkina_nm"), 50),
                _to_text(rec.get("ramat_gimur"), 50),
                _to_int(rec.get("kvutzat_zihum")),
                _to_int(rec.get("madad_yarok")),
                _to_int(rec.get("automatic_ind")),
            )
            for rec in batch
        ]

        await conn.executemany(upsert_sql, rows)
        total += len(rows)
        if total % PROGRESS_EVERY == 0:
            print(f"[wltp] imported {total} records")

    print(f"[wltp] done, total imported: {total}")
    return total


async def print_top_20(conn: asyncpg.Connection) -> None:
    query = """
    SELECT
        manufacturer,
        kinuy_mishari,
        shnat_yitzur,
        mispar_rechavim_pailim AS active_count
    FROM vehicle_market_il
    WHERE mispar_rechavim_pailim IS NOT NULL
    ORDER BY mispar_rechavim_pailim DESC
    LIMIT 20
    """
    rows = await conn.fetch(query)
    print("\\nTop 20 most popular vehicles in Israel:")
    for idx, row in enumerate(rows, start=1):
        manufacturer = row.get("manufacturer") or "-"
        kinuy_mishari = row.get("kinuy_mishari") or "-"
        shnat_yitzur = row.get("shnat_yitzur") or "-"
        active_count = row.get("active_count") or 0
        print(f"{idx:02d}. {manufacturer} | {kinuy_mishari} | {shnat_yitzur} | {active_count}")


async def main() -> None:
    raw_db_url = os.getenv("DATABASE_URL")
    if not raw_db_url:
        raise RuntimeError("DATABASE_URL environment variable is required")

    db_url = _normalize_db_url(raw_db_url)
    conn = await asyncpg.connect(db_url)
    try:
        print("Starting import: dataset 2 (quantities)")
        await import_quantities(conn)

        print("Starting import: dataset 1 (WLTP specs)")
        await import_wltp(conn)

        await print_top_20(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
