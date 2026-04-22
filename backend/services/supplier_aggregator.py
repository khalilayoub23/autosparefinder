import asyncio
import logging
from services.suppliers.base_supplier import PartResult
from services.suppliers.ebay_supplier import EbaySupplier
from services.suppliers.local_db_supplier import LocalDBSupplier

logger = logging.getLogger(__name__)

def _get_active_suppliers():
    try:
        from BACKEND_API_ROUTES import async_session_factory
        return [
            LocalDBSupplier(async_session_factory),
            EbaySupplier(),
        ]
    except Exception as e:
        logger.error(f"Failed to initialize suppliers: {e}")
        return [EbaySupplier()]

ACTIVE_SUPPLIERS = _get_active_suppliers()

async def search_all_suppliers(query: str, limit_per_supplier: int = 10) -> list[PartResult]:
    tasks = [supplier.search(query, limit_per_supplier) for supplier in ACTIVE_SUPPLIERS]
    results_per_supplier = await asyncio.gather(*tasks, return_exceptions=True)

    all_results: list[PartResult] = []
    for index, result in enumerate(results_per_supplier):
        if isinstance(result, Exception):
            logger.error(f"Supplier {ACTIVE_SUPPLIERS[index].name} failed: {result}")
            continue
        all_results.extend(result)

    return sorted(all_results, key=lambda x: x.total_cost)

async def search_by_oem_all(oem_number: str, limit_per_supplier: int = 10) -> list[PartResult]:
    tasks = [supplier.search_by_oem(oem_number, limit_per_supplier) for supplier in ACTIVE_SUPPLIERS]
    results_per_supplier = await asyncio.gather(*tasks, return_exceptions=True)

    all_results: list[PartResult] = []
    for index, result in enumerate(results_per_supplier):
        if isinstance(result, Exception):
            logger.error(f"Supplier {ACTIVE_SUPPLIERS[index].name} OEM search failed: {result}")
            continue
        all_results.extend(result)

    return sorted(all_results, key=lambda x: x.total_cost)

async def find_best_price(
    part_name: str,
    vehicle_make: str = "",
    vehicle_model: str = "",
    vehicle_year: str = ""
) -> list[PartResult]:
    query = " ".join(filter(None, [part_name, vehicle_make, vehicle_model, vehicle_year]))
    logger.info(f"find_best_price query: {query}")
    return await search_all_suppliers(query)
