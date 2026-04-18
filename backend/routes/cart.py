"""
Cart & Wishlist — /api/v1/customers/cart  and  /api/v1/customers/wishlist
endpoints extracted from BACKEND_API_ROUTES.py.

Endpoints:
  GET    /api/v1/customers/cart
  POST   /api/v1/customers/cart/items
  DELETE /api/v1/customers/cart/items/{item_id}
  POST   /api/v1/customers/checkout
  GET    /api/v1/customers/wishlist
  POST   /api/v1/customers/wishlist
  DELETE /api/v1/customers/wishlist/{part_id}
"""
import uuid
import json
import hashlib
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, text

from BACKEND_DATABASE_MODELS import (
    get_db, get_pii_db,
    User, USD_TO_ILS,
)
from BACKEND_AUTH_SECURITY import get_current_user, get_current_verified_user
from BACKEND_AUTH_SECURITY import get_redis
from routes.schemas import (
    CartAddRequest,
    WishlistAddRequest,
    OrderCreate,
    OrderItemCreate,
)
from routes.utils import _mask_supplier

router = APIRouter()


def _normalize_checkout_address(address: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in (address or {}).items():
        if value is None:
            continue
        k = str(key).strip().lower()
        if not k:
            continue
        if isinstance(value, str):
            v = " ".join(value.strip().lower().split())
        else:
            v = str(value).strip().lower()
        normalized[k] = v
    return normalized


def _build_checkout_fingerprint(user_id: Any, order_payload: OrderCreate) -> str:
    items_signature: list[tuple[str, int]] = []
    for item in order_payload.items:
        if not item.supplier_part_id:
            continue
        items_signature.append((str(item.supplier_part_id), int(item.quantity or 0)))
    items_signature.sort(key=lambda row: (row[0], row[1]))

    payload = {
        "user_id": str(user_id),
        "shipping_address": _normalize_checkout_address(order_payload.shipping_address),
        "items": items_signature,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# ── Private helpers ───────────────────────────────────────────────────────────

async def _get_or_create_cart(user_id, db: AsyncSession):
    """Return the user's Cart row, creating one if it doesn't exist yet."""
    from BACKEND_DATABASE_MODELS import Cart
    result = await db.execute(select(Cart).where(Cart.user_id == user_id))
    cart = result.scalar_one_or_none()
    if not cart:
        cart = Cart(user_id=user_id)
        db.add(cart)
        await db.flush()
    return cart


async def _cart_to_response(items: list, cat_db: AsyncSession) -> list:
    """
    Convert CartItem ORM rows → camelCase dicts matching the mobile cartStore.ts CartItem shape:
        id, partId, name, price, quantity, imageUrl, supplierId, supplierName, stockAvailable
    Fetches part + supplier details from the catalog DB in a single JOIN query.
    """
    from BACKEND_DATABASE_MODELS import SupplierPart, PartsCatalog, Supplier as SupplierModel, PartImage

    if not items:
        return []

    sp_ids = [i.supplier_part_id for i in items]
    rows = await cat_db.execute(
        select(SupplierPart, PartsCatalog, SupplierModel)
        .join(PartsCatalog, SupplierPart.part_id == PartsCatalog.id)
        .join(SupplierModel, SupplierPart.supplier_id == SupplierModel.id)
        .where(SupplierPart.id.in_(sp_ids))
    )
    catalog: dict = {str(r.SupplierPart.id): r for r in rows}

    # Fetch primary images for all parts in one query
    part_ids = [r[1].id for r in catalog.values()]
    img_res = await cat_db.execute(
        select(PartImage)
        .where(and_(PartImage.part_id.in_(part_ids), PartImage.is_primary == True))
    )
    images: dict = {str(r.part_id): r.url for r in img_res.scalars()}

    result = []
    for item in items:
        row = catalog.get(str(item.supplier_part_id))
        if not row:  # supplier_part deleted from catalog — skip silently
            continue
        sp, part, supplier = row[0], row[1], row[2]
        result.append({
            "id":             str(item.id),
            "partId":         str(item.part_id),
            "supplierPartId": str(item.supplier_part_id),
            "name":           part.name,
            "price":          float(item.unit_price),
            "quantity":       item.quantity,
            "imageUrl":       images.get(str(part.id)),
            "supplierId":     str(sp.supplier_id),
            "supplierName":   _mask_supplier(supplier.name),
            "stockAvailable": sp.stock_quantity if sp.stock_quantity is not None else 99,
        })
    return result


async def _wishlist_item_to_response(item, cat_db: AsyncSession) -> dict:
    """Resolve part details from catalog DB for a single WishlistItem row."""
    from BACKEND_DATABASE_MODELS import PartsCatalog, PartImage
    part_res = await cat_db.execute(
        select(PartsCatalog).where(PartsCatalog.id == item.part_id)
    )
    part = part_res.scalar_one_or_none()
    if not part:
        return None

    img_res = await cat_db.execute(
        select(PartImage).where(
            and_(PartImage.part_id == part.id, PartImage.is_primary == True)
        ).limit(1)
    )
    img = img_res.scalar_one_or_none()

    return {
        "id":           str(item.id),
        "partId":       str(item.part_id),
        "name":         part.name,
        "category":     part.category,
        "manufacturer": part.manufacturer,
        "price":        float(part.min_price_ils or part.base_price or 0),
        "imageUrl":     img.url if img else None,
        "addedAt":      item.added_at.isoformat(),
    }


# ── Cart endpoints ────────────────────────────────────────────────────────────

@router.get("/api/v1/customers/cart")
async def get_cart(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db),
):
    from BACKEND_DATABASE_MODELS import Cart, CartItem as CartItemModel
    cart = await _get_or_create_cart(current_user.id, db)
    result = await db.execute(
        select(CartItemModel).where(CartItemModel.cart_id == cart.id)
    )
    items = result.scalars().all()
    return {"items": await _cart_to_response(items, cat_db)}


@router.post("/api/v1/customers/cart/items", status_code=status.HTTP_201_CREATED)
async def add_cart_item(
    data: CartAddRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db),
):
    from BACKEND_DATABASE_MODELS import Cart, CartItem as CartItemModel, SupplierPart

    # Resolve cheapest available supplier_part for the given catalog part
    sp_res = await cat_db.execute(
        select(SupplierPart)
        .where(
            and_(
                SupplierPart.part_id == data.part_id,
                SupplierPart.is_available == True,
            )
        )
        .order_by(SupplierPart.price_ils.asc().nullslast())
        .limit(1)
    )
    sp = sp_res.scalar_one_or_none()
    if not sp:
        raise HTTPException(status_code=404, detail="Part not available from any supplier")

    unit_price = float(sp.price_ils or 0) or (float(sp.price_usd or 0) * USD_TO_ILS)
    cart = await _get_or_create_cart(current_user.id, db)

    # Compatible upsert: update existing row if found, otherwise insert.
    # Avoids reliance on a DB-level constraint name that may not exist in older deployments.
    existing_res = await db.execute(
        select(CartItemModel).where(
            and_(
                CartItemModel.cart_id == cart.id,
                CartItemModel.supplier_part_id == sp.id,
            )
        )
    )
    existing_item = existing_res.scalar_one_or_none()
    if existing_item:
        existing_item.quantity += data.quantity
        existing_item.unit_price = round(unit_price, 2)
        existing_item.updated_at = datetime.utcnow()
    else:
        db.add(
            CartItemModel(
                cart_id=cart.id,
                part_id=uuid.UUID(str(data.part_id)),
                supplier_part_id=sp.id,
                quantity=data.quantity,
                unit_price=round(unit_price, 2),
            )
        )
    await db.execute(
        text("UPDATE carts SET updated_at = now() WHERE id = :cid"),
        {"cid": str(cart.id)},
    )
    await db.flush()

    result = await db.execute(
        select(CartItemModel).where(CartItemModel.cart_id == cart.id)
    )
    items = result.scalars().all()
    return {"items": await _cart_to_response(items, cat_db)}


