"""Create a fake supplier + supplier part for full payment-cycle testing.

Usage:
  python create_fake_supplier_for_cycle.py

Environment overrides:
  FAKE_SUPPLIER_NAME
  FAKE_SUPPLIER_PAYMENT_METHOD
"""

import asyncio
import os
from decimal import Decimal

from sqlalchemy import select

from BACKEND_DATABASE_MODELS import async_session_factory, Supplier, SupplierPart, PartsCatalog


async def main() -> None:
    supplier_name = (os.getenv("FAKE_SUPPLIER_NAME", "Sandbox Supplier QA") or "Sandbox Supplier QA").strip()
    supplier_payment_method = (os.getenv("FAKE_SUPPLIER_PAYMENT_METHOD", "pm_card_visa") or "pm_card_visa").strip()

    async with async_session_factory() as db:
        supplier_res = await db.execute(select(Supplier).where(Supplier.name == supplier_name))
        supplier = supplier_res.scalar_one_or_none()

        if not supplier:
            supplier = Supplier(
                name=supplier_name,
                country="IL",
                website="https://example.invalid/sandbox-supplier",
                is_active=True,
                reliability_score=Decimal("0.95"),
                credentials={
                    "stripe_test_payment_method": supplier_payment_method,
                    "auto_fake_tracking": True,
                },
            )
            db.add(supplier)
            await db.flush()

        part_res = await db.execute(
            select(PartsCatalog.id)
            .where(PartsCatalog.is_active == True)
            .order_by(PartsCatalog.updated_at.desc().nullslast(), PartsCatalog.created_at.desc())
            .limit(1)
        )
        part_row = part_res.first()
        if not part_row:
            raise RuntimeError("No active parts found in parts_catalog. Seed catalog first.")
        part_id = part_row[0]

        sp_res = await db.execute(
            select(SupplierPart).where(
                SupplierPart.supplier_id == supplier.id,
                SupplierPart.part_id == part_id,
            )
        )
        supplier_part = sp_res.scalar_one_or_none()

        if not supplier_part:
            supplier_part = SupplierPart(
                supplier_id=supplier.id,
                part_id=part_id,
                supplier_sku=f"QA-{str(part_id).split('-')[0].upper()}",
                price_usd=Decimal("25.00"),
                price_ils=Decimal("92.00"),
                shipping_cost_usd=Decimal("8.00"),
                shipping_cost_ils=Decimal("29.00"),
                availability="In Stock",
                warranty_months=12,
                estimated_delivery_days=7,
                is_available=True,
                stock_quantity=20,
            )
            db.add(supplier_part)
            await db.flush()

        await db.commit()

        print("Fake supplier cycle data is ready")
        print(f"supplier_id={supplier.id}")
        print(f"supplier_name={supplier.name}")
        print(f"supplier_part_id={supplier_part.id}")
        print(f"part_id={part_id}")
        print(f"stripe_test_payment_method={supplier_payment_method}")


if __name__ == "__main__":
    asyncio.run(main())
