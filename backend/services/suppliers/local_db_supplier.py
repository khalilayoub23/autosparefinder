import logging
from typing import Optional
from sqlalchemy import text
from services.suppliers.base_supplier import BaseSupplier, PartResult

logger = logging.getLogger(__name__)

class LocalDBSupplier(BaseSupplier):
    name = "local_db"

    def __init__(self, db_session_factory):
        self._session_factory = db_session_factory

    async def search(self, query: str, limit: int = 10) -> list[PartResult]:
        try:
            terms = query.strip().split()
            if not terms:
                return []

            term_conditions = [
                f"(pc.name ILIKE :term{i} OR pc.name_he ILIKE :term{i} OR pc.oem_number ILIKE :term{i} OR sp.supplier_sku ILIKE :term{i})"
                for i in range(len(terms))
            ]
            strict_conditions = " AND ".join(term_conditions)
            relaxed_conditions = " OR ".join(term_conditions)

            match_score_terms = [
                f"CASE WHEN {term_conditions[i]} THEN 1 ELSE 0 END"
                for i in range(len(terms))
            ]
            match_score_sql = " + ".join(match_score_terms)

            params = {f"term{i}": f"%{term}%" for i, term in enumerate(terms)}
            params["limit"] = limit

            strict_sql = text(f"""
                SELECT 
                    sp.id::text as item_id,
                    pc.name as title,
                    pc.name_he as title_he,
                    pc.oem_number,
                    sp.price_ils,
                    sp.price_usd,
                    sp.shipping_cost_ils,
                    sp.shipping_cost_usd,
                    sp.supplier_sku,
                    sp.supplier_url,
                    s.name as supplier_name,
                    s.country
                FROM supplier_parts sp
                JOIN parts_catalog pc ON pc.id = sp.part_id
                JOIN suppliers s ON s.id = sp.supplier_id
                WHERE sp.is_available = true
                AND ({strict_conditions})
                ORDER BY sp.price_ils ASC
                LIMIT :limit
            """)

            relaxed_sql = text(f"""
                SELECT 
                    sp.id::text as item_id,
                    pc.name as title,
                    pc.name_he as title_he,
                    pc.oem_number,
                    sp.price_ils,
                    sp.price_usd,
                    sp.shipping_cost_ils,
                    sp.shipping_cost_usd,
                    sp.supplier_sku,
                    sp.supplier_url,
                    s.name as supplier_name,
                    s.country,
                    ({match_score_sql}) as match_score
                FROM supplier_parts sp
                JOIN parts_catalog pc ON pc.id = sp.part_id
                JOIN suppliers s ON s.id = sp.supplier_id
                WHERE sp.is_available = true
                AND ({relaxed_conditions})
                ORDER BY match_score DESC, sp.price_ils ASC
                LIMIT :limit
            """)

            async with self._session_factory() as db:
                strict_result = await db.execute(strict_sql, params)
                rows = strict_result.fetchall()

                if not rows and len(terms) > 1:
                    relaxed_result = await db.execute(relaxed_sql, params)
                    rows = relaxed_result.fetchall()
                    logger.info(
                        f"LocalDB strict search returned 0 for '{query}', used relaxed fallback and got {len(rows)} result(s)"
                    )

            results = []
            for row in rows:
                results.append(PartResult(
                    supplier=f"local_db:{row.supplier_name}",
                    item_id=row.item_id,
                    title=f"{row.title} ({row.supplier_sku})",
                    price=float(row.price_usd or 0),
                    currency="USD",
                    shipping_cost=float(row.shipping_cost_usd or 0),
                    total_cost=float(row.price_usd or 0) + float(row.shipping_cost_usd or 0),
                    condition="New",
                    seller=row.supplier_name,
                    seller_rating=None,
                    item_url=row.supplier_url or "",
                    image_url=None,
                    location=row.country or "IL",
                    estimated_delivery_days=2,
                ))

            logger.info(f"LocalDB query '{query}' returned {len(results)} results")
            return results

        except Exception as e:
            logger.error(f"LocalDBSupplier search failed: {e}", exc_info=True)
            return []

    async def search_by_oem(self, oem_number: str, limit: int = 10) -> list[PartResult]:
        return await self.search(oem_number, limit)

    async def get_part_details(self, item_id: str) -> Optional[PartResult]:
        return None
