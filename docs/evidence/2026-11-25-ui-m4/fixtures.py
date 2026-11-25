"""Fixtures for the mission-4 UI audit. Every payload follows the REAL response shape of the production route named next
to it (backend/routes/cart.py::_cart_to_response, orders.py::get_orders/get_order, admin.py::get_all_orders_admin /
get_admin_users / get_admin_suppliers / get_admin_supplier_orders). `test_ui_fixture_contract.py` re-derives those key sets
from the backend source so a drift fails a test. They are SYNTHETIC (no real customer, order or supplier data)."""

CUSTOMER_USER = {"id": "00000000-0000-4000-8000-0000000000c1", "email": "customer@example.test", "full_name": "לקוח בדיקה",
                 "phone": "0500000000", "is_admin": False, "role": "customer", "is_verified": True,
                 "terms_accepted_at": "2026-01-01T00:00:00Z"}
ADMIN_USER = dict(CUSTOMER_USER, id="00000000-0000-4000-8000-000000000001", email="admin@example.test", full_name="מנהל בדיקות",
                  is_admin=True, role="admin")
PROFILE = {"profile": {"address": "הרצל 55", "city": "עכו", "postal_code": "24100"}}

LONG_ORDER = "ASF-20261124-7F3A9C1E5B2D48A6-REPLACEMENT-0001"   # worst case: long, unbroken identifier

CART = {"items": [
    {"id": "10000000-0000-4000-8000-000000000001", "partId": "20000000-0000-4000-8000-000000000001",
     "supplierPartId": "30000000-0000-4000-8000-000000000001", "name": "רפידות בלם קדמיות Toyota Corolla 2017-2019 (סט מלא, כולל חיישן שחיקה)",
     "price": 358.0, "quantity": 2, "imageUrl": None, "supplierId": "40000000-0000-4000-8000-000000000001", "supplierName": "ספק מקומי",
     "offerPartType": "oem", "isIlSupplier": True, "stockAvailable": 5, "vatIncluded": True, "deliveryFee": 29.0, "shippingCost": 29.0},
    {"id": "10000000-0000-4000-8000-000000000002", "partId": "20000000-0000-4000-8000-000000000002",
     "supplierPartId": "30000000-0000-4000-8000-000000000002", "name": "מסנן שמן MANN W 712/94",
     "price": 47.5, "quantity": 1, "imageUrl": None, "supplierId": "40000000-0000-4000-8000-000000000001", "supplierName": "ספק מקומי",
     "offerPartType": "aftermarket", "isIlSupplier": True, "stockAvailable": 99, "vatIncluded": True, "deliveryFee": 29.0, "shippingCost": 0.0},
    {"id": "10000000-0000-4000-8000-000000000003", "partId": "20000000-0000-4000-8000-000000000003",
     "supplierPartId": "30000000-0000-4000-8000-000000000003", "name": "Wheel Bearing Kit Front Axle 43202-1KK0A",
     "price": 214.9, "quantity": 1, "imageUrl": None, "supplierId": "40000000-0000-4000-8000-000000000002", "supplierName": "ספק בינלאומי",
     "offerPartType": "oe_equivalent", "isIlSupplier": False, "stockAvailable": 3, "vatIncluded": True, "deliveryFee": 64.0, "shippingCost": 64.0},
]}

ORDERS = {"orders": [
    {"id": "50000000-0000-4000-8000-000000000001", "order_number": LONG_ORDER, "status": "shipped", "total": 1138.5,
     "created_at": "2026-11-20T10:00:00", "tracking_number": "RR123456789IL", "tracking_url": "https://example.test/track/RR123456789IL",
     "estimated_delivery": "2026-11-28T00:00:00"},
    {"id": "50000000-0000-4000-8000-000000000002", "order_number": "ASF-20261121-0002", "status": "pending_payment", "total": 294.2,
     "created_at": "2026-11-21T09:12:00", "tracking_number": None, "tracking_url": None, "estimated_delivery": None},
    {"id": "50000000-0000-4000-8000-000000000003", "order_number": "ASF-20261110-0003", "status": "delivered", "total": 89.0,
     "created_at": "2026-11-10T16:40:00", "tracking_number": "RR987654321IL", "tracking_url": None, "estimated_delivery": "2026-11-15T00:00:00"},
    {"id": "50000000-0000-4000-8000-000000000004", "order_number": "ASF-20261101-0004", "status": "cancelled", "total": 530.0,
     "created_at": "2026-11-01T08:00:00", "tracking_number": None, "tracking_url": None, "estimated_delivery": None},
]}

ORDER_DETAIL = {"id": "50000000-0000-4000-8000-000000000001", "order_number": LONG_ORDER, "status": "shipped", "subtotal": 964.83,
                "vat": 173.67, "shipping": 29.0, "total": 1167.5, "tracking_number": "RR123456789IL",
                "tracking_url": "https://example.test/track/RR123456789IL", "estimated_delivery": "2026-11-28T00:00:00",
                "items": [
                    {"part_id": "20000000-0000-4000-8000-000000000001", "supplier_part_id": "30000000-0000-4000-8000-000000000001",
                     "part_name": "רפידות בלם קדמיות Toyota Corolla 2017-2019 (סט מלא, כולל חיישן שחיקה)", "manufacturer": "Toyota",
                     "quantity": 2, "unit_price": 303.39, "total": 606.78},
                    {"part_id": "20000000-0000-4000-8000-000000000002", "supplier_part_id": "30000000-0000-4000-8000-000000000002",
                     "part_name": "מסנן שמן MANN W 712/94", "manufacturer": "MANN", "quantity": 1, "unit_price": 40.25, "total": 40.25}]}

