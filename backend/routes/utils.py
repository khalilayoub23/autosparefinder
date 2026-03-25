"""Shared utilities for route modules, independent of BACKEND_API_ROUTES."""
import asyncio
import os
import io
import hashlib as _hashlib
import clamd as _clamd
from datetime import datetime

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from BACKEND_DATABASE_MODELS import User, OrderItem, Notification
from BACKEND_AUTH_SECURITY import publish_notification
from BACKEND_AI_AGENTS import OrdersAgent

# Cap fire-and-forget asyncio.create_task() fan-out.
_TASK_SEMAPHORE = asyncio.Semaphore(50)


async def _guarded_task(coro) -> None:
    """Acquire the shared semaphore before running a fire-and-forget coroutine."""
    async with _TASK_SEMAPHORE:
        await coro


def _scan_bytes_for_virus(content: bytes) -> tuple:
    """
    Scan raw bytes with ClamAV daemon.
    Returns: ('clean', None) | ('infected', '<VirusName>') | ('skipped', None)
    Tries Unix socket first, falls back to TCP, then skips gracefully.
    """
    for _make_scanner in (
        lambda: _clamd.ClamdUnixSocket(),
        lambda: _clamd.ClamdNetworkSocket(host=os.getenv("CLAMD_HOST", "clamav"), port=3310),
    ):
        try:
            scanner = _make_scanner()
            result = scanner.instream(io.BytesIO(content))
            status, virus_name = result.get("stream", ("skipped", None))
            return (status.lower(), virus_name)
        except Exception:
            continue
    # ClamAV daemon unavailable — skip scan (dev/CI without ClamAV)
    return ("skipped", None)


def _mask_supplier(name: str) -> str:
    """Return a deterministic numbered alias for supplier names."""
    if not name:
        return "ספק"
    digest = int(_hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)
    num = (digest % 9999) + 1
    return f"ספק #{num}"


def _get_frontend_url(request: Request) -> str:
    """Auto-detect frontend URL: Codespaces or localhost."""
    codespace = os.getenv("CODESPACE_NAME", "")
    if codespace:
        domain = os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
        return f"https://{codespace}-5173.{domain}"
    return os.getenv("FRONTEND_URL", "http://localhost:5173")


async def trigger_supplier_fulfillment(paid_orders: list, db: AsyncSession) -> None:
    """Run supplier-fulfillment flow only after payment confirmation."""
    agent = OrdersAgent()

    admins_res = await db.execute(select(User).where(User.is_admin == True))
    admins = admins_res.scalars().all()

    for order in paid_orders:
        items_res = await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
        order_items = items_res.scalars().all()

        if not order_items:
            print(f"[Fulfillment] No items for order {order.order_number} - marking processing for manual review")
            order.status = "processing"
            for admin in admins:
                _title = f"⚠️ {order.order_number} – אין נתוני ספק"
                _msg = f"ההזמנה {order.order_number} שולמה אך אין פריטים. טיפול ידני נדרש."
                db.add(Notification(
                    user_id=admin.id,
                    type="supplier_order",
                    title=_title,
                    message=_msg,
                    data={"order_id": str(order.id), "order_number": order.order_number, "needs_manual": True},
                ))
                asyncio.create_task(_guarded_task(publish_notification(str(admin.id), {"type": "supplier_order", "title": _title, "message": _msg})))
            continue

        by_supplier: dict = {}
        for oi in order_items:
            sup_name = oi.supplier_name or "ספק לא ידוע"
            if sup_name not in by_supplier:
                by_supplier[sup_name] = {"items": [], "total_cost_ils": 0.0}
            item_cost = float(oi.total_price or (oi.unit_price * oi.quantity))
            by_supplier[sup_name]["items"].append({
                "part_name": oi.part_name,
                "part_sku": oi.part_sku or "",
                "supplier_sku": "",
                "manufacturer": oi.manufacturer or "",
                "quantity": oi.quantity,
                "unit_cost_ils": float(oi.unit_price or 0),
                "shipping_ils": 0.0,
                "item_total_ils": round(item_cost, 2),
                "warranty_months": oi.warranty_months or 12,
                "availability": "In Stock",
                "estimated_delivery_days": 14,
            })
            by_supplier[sup_name]["total_cost_ils"] += item_cost

        _supplier_country_map = {
            "AutoParts Pro IL": "il",
            "Global Parts Hub": "de",
            "EastAuto Supply": "cn",
        }
        by_supplier_for_agent = {
            f"name:{k}": {
                "supplier": type("S", (), {
                    "id": f"name:{k}",
                    "name": k,
                    "website": "",
                    "country": _supplier_country_map.get(k, "il"),
                })(),
                "items": v["items"],
                "total_cost_ils": v["total_cost_ils"],
            }
            for k, v in by_supplier.items()
        }

        try:
            all_tracking = await agent.auto_fulfill_order(order, by_supplier_for_agent, db)
        except Exception as e:
            print(f"[Fulfillment] Agent fulfill error: {e}")
            all_tracking = []

        for sup_name, sup_data in by_supplier.items():
            sup_tracking = next((t for t in (all_tracking or []) if t.get("supplier") == sup_name), {})
            items_lines = "\n".join(
                f"  • {it['part_name']} ×{it['quantity']} — ₪{it['item_total_ils']:.2f}"
                for it in sup_data["items"]
            )
            for admin in admins:
                _title2 = f"🤖 הסוכן הזמין מ-{sup_name} עבור {order.order_number}"
                _msg2 = (
                    f"הסוכן ביצע הזמנה אוטומטית מ-{sup_name}.\n{items_lines}\n"
                    f"מספר מעקב: {sup_tracking.get('tracking_number', '—')} ({sup_tracking.get('carrier', '—')})"
                )
                db.add(Notification(
                    user_id=admin.id,
                    type="supplier_order",
                    title=_title2,
                    message=_msg2,
                    data={
                        "order_id": str(order.id),
                        "order_number": order.order_number,
                        "supplier_name": sup_name,
                        "items": sup_data["items"],
                        "total_cost_ils": round(sup_data["total_cost_ils"], 2),
                        "currency": "ILS",
                        "tracking_number": sup_tracking.get("tracking_number", ""),
                        "tracking_url": sup_tracking.get("tracking_url", ""),
                        "carrier": sup_tracking.get("carrier", ""),
                        "auto_fulfilled": True,
                    },
                    read_at=datetime.utcnow(),
                ))
                asyncio.create_task(_guarded_task(publish_notification(str(admin.id), {"type": "supplier_order", "title": _title2, "message": _msg2})))

        print(f"[Fulfillment] Order {order.order_number}: agent auto-fulfilled {len(by_supplier)} supplier(s) -> supplier_ordered")

    await db.flush()
