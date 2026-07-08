"""
supplier_aggregator.py — Central supplier registry.
Wires all suppliers from the PDF research into search + comparison.

Priority tiers (from PDF Car Parts Sellers Study):
  Tier 1 (API, ships Israel): AliExpress DS, eBay, Autodoc
  Tier 2 (ships Israel, batch import): RockAuto, Spareto
  Tier 3 (ships Israel, affiliate): Alvadi, Cars245, PartSouq, Amayama,
          FCP Euro, Summit Racing, Fitinpart, Pelican Parts, ECS Tuning,
          Toyota Parts Deal, Ford Parts Giant, Hyundai Parts Deal

Skipped (no Israel direct shipping): BMW Parts Factory, Bosch Direct,
  Denso Direct, NGK Direct, Brembo Direct, Mann, Continental, Valeo Service,
  Mercedes-Benz Used Parts
"""
import asyncio
import logging
import os

from services.suppliers.aliexpress_supplier import AliExpressSupplier
from services.suppliers.autodoc_supplier import AutodocSupplier
from services.suppliers.base_supplier import PartResult
from services.suppliers.catalog_suppliers import (
    AlvadiSupplier,
    Cars245Supplier,
    ECSTuningSupplier,
    FCPEuroSupplier,
    FitinpartSupplier,
    FordPartsSupplier,
    HyundaiPartsSupplier,
    PelicanPartsSupplier,
    RockAutoSupplier,
    SparetoSupplier,
    SummitRacingSupplier,
    ToyotaPartsSupplier,
)
from services.suppliers.amayama_supplier import AmayamaSupplier
from services.suppliers.ebay_supplier import EbaySupplier
from services.suppliers.local_db_supplier import LocalDBSupplier
from services.suppliers.partsouq_supplier import PartSouqSupplier

logger = logging.getLogger(__name__)


def _enabled(env_var: str) -> bool:
    return os.getenv(env_var, "1").strip().lower() not in ("0", "false", "no", "off")


def _aliexpress_enabled() -> bool:
    return bool(
        os.getenv("ALIEXPRESS_APP_KEY", "").strip()
        and os.getenv("ALIEXPRESS_APP_SECRET", "").strip()
        and os.getenv("ALIEXPRESS_ACCESS_TOKEN", "").strip()
    )


