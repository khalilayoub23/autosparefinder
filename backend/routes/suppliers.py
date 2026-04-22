from fastapi import APIRouter, Query

from services.supplier_aggregator import ACTIVE_SUPPLIERS, find_best_price, search_all_suppliers, search_by_oem_all

router = APIRouter(prefix="/api/suppliers", tags=["Suppliers"])


@router.get("/search")
async def search_parts(
    query: str = Query(..., description="Part name or description"),
    limit: int = Query(10, ge=1, le=50),
):
    results = await search_all_suppliers(query, limit)
    return [vars(result) for result in results]


@router.get("/search/oem")
async def search_by_oem(
    oem_number: str = Query(..., description="OEM part number"),
    limit: int = Query(10, ge=1, le=50),
):
    results = await search_by_oem_all(oem_number, limit)
    return [vars(result) for result in results]


@router.get("/compare")
async def compare_prices(
    part: str = Query(..., description="Part name"),
    make: str = Query("", description="Vehicle make"),
    model: str = Query("", description="Vehicle model"),
    year: str = Query("", description="Vehicle year"),
):
    results = await find_best_price(part, make, model, year)
    return [vars(result) for result in results]


@router.get("/health")
async def suppliers_health():
    return {"active_suppliers": [supplier.name for supplier in ACTIVE_SUPPLIERS]}
