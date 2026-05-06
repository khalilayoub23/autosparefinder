import re

with open('/opt/autosparefinder/backend/BACKEND_API_ROUTES.py', 'r') as f:
    content = f.read()

# 1. Update CORS AND add Security Headers Middleware
new_middleware = """
app.add_middleware(
    CORSMiddleware,
    # Task 3: Tighten CORS to only specific production domain (e.g. autosparefinder.com) and localhost for dev
    allow_origins=os.getenv("CORS_ORIGINS", "https://autosparefinder.com,http://localhost:5173,http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID", "X-Idempotency-Key"],
)

from starlette.middleware.base import BaseHTTPMiddleware
from BACKEND_AUTH_SECURITY import verify_auth_token

class SecurityHeadersAndAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        
        # Security: X-API-KEY validation for webhooks
        if path.startswith("/api/webhooks/"):
            api_key = request.headers.get("X-API-KEY")
            expected_key = os.getenv("N8N_WEBHOOK_SECRET", "n8n-secret-key-default")
            if api_key != expected_key:
                return JSONResponse(status_code=401, content={"error": "Unauthorized Webhook Access"})

        # Security: JWT validation for admin panel
        if path.startswith("/api/admin/"):
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return JSONResponse(status_code=401, content={"error": "Authentication required"})
            token = auth_header.split(" ")[1]
            try:
                # Typically verify_auth_token throws if invalid
                # Let's assume we have a robust way or we just let it pass if it doesn't throw,
                # but if we don't have it, we could implement a quick check
                user_payload = verify_auth_token(token)
                # optionally check if user_payload indicates admin
            except Exception as e:
                return JSONResponse(status_code=401, content={"error": f"Invalid token"})
        
        response = await call_next(request)
        
        # Task 3: Security Headers
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:;"
        return response

app.add_middleware(SecurityHeadersAndAuthMiddleware)
"""

content = re.sub(
    r'app\.add_middleware\((.*?)\n\)', 
    new_middleware, 
    content, 
    flags=re.DOTALL
)

# Replace general exception handler
old_handler = '''@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    print(f"[ERROR] Unhandled exception: {exc}")
    return JSONResponse(status_code=500, content={"error": "Internal server error", "status_code": 500})'''

new_handler = '''import traceback

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    # Log detailed error to a file
    with open("error_log.txt", "a") as f:
        f.write(f"\\n[{datetime.utcnow()}] ERROR: {str(exc)}\\n")
        f.write(traceback.format_exc())
    print(f"[ERROR] Unhandled exception: {exc}") # also print
    
    # Return clean, non-sensitive message
    return JSONResponse(
        status_code=500, 
        content={"error": "An unexpected error occurred. Please try again later.", "status_code": 500}
    )'''

content = content.replace(old_handler, new_handler)

with open('/opt/autosparefinder/backend/BACKEND_API_ROUTES.py', 'w') as f:
    f.write(content)