def _get_active_suppliers():
    suppliers = []

    # ── Local DB (our own catalog — fastest, no network) ─────────────────────
    try:
        from BACKEND_API_ROUTES import async_session_factory
        suppliers.append(LocalDBSupplier(async_session_factory))
        logger.info("[suppliers] LocalDB: enabled")
    except Exception as e:
        logger.error("[suppliers] LocalDB failed: %s", e)

    # ── Tier 1: Real API suppliers ────────────────────────────────────────────
    suppliers.append(EbaySupplier())
    logger.info("[suppliers] eBay: enabled")

    if _aliexpress_enabled():
        suppliers.append(AliExpressSupplier())
        logger.info("[suppliers] AliExpress DS: enabled")
    else:
        logger.info("[suppliers] AliExpress DS: disabled (missing credentials)")

    if _enabled("EXTERNAL_ENABLE_AUTODOC"):
        suppliers.append(AutodocSupplier())
        logger.info("[suppliers] Autodoc: enabled")

    # ── Tier 2: Batch-import suppliers (also provide affiliate fallback) ──────
    if _enabled("EXTERNAL_ENABLE_ROCKAUTO"):
        suppliers.append(RockAutoSupplier())
        logger.info("[suppliers] RockAuto: enabled")

    if _enabled("EXTERNAL_ENABLE_SPARETO"):
        suppliers.append(SparetoSupplier())
        logger.info("[suppliers] Spareto: enabled")

    # ── Tier 3: Ships-to-Israel affiliate suppliers ───────────────────────────
    if _enabled("EXTERNAL_ENABLE_PARTSOUQ"):
        suppliers.append(PartSouqSupplier())
        logger.info("[suppliers] PartSouq: enabled")

    if _enabled("EXTERNAL_ENABLE_AMAYAMA"):
        suppliers.append(AmayamaSupplier())
        logger.info("[suppliers] Amayama: enabled")

    if _enabled("EXTERNAL_ENABLE_ALVADI"):
        suppliers.append(AlvadiSupplier())
        logger.info("[suppliers] Alvadi: enabled")

    if _enabled("EXTERNAL_ENABLE_CARS245"):
        suppliers.append(Cars245Supplier())
        logger.info("[suppliers] Cars245: enabled")

    if _enabled("EXTERNAL_ENABLE_FCPEURO"):
        suppliers.append(FCPEuroSupplier())
        logger.info("[suppliers] FCP Euro: enabled")

    if _enabled("EXTERNAL_ENABLE_SUMMIT_RACING"):
        suppliers.append(SummitRacingSupplier())
        logger.info("[suppliers] Summit Racing: enabled")

    if _enabled("EXTERNAL_ENABLE_FITINPART"):
        suppliers.append(FitinpartSupplier())
        logger.info("[suppliers] Fitinpart: enabled")

    if _enabled("EXTERNAL_ENABLE_PELICAN"):
        suppliers.append(PelicanPartsSupplier())
        logger.info("[suppliers] Pelican Parts: enabled")

    if _enabled("EXTERNAL_ENABLE_ECS_TUNING"):
        suppliers.append(ECSTuningSupplier())
        logger.info("[suppliers] ECS Tuning: enabled")

    # OEM brand-specific
    if _enabled("EXTERNAL_ENABLE_TOYOTA_PARTS"):
        suppliers.append(ToyotaPartsSupplier())
    if _enabled("EXTERNAL_ENABLE_FORD_PARTS"):
        suppliers.append(FordPartsSupplier())
    if _enabled("EXTERNAL_ENABLE_HYUNDAI_PARTS"):
        suppliers.append(HyundaiPartsSupplier())

    logger.info("[suppliers] Total active suppliers: %d", len(suppliers))
    return suppliers


ACTIVE_SUPPLIERS = _get_active_suppliers()

# Name → supplier map for targeted lookups
SUPPLIER_MAP = {s.name: s for s in ACTIVE_SUPPLIERS}


async def search_all_suppliers(query: str, limit_per_supplier: int = 10) -> list[PartResult]:
    tasks = [s.search(query, limit_per_supplier) for s in ACTIVE_SUPPLIERS]
    results_per_supplier = await asyncio.gather(*tasks, return_exceptions=True)

    all_results: list[PartResult] = []
    for i, result in enumerate(results_per_supplier):
        if isinstance(result, Exception):
            logger.error("[suppliers] %s search failed: %s", ACTIVE_SUPPLIERS[i].name, result)
            continue
        all_results.extend(result)

    # Sort: priced results first (total_cost > 0), then affiliate links, cheapest first
    priced = sorted([r for r in all_results if r.total_cost > 0], key=lambda x: x.total_cost)
    affiliates = [r for r in all_results if r.total_cost == 0]
    return priced + affiliates


async def search_by_oem_all(oem_number: str, limit_per_supplier: int = 10) -> list[PartResult]:
    tasks = [s.search_by_oem(oem_number, limit_per_supplier) for s in ACTIVE_SUPPLIERS]
    results_per_supplier = await asyncio.gather(*tasks, return_exceptions=True)

    all_results: list[PartResult] = []
    for i, result in enumerate(results_per_supplier):
        if isinstance(result, Exception):
            logger.error("[suppliers] %s OEM search failed: %s", ACTIVE_SUPPLIERS[i].name, result)
            continue
        all_results.extend(result)

    priced = sorted([r for r in all_results if r.total_cost > 0], key=lambda x: x.total_cost)
    affiliates = [r for r in all_results if r.total_cost == 0]
    return priced + affiliates


async def find_best_price(
    part_name: str,
    vehicle_make: str = "",
    vehicle_model: str = "",
    vehicle_year: str = "",
) -> list[PartResult]:
    query = " ".join(filter(None, [part_name, vehicle_make, vehicle_model, vehicle_year]))
    return await search_all_suppliers(query)


def get_supplier_names() -> list[str]:
    return [s.name for s in ACTIVE_SUPPLIERS]