@router.delete("/api/v1/customers/cart/items/{item_id}")
async def remove_cart_item(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db),
):
    from BACKEND_DATABASE_MODELS import Cart, CartItem as CartItemModel

    cart = await _get_or_create_cart(current_user.id, db)
    res = await db.execute(
        select(CartItemModel).where(
            and_(CartItemModel.id == item_id, CartItemModel.cart_id == cart.id)
        )
    )
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Cart item not found")
    await db.delete(item)
    await db.execute(
        text("UPDATE carts SET updated_at = now() WHERE id = :cid"),
        {"cid": str(cart.id)},
    )
    await db.flush()

    result = await db.execute(
        select(CartItemModel).where(CartItemModel.cart_id == cart.id)
    )
    items = result.scalars().all()
    return {"items": await _cart_to_response(items, cat_db)}


@router.post("/api/v1/customers/checkout", status_code=status.HTTP_201_CREATED)
async def checkout(
    payload: dict,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """
    Convert the user's server-side cart into an Order, then empty the cart.
    Delegates all pricing / OrderItem creation to the existing create_order logic.
    Supports partial checkout via optional selected_supplier_part_ids.
    """
    from BACKEND_DATABASE_MODELS import Cart, CartItem as CartItemModel
    from routes.orders import create_order, _find_recent_duplicate_pending_order

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Invalid checkout payload")

    # Backward-compatible body support:
    # 1) old clients send shipping_address object directly
    # 2) new clients send { shipping_address: {...}, selected_supplier_part_ids: [...] }
    if isinstance(payload.get("shipping_address"), dict):
        shipping_address: dict[str, Any] = payload.get("shipping_address") or {}
    else:
        shipping_address = payload

    if not isinstance(shipping_address, dict):
        raise HTTPException(status_code=422, detail="Invalid shipping address")

    raw_selected = payload.get("selected_supplier_part_ids") if isinstance(payload.get("selected_supplier_part_ids"), list) else None
    selected_supplier_part_ids: set[str] = set()
    if raw_selected is not None:
        for raw in raw_selected:
            try:
                selected_supplier_part_ids.add(str(uuid.UUID(str(raw))))
            except Exception:
                continue
        if not selected_supplier_part_ids:
            raise HTTPException(status_code=400, detail="לא נבחרו פריטים לתשלום")

    cart_res = await db.execute(
        select(Cart).where(Cart.user_id == current_user.id)
    )
    cart = cart_res.scalar_one_or_none()
    if not cart:
        raise HTTPException(status_code=400, detail="Cart is empty")

    items_res = await db.execute(
        select(CartItemModel).where(CartItemModel.cart_id == cart.id)
    )
    cart_items = items_res.scalars().all()
    if not cart_items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    selected_cart_items = cart_items
    if selected_supplier_part_ids:
        selected_cart_items = [
            ci for ci in cart_items
            if str(ci.supplier_part_id) in selected_supplier_part_ids
        ]
        if not selected_cart_items:
            raise HTTPException(status_code=400, detail="הפריטים שנבחרו אינם נמצאים בסל")

    # Build the same OrderCreate payload the existing endpoint expects
    order_payload = OrderCreate(
        items=[
            OrderItemCreate(
                part_id=str(ci.part_id),
                supplier_part_id=str(ci.supplier_part_id),
                quantity=ci.quantity,
            )
            for ci in selected_cart_items
        ],
        shipping_address=shipping_address,
    )

    redis_lock_key = None
    redis_lock_token = None
    redis_lock_acquired = False

    if redis is not None:
        redis_lock_key = f"checkout:dedupe:{_build_checkout_fingerprint(current_user.id, order_payload)}"
        redis_lock_token = str(uuid.uuid4())
        try:
            redis_lock_acquired = bool(
                await redis.set(redis_lock_key, redis_lock_token, nx=True, ex=120)
            )
        except Exception:
            redis_lock_acquired = False

        if not redis_lock_acquired:
            duplicate_order = await _find_recent_duplicate_pending_order(order_payload, current_user, db)
            if duplicate_order:
                return {
                    "order_id": str(duplicate_order.id),
                    "order_number": duplicate_order.order_number,
                    "status": duplicate_order.status,
                    "subtotal": float(duplicate_order.subtotal),
                    "vat": float(duplicate_order.vat_amount),
                    "shipping": float(duplicate_order.shipping_cost),
                    "total": float(duplicate_order.total_amount),
                    "deduplicated": True,
                }
            raise HTTPException(status_code=409, detail="Checkout already in progress. Please wait and retry.")

    try:
        # Delegate to the existing create_order function — no logic duplication
        order_result = await create_order(
            data=order_payload,
            current_user=current_user,
            cat_db=cat_db,
            db=db,
            redis=redis,
        )

        # Clear only checked-out items on success.
        # If no explicit selection was sent, this removes all items (legacy behavior).
        for ci in selected_cart_items:
            await db.delete(ci)
        await db.flush()

        await db.execute(
            text("UPDATE carts SET updated_at = now() WHERE id = :cid"),
            {"cid": str(cart.id)},
        )

        return order_result
    finally:
        if redis is not None and redis_lock_key and redis_lock_token and redis_lock_acquired:
            try:
                current_lock_value = await redis.get(redis_lock_key)
                if current_lock_value == redis_lock_token:
                    await redis.delete(redis_lock_key)
            except Exception:
                pass


# ── Wishlist endpoints ────────────────────────────────────────────────────────

@router.get("/api/v1/customers/wishlist")
async def get_wishlist(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db),
):
    from BACKEND_DATABASE_MODELS import WishlistItem
    res = await db.execute(
        select(WishlistItem)
        .where(WishlistItem.user_id == current_user.id)
        .order_by(WishlistItem.added_at.desc())
    )
    items = res.scalars().all()
    out = []
    for item in items:
        row = await _wishlist_item_to_response(item, cat_db)
        if row:
            out.append(row)
    return {"items": out, "count": len(out)}


