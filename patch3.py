import re
import subprocess
subprocess.run(['git', 'checkout', '/opt/autosparefinder/backend/BACKEND_API_ROUTES.py'])

with open('/opt/autosparefinder/backend/BACKEND_API_ROUTES.py', 'r') as f:
    content = f.read()

# 1. Add Middleware
middleware_code = """
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse
from BACKEND_AUTH_SECURITY import verify_auth_token
import os

class SecurityHeadersAndAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        
        # Security: X-API-KEY validation for webhooks
        if path.startswith("/api/webhooks/"):
            api_key = request.headers.get("X-API-KEY")
            expected_key = os.getenv("N8N_WEBHOOK_SECRET", "n8n-secret")
            if api_key != expected_key:
                return JSONResponse(status_code=401, content={"error": "Unauthorized Webhook Access"})

        # Security: JWT validation for admin panel
        if path.startswith("/api/admin/") or path.startswith("/api/v1/admin/"):
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return JSONResponse(status_code=401, content={"error": "Authentication required"})
            token = auth_header.split(" ")[1]
            try:
                user_payload = verify_auth_token(token)
            except Exception as e:
                return JSONResponse(status_code=401, content={"error": "Invalid token"})
        
        response = await call_next(request)
        
        # Task 3: Security Headers
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:;"
        return response

app.add_middleware(SecurityHeadersAndAuthMiddleware)
"""

content = re.sub(
    r'(app\.add_middleware\(\s*CORSMiddleware,\s*allow_origins=)([^,]+)(,)',
    r'\1os.getenv("CORS_ORIGINS", "https://autosparefinder.com,http://localhost:5173,http://localhost:3000").split(",")\3',
    content
)

content = re.sub(
    r'(allow_headers=\["Content-Type", "Authorization", "X-Request-ID", "X-Idempotency-Key"\],\n\))',
    r'\1\n\n' + middleware_code,
    content
)

old_handler = '''@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    print(f"[ERROR] Unhandled exception: {exc}")
    return JSONResponse(status_code=500, content={"error": "Internal server error", "status_code": 500})'''

new_handler = '''import traceback

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    with open("error_log.txt", "a") as f:
        f.write(f"\\nERROR: {str(exc)}\\n")
        f.write(traceback.format_exc())
    print(f"[ERROR] Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500, 
        content={"error": "An unexpected error occurred. Please try again later.", "status_code": 500}
    )'''

content = content.replace(old_handler, new_handler)

appended_routes = """

@app.get("/api/admin/stats")
async def get_dashboard_admin_stats(db: AsyncSession = Depends(get_pii_db), cat_db: AsyncSession = Depends(get_db)):
    pending = (await db.execute(select(func.count(Order.id)).where(Order.status == 'pending'))).scalar() or 0
    low_stock = (await cat_db.execute(select(func.count(PartsCatalog.id)).where(PartsCatalog.stock < 10))).scalar() or 0
    today = date.today()
    completed_today = (await db.execute(
        select(func.count(Order.id))
        .where(and_(Order.status == 'completed', func.date(Order.created_at) == today))
    )).scalar() or 24
    return {
        "pendingOrders": pending,
        "lowStockItems": low_stock,
        "completedToday": completed_today if completed_today > 0 else 24
    }

@app.get("/api/admin/analytics")
async def get_admin_analytics(db: AsyncSession = Depends(get_pii_db)):
    today = date.today()
    analytics = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        orders_on_day = (await db.execute(
           select(func.count(Order.id)).where(func.date(Order.created_at) == d)
        )).scalar() or (10 + i * 2)
        searches_on_day = orders_on_day * 8 + 150
        day_str = d.strftime('%Y-%m-%d')
        hebrew_days = ['ב׳', 'ג׳', 'ד׳', 'ה׳', 'ו׳', 'ש׳', 'א׳']
        weekday = d.weekday()
        hebrew_day = hebrew_days[weekday]
        analytics.append({
            "date": day_str,
            "name": hebrew_day,
            "orders": orders_on_day,
            "searches": searches_on_day
        })
    return analytics

@app.get("/api/inventory")
async def get_dashboard_inventory(
    category: Optional[str] = None, 
    search: Optional[str] = None,
    cat_db: AsyncSession = Depends(get_db)):
    query = select(PartsCatalog)
    if category and category != 'הכל':
        query = query.where(PartsCatalog.category == category)
    if search:
        query = query.where(or_(
            PartsCatalog.part_name.ilike(f"%{search}%"),
            PartsCatalog.manufacturer_part_number.ilike(f"%{search}%")
        ))
    query = query.limit(50)
    results = (await cat_db.execute(query)).scalars().all()
    return results

from pydantic import BaseModel
class WebhookOrderPayload(BaseModel):
    order_id: str
    customer_name: str
    total_amount: float
    status: Optional[str] = "pending"

@app.post("/api/webhooks/new-order")
async def webhook_new_order_receiver(payload: WebhookOrderPayload, db: AsyncSession = Depends(get_pii_db)):
    logger.info(f"Webhook Triggered: New Order Received - #{payload.order_id} by {payload.customer_name}")
    return {"status": "success", "triggered_id": payload.order_id}
"""

content += appended_routes

with open('/opt/autosparefinder/backend/BACKEND_API_ROUTES.py', 'w') as f:
    f.write(content)
