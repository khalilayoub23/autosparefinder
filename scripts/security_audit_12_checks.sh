#!/usr/bin/env bash
set +e

cd /workspaces/autosparefinder || exit 1

echo '=== 1. SQL Injection ==='
grep -rn "text(f[\"']\|execute(f[\"']\|format.*sql\|%.*WHERE\|+.*WHERE" backend/*.py | grep -v '#' | head -20

echo '=== 2. Unprotected endpoints ==='
python3 -c "
import re
content = open('backend/BACKEND_API_ROUTES.py').read()
endpoints = re.findall(r'@app\.(get|post|put|delete|patch)\([\"\'](/[^\"\']+)', content)
funcs = re.findall(r'async def (\w+)\([^)]*(?:Depends\(get_current[^)]*\))[^)]*\)', content)
print('Total endpoints:', len(endpoints))
" 2>/dev/null
grep -c 'get_current_user\|get_current_admin\|get_current_verified' backend/BACKEND_API_ROUTES.py

echo '=== 3. Hardcoded secrets ==='
grep -rn 'secret\|password\|api_key\|token' backend/*.py | grep -v 'os.getenv\|os.environ\|getenv\|#\|test\|example\|CHANGE_ME\|YOUR_\|placeholder' | grep '=.*["\x27][A-Za-z0-9+/]\{8,\}' | head -20

echo '=== 4. Path traversal ==='
grep -rn 'open(\|os.path.join\|os.path\|Path(' backend/*.py | grep -v '#\|BACKUP_DIR\|__file__\|test' | head -20

echo '=== 5. CORS configuration ==='
grep -n 'CORS\|allow_origins\|allow_methods\|allow_headers' backend/BACKEND_API_ROUTES.py | head -10

echo '=== 6. JWT security ==='
grep -n 'algorithm\|JWT_SECRET\|HS256\|RS256\|verify\|decode' backend/BACKEND_AUTH_SECURITY.py | head -15

echo '=== 7. Rate limiting coverage — ALL public endpoints ==='
grep -n '@app\.\(get\|post\|put\|delete\)' backend/BACKEND_API_ROUTES.py | grep -v 'get_current\|admin' | wc -l
grep -n 'check_rate_limit' backend/BACKEND_API_ROUTES.py | wc -l

echo '=== 8. Input validation — max lengths ==='
grep -rn 'max_length\|MaxLen\|constr\|Field.*max' backend/BACKEND_API_ROUTES.py | head -15

echo '=== 9. File upload security ==='
grep -n 'UploadFile' backend/BACKEND_API_ROUTES.py
grep -n '_scan_bytes_for_virus\|clamav\|clamd' backend/BACKEND_API_ROUTES.py | wc -l

echo '=== 10. Sensitive data exposure — PII in logs ==='
grep -rn 'print.*email\|print.*phone\|print.*password\|logger.*email\|logger.*phone' backend/*.py | head -10

echo '=== 11. HTTPS enforcement ==='
grep -n 'HTTPSRedirect\|https_only\|secure.*cookie\|httponly' backend/BACKEND_API_ROUTES.py | head -10

echo '=== 12. Dependencies with known vulnerabilities ==='
cat backend/requirements.txt | grep -E 'sqlalchemy|fastapi|pydantic|stripe|cryptography|jwt|passlib'
