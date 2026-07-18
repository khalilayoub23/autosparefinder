import re
with open('/opt/autosparefinder/backend/BACKEND_API_ROUTES.py', 'r') as f:
    text = f.read()

text = text.replace('path = request.url.path', 'path = str(request.url.path)\n        print("DEBUG PATH:", path, flush=True)')

# fix the returning without setting security headers issue
text = text.replace('return JSONResponse(status_code=401, content={"error": "Authentication required"})', 
                     'resp = JSONResponse(status_code=401, content={"error": "Authentication required"}); resp.headers["X-Frame-Options"] = "DENY"; resp.headers["X-Content-Type-Options"] = "nosniff"; return resp')

text = text.replace('return JSONResponse(status_code=401, content={"error": "Invalid token"})', 
                     'resp = JSONResponse(status_code=401, content={"error": "Invalid token"}); resp.headers["X-Frame-Options"] = "DENY"; resp.headers["X-Content-Type-Options"] = "nosniff"; return resp')

text = text.replace('return JSONResponse(status_code=401, content={"error": "Unauthorized Webhook Access"})', 
                     'resp = JSONResponse(status_code=401, content={"error": "Unauthorized Webhook Access"}); resp.headers["X-Frame-Options"] = "DENY"; resp.headers["X-Content-Type-Options"] = "nosniff"; return resp')

with open('/opt/autosparefinder/backend/BACKEND_API_ROUTES.py', 'w') as f:
    f.write(text)
