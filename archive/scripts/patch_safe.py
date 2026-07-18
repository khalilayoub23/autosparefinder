import re
from datetime import datetime

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
        if path.startswith("/api/admin/"):
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

# Find app = FastAPI(...) block and place the middleware right after.
# Also update the CORSMiddleware
content = re.sub(
    r'(app\.add_middleware\(\s*CORSMiddleware,\s*allow_origins=)([^,]+)(,)',
    r'\1os.getenv("CORS_ORIGINS", "https://autosparefinder.com,http://localhost:5173,http://localhost:3000").split(",")\3',
    content
)

# Insert custom middleware after CORSMiddleware
content = re.sub(
    r'(allow_headers=\["Content-Type", "Authorization", "X-Request-ID", "X-Idempotency-Key"\],\n\))',
    r'\1\n\n' + middleware_code,
    content
)

# 2. Update general exception handler
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

if old_handler in content:
    content = content.replace(old_handler, new_handler)
else:
    print("Could not find old exception handler")

with open('/opt/autosparefinder/backend/BACKEND_API_ROUTES.py', 'w') as f:
    f.write(content)
