#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT_DIR}"

# Runs inside backend container so credentials come from runtime env.
docker compose exec -T backend python - <<'PY'
import base64
import json
import os
import sys
import urllib.parse
import urllib.request

client_id = (os.getenv("EBAY_CLIENT_ID") or "").strip()
client_secret = (os.getenv("EBAY_CLIENT_SECRET") or "").strip()
identity_base = (os.getenv("EBAY_IDENTITY_BASE") or "https://api.ebay.com/identity/v1").rstrip("/")
scope = (os.getenv("EBAY_SCOPE") or "https://api.ebay.com/oauth/api_scope/buy.browse").strip()

if not client_id or not client_secret:
    print("missing_credentials: set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET")
    sys.exit(2)

url = f"{identity_base}/oauth2/token"
body = urllib.parse.urlencode({
    "grant_type": "client_credentials",
    "scope": scope,
}).encode("utf-8")

basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
headers = {
    "Authorization": f"Basic {basic}",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
}

req = urllib.request.Request(url=url, data=body, headers=headers, method="POST")
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
except Exception as exc:
    print(f"token_request_failed: {exc}")
    sys.exit(1)

token = str(payload.get("access_token") or "").strip()
expires_in = int(payload.get("expires_in") or 0)
if not token:
    print("token_request_failed: access_token missing in response")
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(1)

print("token_status=ok")
print(f"expires_in={expires_in}")
print("export_line_start")
print(f"export EBAY_BEARER_TOKEN='{token}'")
print("export_line_end")
PY