_STATUSES = ["pending_payment", "paid", "processing", "shipped", "delivered", "cancelled"]
ADMIN_ORDERS = {"orders": [
    {"id": f"60000000-0000-4000-8000-{i:012d}", "order_number": (LONG_ORDER if i == 1 else f"ASF-202611{20 - i % 19:02d}-{i:04d}"),
     "status": _STATUSES[i % 6], "total": round(89 + i * 37.35, 2), "created_at": f"2026-11-{20 - i % 19:02d}T1{i % 10}:00:00",
     "user_email": f"customer{i}@example.test", "user_name": ["דוד כהן", "שרה לוי", "Ahmad Khalil", "Maria Petrova", "יוסי מזרחי"][i % 5]}
    for i in range(1, 13)]}
ADMIN_USERS = {"users": [
    {"id": f"70000000-0000-4000-8000-{i:012d}", "email": f"user{i}@example.test", "full_name": ["דוד כהן", "שרה לוי", "Ahmad Khalil", "Maria Petrova", "יוסי מזרחי"][i % 5],
     "phone": f"05000000{i:02d}", "is_verified": i % 4 != 0, "is_admin": i == 1, "is_active": i % 7 != 0, "role": "admin" if i == 1 else "customer",
     "failed_login_count": i % 3, "locked_until": None, "created_at": f"2026-10-{i + 1:02d}T09:00:00"} for i in range(1, 11)]}
ADMIN_SUPPLIERS = {"suppliers": [
    {"id": f"80000000-0000-4000-8000-{i:012d}", "name": n, "country": c, "website": f"https://supplier{i}.example.test", "api_endpoint": None,
     "contact_email": f"sales@supplier{i}.example.test", "contact_phone": "03-0000000", "is_active": i != 3, "priority": i,
     "reliability_score": round(0.6 + i * 0.05, 2), "supports_express": i % 2 == 0, "express_carrier": "DHL" if i % 2 == 0 else None,
     "express_base_cost_usd": 18.0 if i % 2 == 0 else None, "avg_delivery_days_actual": 3.5 + i, "shipping_info": {}, "return_policy": {},
     "created_at": "2026-01-01T00:00:00"} for i, (n, c) in enumerate([("ספק מקומי א׳", "IL"), ("Euro Parts Ltd", "IE"), ("Global Auto Supply", "US"),
                                                                     ("ספק מקומי ב׳", "IL")], 1)]}
ADMIN_SUPPLIER_ORDERS = {"supplier_orders": [
    {"id": f"90000000-0000-4000-8000-{i:012d}", "title": f"הזמנה מספק — {LONG_ORDER if i == 1 else 'ASF-2026112' + str(i)}",
     "message": "יש להזמין 2 יח׳ רפידות בלם מהספק", "data": {"order_number": "ASF-20261124-0001"}, "is_done": i == 3, "done_at": None,
     "created_at": "2026-11-24T10:00:00"} for i in range(1, 4)], "pending_count": 2}
ADMIN_STATS = {"users": 128, "orders": 342, "parts": 4180000, "pending_orders": 12, "orders_by_status": {"paid": 40, "shipped": 20, "delivered": 250, "pending_payment": 12, "cancelled": 20},
               "gross_revenue": 184250.5, "revenue": 171000.0, "refunds": 3200.0}


def api_mock(path: str, method: str, body: str = ""):
    """(status, json) for an /api/ path. Unlisted GETs return an empty collection; every write is acknowledged, nothing persists."""
    p = path.split("?")[0].strip("/")
    if p.startswith("v1/"):
        p = p[3:]
    if method != "GET":
        if p == "customers/checkout":
            return 200, {"order_id": ORDERS["orders"][1]["id"], "order_number": "ASF-20261121-0002", "total": 294.2}
        if p.startswith("payments/create-checkout"):
            return 200, {"checkout_url": "http://127.0.0.1:4173/__mock_checkout__"}
        if p.startswith("customers/cart"):
            return 200, CART
        return 200, {}
    if p in ("auth/me", "customers/me", "users/me"):
        return 200, MOCK_STATE["user"]
    if p == "profile":
        return 200, PROFILE
    if p == "customers/cart":
        return 200, CART
    if p == "orders":
        return 200, ORDERS
    if p.startswith("orders/") and p.endswith("/track"):
        return 200, {k: ORDER_DETAIL[k] for k in ("order_number", "status", "tracking_number", "tracking_url", "estimated_delivery")}
    if p.startswith("orders/"):
        return 200, ORDER_DETAIL
    if p == "admin/orders":
        return 200, ADMIN_ORDERS
    if p == "admin/users":
        return 200, ADMIN_USERS
    if p == "admin/suppliers":
        return 200, ADMIN_SUPPLIERS
    if p == "admin/supplier-orders":
        return 200, ADMIN_SUPPLIER_ORDERS
    if p == "admin/stats":
        return 200, ADMIN_STATS
    if p == "admin/returns":
        return 200, {"returns": []}
    return 200, {"items": [], "results": [], "total": 0}


MOCK_STATE = {"user": CUSTOMER_USER}
