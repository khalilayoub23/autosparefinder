"""
Brands reference — /api/v1/brands endpoints extracted from BACKEND_API_ROUTES.py.

Endpoints:
  GET /api/v1/brands
  GET /api/v1/brands/with-parts
  GET /api/v1/brands/{brand_name}/parts
"""
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func, text

from BACKEND_DATABASE_MODELS import get_db, CarBrand, TruckBrand, PartsCatalog

router = APIRouter()


@router.get("/api/v1/brands")
async def get_brands(
    region: Optional[str] = None,
    group: Optional[str] = None,
    is_luxury: Optional[bool] = None,
    is_electric: Optional[bool] = None,
    q: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Return the car_brands reference table with optional filters."""
    stmt = select(CarBrand).where(CarBrand.is_active == True)
    if region:
        stmt = stmt.where(CarBrand.region == region)
    if group:
        stmt = stmt.where(CarBrand.group_name.ilike(f"%{group}%"))
    if is_luxury is not None:
        stmt = stmt.where(CarBrand.is_luxury == is_luxury)
    if is_electric is not None:
        stmt = stmt.where(CarBrand.is_electric_focused == is_electric)
    if q:
        stmt = stmt.where(
            CarBrand.name.ilike(f"%{q}%") | CarBrand.name_he.ilike(f"%{q}%")
        )
    stmt = stmt.order_by(CarBrand.name)
    result = await db.execute(stmt)
    brands = result.scalars().all()
    return {
        "brands": [
            {
                "id": str(b.id),
                "name": b.name,
                "name_he": b.name_he,
                "group_name": b.group_name,
                "country": b.country,
                "region": b.region,
                "is_luxury": b.is_luxury,
                "is_electric_focused": b.is_electric_focused,
                "website": b.website,
                "has_parts": False,  # enriched below
            }
            for b in brands
        ],
        "total": len(brands),
    }


@router.get("/api/v1/brands/with-parts")
async def get_brands_with_parts(db: AsyncSession = Depends(get_db)):
    """Return brands that have actual parts in parts_catalog, merged with registry info."""
    # Brands that have parts
    parts_result = await db.execute(
        select(PartsCatalog.manufacturer, func.count().label("parts_count"))
        .where(PartsCatalog.is_active == True)
        .group_by(PartsCatalog.manufacturer)
        .order_by(func.count().desc())
    )
    parts_by_mfr = {row[0]: row[1] for row in parts_result.fetchall() if row[0]}

    # All known brands
    brand_result = await db.execute(select(CarBrand).where(CarBrand.is_active == True).order_by(CarBrand.name))
    all_brands = brand_result.scalars().all()

    # Merge: known brands get parts count; parts-only manufacturers get minimal entry
    merged = []
    seen_names = set()
    for b in all_brands:
        # Exact case-insensitive match on canonical name
        count = 0
        for mfr_name in list(parts_by_mfr.keys()):
            if mfr_name and mfr_name.lower() == b.name.lower():
                count += parts_by_mfr[mfr_name]
                seen_names.add(mfr_name.lower())
        # Also match via aliases stored in car_brands
        aliases = b.aliases or []
        for alias in aliases:
            for mfr_name in list(parts_by_mfr.keys()):
                if mfr_name and mfr_name.lower() == alias.lower():
                    count += parts_by_mfr[mfr_name]
                    seen_names.add(mfr_name.lower())
        seen_names.add(b.name.lower())
        merged.append({
            "name": b.name, "name_he": b.name_he,
            "group_name": b.group_name, "country": b.country,
            "region": b.region, "is_luxury": b.is_luxury,
            "is_electric_focused": b.is_electric_focused,
            "website": b.website, "parts_count": count,
            "has_parts": count > 0,
            "aliases": aliases,
        })

    # Add any parts-only manufacturers not in car_brands registry
    for mfr_name, mfr_count in parts_by_mfr.items():
        if mfr_name and mfr_name.lower() not in seen_names:
            # Check if it matches any brand already included
            already = any(mfr_name.lower() in m["name"].lower() for m in merged)
            if not already:
                merged.append({
                    "name": mfr_name, "name_he": None, "group_name": None,
                    "country": None, "region": None, "is_luxury": False,
                    "is_electric_focused": False, "website": None,
                    "parts_count": mfr_count, "has_parts": True,
                })

    return {"brands": merged, "total": len(merged)}


@router.get("/api/v1/brands/{brand_name}/parts")
async def get_parts_by_brand(
    brand_name: str,
    category: Optional[str] = None,
    part_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Return parts for a specific brand (by canonical name or alias), with pricing from supplier_parts."""
    # Resolve brand aliases
    brand_result = await db.execute(
        select(CarBrand).where(CarBrand.is_active == True).where(
            or_(CarBrand.name.ilike(brand_name), CarBrand.name_he.ilike(brand_name))
        ).limit(1)
    )
    brand = brand_result.scalar_one_or_none()

    # Build manufacturer name set to search
    mfr_names: list[str] = [brand_name]
    if brand:
        mfr_names = [brand.name] + (brand.aliases or [])

    # Query parts
    stmt = (
        select(PartsCatalog)
        .where(PartsCatalog.is_active == True)
        .where(or_(*[PartsCatalog.manufacturer.ilike(m) for m in mfr_names]))
    )
    if category:
        stmt = stmt.where(PartsCatalog.category.ilike(f"%{category}%"))
    if part_type:
        stmt = stmt.where(PartsCatalog.part_type == part_type)

    # Count total
    count_stmt = (
        select(func.count(PartsCatalog.id))
        .where(PartsCatalog.is_active == True)
        .where(or_(*[PartsCatalog.manufacturer.ilike(m) for m in mfr_names]))
    )
    if category:
        count_stmt = count_stmt.where(PartsCatalog.category.ilike(f"%{category}%"))
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()

    stmt = stmt.order_by(PartsCatalog.category, PartsCatalog.name).offset(offset).limit(limit)
    result = await db.execute(stmt)
    parts = result.scalars().all()

    if not parts:
        return {"brand": brand.name if brand else brand_name, "brand_he": brand.name_he if brand else None,
                "total": total, "offset": offset, "limit": limit, "parts": []}

    from BACKEND_AI_AGENTS import PartsFinderAgent, get_supplier_shipping
    agent = PartsFinderAgent()

    # Batch fetch best supplier_part for all parts in one query (no N+1)
    part_ids = [part.id for part in parts]
    sp_batch = await db.execute(
        text("""
            SELECT DISTINCT ON (sp.part_id)
                sp.id AS sp_id, sp.part_id, sp.price_usd, sp.price_ils,
                sp.shipping_cost_usd, sp.shipping_cost_ils,
                sp.is_available, sp.warranty_months, sp.estimated_delivery_days,
                s.name AS supplier_name, s.country AS supplier_country
            FROM supplier_parts sp
            JOIN suppliers s ON sp.supplier_id = s.id
            WHERE sp.part_id = ANY(:pids) AND s.is_active = true
            ORDER BY sp.part_id, sp.is_available DESC, s.priority ASC
        """),
        {"pids": part_ids},
    )
    sp_map = {str(r.part_id): r for r in sp_batch.fetchall()}

    output = []
    for part in parts:
        sp_row = sp_map.get(str(part.id))
        pricing = None
        if sp_row:
            # Prefer stored ILS price (avoids exchange-rate round-trips)
            cost_ils = float(sp_row.price_ils or 0)
            ship_ils = float(sp_row.shipping_cost_ils or 0)
            delivery_fee = get_supplier_shipping(sp_row.supplier_name or "")
            if cost_ils > 0:
                pricing = agent.calculate_customer_price_from_ils(cost_ils, ship_ils, customer_shipping=delivery_fee)
            else:
                pricing = agent.calculate_customer_price(
                    float(sp_row.price_usd), float(sp_row.shipping_cost_usd or 0), customer_shipping=delivery_fee
                )
            pricing["availability"] = "in_stock" if sp_row.is_available else "on_order"
            pricing["warranty_months"] = sp_row.warranty_months
            pricing["estimated_delivery_days"] = sp_row.estimated_delivery_days
            pricing["supplier_part_id"] = str(sp_row.sp_id)

        output.append({
            "id": str(part.id),
            "sku": part.sku,
            "name": part.name,
            "manufacturer": part.manufacturer,
            "category": part.category,
            "part_type": part.part_type,
            "description": part.description,
            "compatible_vehicles": part.compatible_vehicles or [],
            "pricing": pricing,
        })

    return {
        "brand": brand.name if brand else brand_name,
        "brand_he": brand.name_he if brand else None,
        "total": total,
        "offset": offset,
        "limit": limit,
        "parts": output,
    }


# ─── Truck Brand Endpoints ────────────────────────────────────────────────────


@router.get("/api/v1/truck-brands")
async def get_truck_brands(
    region: Optional[str] = None,
    group: Optional[str] = None,
    q: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Return the truck_brands reference table with optional filters."""
    stmt = select(TruckBrand).where(TruckBrand.is_active == True)
    if region:
        stmt = stmt.where(TruckBrand.region == region)
    if group:
        stmt = stmt.where(TruckBrand.group_name.ilike(f"%{group}%"))
    if q:
        stmt = stmt.where(
            TruckBrand.name.ilike(f"%{q}%") | TruckBrand.name_he.ilike(f"%{q}%")
        )
    stmt = stmt.order_by(TruckBrand.name)
    result = await db.execute(stmt)
    brands = result.scalars().all()
    return {
        "brands": [
            {
                "id": str(b.id),
                "name": b.name,
                "name_he": b.name_he,
                "group_name": b.group_name,
                "country": b.country,
                "region": b.region,
                "website": b.website,
                "vehicle_type": "truck",
            }
            for b in brands
        ],
        "total": len(brands),
    }


@router.get("/api/v1/truck-brands/with-parts")
async def get_truck_brands_with_parts(db: AsyncSession = Depends(get_db)):
    """Return truck brands that have parts in parts_catalog, merged with registry info."""
    parts_result = await db.execute(
        select(PartsCatalog.manufacturer, func.count().label("parts_count"))
        .where(PartsCatalog.is_active == True)
        .group_by(PartsCatalog.manufacturer)
        .order_by(func.count().desc())
    )
    parts_by_mfr = {row[0]: row[1] for row in parts_result.fetchall() if row[0]}

    brand_result = await db.execute(
        select(TruckBrand).where(TruckBrand.is_active == True).order_by(TruckBrand.name)
    )
    all_brands = brand_result.scalars().all()

    merged = []
    seen_names: set[str] = set()
    for b in all_brands:
        count = 0
        for mfr_name in list(parts_by_mfr.keys()):
            if mfr_name and mfr_name.lower() == b.name.lower():
                count += parts_by_mfr[mfr_name]
                seen_names.add(mfr_name.lower())
        aliases = b.aliases or []
        for alias in aliases:
            for mfr_name in list(parts_by_mfr.keys()):
                if mfr_name and mfr_name.lower() == alias.lower():
                    count += parts_by_mfr[mfr_name]
                    seen_names.add(mfr_name.lower())
        seen_names.add(b.name.lower())
        merged.append({
            "name": b.name, "name_he": b.name_he,
            "group_name": b.group_name, "country": b.country,
            "region": b.region, "website": b.website,
            "parts_count": count, "has_parts": count > 0,
            "aliases": aliases, "vehicle_type": "truck",
        })

    for mfr_name, mfr_count in parts_by_mfr.items():
        if mfr_name and mfr_name.lower() not in seen_names:
            already = any(mfr_name.lower() in m["name"].lower() for m in merged)
            if not already:
                merged.append({
                    "name": mfr_name, "name_he": None, "group_name": None,
                    "country": None, "region": None, "website": None,
                    "parts_count": mfr_count, "has_parts": True, "vehicle_type": "truck",
                })

    return {"brands": merged, "total": len(merged)}


@router.get("/api/v1/truck-brands/{brand_name}/parts")
async def get_parts_by_truck_brand(
    brand_name: str,
    category: Optional[str] = None,
    part_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Return parts for a specific truck brand (by canonical name or alias)."""
    brand_result = await db.execute(
        select(TruckBrand).where(TruckBrand.is_active == True).where(
            or_(TruckBrand.name.ilike(brand_name), TruckBrand.name_he.ilike(brand_name))
        ).limit(1)
    )
    brand = brand_result.scalar_one_or_none()

    mfr_names: list[str] = [brand_name]
    if brand:
        mfr_names = [brand.name] + (brand.aliases or [])

    stmt = (
        select(PartsCatalog)
        .where(PartsCatalog.is_active == True)
        .where(or_(*[PartsCatalog.manufacturer.ilike(m) for m in mfr_names]))
    )
    if category:
        stmt = stmt.where(PartsCatalog.category.ilike(f"%{category}%"))
    if part_type:
        stmt = stmt.where(PartsCatalog.part_type == part_type)

    count_stmt = (
        select(func.count(PartsCatalog.id))
        .where(PartsCatalog.is_active == True)
        .where(or_(*[PartsCatalog.manufacturer.ilike(m) for m in mfr_names]))
    )
    if category:
        count_stmt = count_stmt.where(PartsCatalog.category.ilike(f"%{category}%"))
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()

    stmt = stmt.order_by(PartsCatalog.category, PartsCatalog.name).offset(offset).limit(limit)
    result = await db.execute(stmt)
    parts = result.scalars().all()

    if not parts:
        return {
            "brand": brand.name if brand else brand_name,
            "brand_he": brand.name_he if brand else None,
            "vehicle_type": "truck",
            "total": total, "offset": offset, "limit": limit, "parts": [],
        }

    from BACKEND_AI_AGENTS import PartsFinderAgent, get_supplier_shipping
    agent = PartsFinderAgent()

    part_ids = [part.id for part in parts]
    sp_batch = await db.execute(
        text("""
            SELECT DISTINCT ON (sp.part_id)
                sp.id AS sp_id, sp.part_id, sp.price_usd, sp.price_ils,
                sp.shipping_cost_usd, sp.shipping_cost_ils,
                sp.is_available, sp.warranty_months, sp.estimated_delivery_days,
                s.name AS supplier_name, s.country AS supplier_country
            FROM supplier_parts sp
            JOIN suppliers s ON sp.supplier_id = s.id
            WHERE sp.part_id = ANY(:pids) AND s.is_active = true
            ORDER BY sp.part_id, sp.is_available DESC, s.priority ASC
        """),
        {"pids": part_ids},
    )
    sp_map = {str(r.part_id): r for r in sp_batch.fetchall()}

    output = []
    for part in parts:
        sp_row = sp_map.get(str(part.id))
        pricing = None
        if sp_row:
            cost_ils = float(sp_row.price_ils or 0)
            ship_ils = float(sp_row.shipping_cost_ils or 0)
            delivery_fee = get_supplier_shipping(sp_row.supplier_name or "")
            if cost_ils > 0:
                pricing = agent.calculate_customer_price_from_ils(cost_ils, ship_ils, customer_shipping=delivery_fee)
            else:
                pricing = agent.calculate_customer_price(
                    float(sp_row.price_usd), float(sp_row.shipping_cost_usd or 0), customer_shipping=delivery_fee
                )
            pricing["availability"] = "in_stock" if sp_row.is_available else "on_order"
            pricing["warranty_months"] = sp_row.warranty_months
            pricing["estimated_delivery_days"] = sp_row.estimated_delivery_days
            pricing["supplier_part_id"] = str(sp_row.sp_id)

        output.append({
            "id": str(part.id),
            "sku": part.sku,
            "name": part.name,
            "manufacturer": part.manufacturer,
            "category": part.category,
            "part_type": part.part_type,
            "description": part.description,
            "compatible_vehicles": part.compatible_vehicles or [],
            "pricing": pricing,
        })

    return {
        "brand": brand.name if brand else brand_name,
        "brand_he": brand.name_he if brand else None,
        "vehicle_type": "truck",
        "total": total,
        "offset": offset,
        "limit": limit,
        "parts": output,
    }