@router.post("/api/v1/customers/wishlist", status_code=status.HTTP_201_CREATED)
async def add_to_wishlist(
    body: WishlistAddRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db),
):
    from BACKEND_DATABASE_MODELS import WishlistItem
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    try:
        part_uuid = uuid.UUID(body.part_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid part_id")

    stmt = pg_insert(WishlistItem).values(
        user_id=current_user.id,
        part_id=part_uuid,
    ).on_conflict_do_nothing(constraint="uq_wishlist_item")
    await db.execute(stmt)
    await db.commit()

    res = await db.execute(
        select(WishlistItem).where(
            WishlistItem.user_id == current_user.id,
            WishlistItem.part_id == part_uuid,
        )
    )
    item = res.scalar_one()
    return await _wishlist_item_to_response(item, cat_db)


@router.delete("/api/v1/customers/wishlist/{part_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_wishlist(
    part_id: str,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_pii_db),
):
    from BACKEND_DATABASE_MODELS import WishlistItem

    try:
        part_uuid = uuid.UUID(part_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid part_id")

    res = await db.execute(
        select(WishlistItem).where(
            WishlistItem.user_id == current_user.id,
            WishlistItem.part_id == part_uuid,
        )
    )
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not in wishlist")
    await db.delete(item)
    await db.commit()
