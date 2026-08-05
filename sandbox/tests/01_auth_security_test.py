#!/usr/bin/env python3
"""
Auth & JWT security test suite for AutoSpareFinder.
Tests: registration, login, JWT manipulation, token expiry, auth bypass.

Usage (from inside sandbox_tools):
    python3 /workspace/tests/01_auth_security_test.py
"""

import os, sys, json, time
import requests
import jwt as pyjwt
from datetime import datetime, timedelta, timezone
from rich import print as rprint
from rich.table import Table
from rich.console import Console

BASE = os.environ.get("TARGET", "http://sandbox_backend:8000")
console = Console()

results = []

def check(name, resp, expected_status, extra_check=None):
    ok = resp.status_code == expected_status
    if extra_check and ok:
        ok = extra_check(resp)
    status = "✅ PASS" if ok else "❌ FAIL"
    results.append((name, status, resp.status_code, expected_status))
    console.print(f"  {status}  [{resp.status_code}]  {name}")
    return resp

def section(title):
    console.rule(f"[bold cyan]{title}[/bold cyan]")


# ── 1. Health ─────────────────────────────────────────────────────────────────
section("1. Basic connectivity")
check("GET /health returns 200", requests.get(f"{BASE}/api/v1/system/health"), 200)


# ── 2. Registration edge cases ────────────────────────────────────────────────
section("2. Registration")
REG_URL = f"{BASE}/api/v1/auth/register"

# Valid registration
ts = int(time.time())
test_email = f"pentest_{ts}@sandbox.local"
r = requests.post(REG_URL, json={"email": test_email, "password": "TestPass2024!", "name": "Pentest User"})
check("Valid registration returns 200/201", r, r.status_code if r.status_code in (200,201) else 400,
      lambda resp: resp.status_code in (200, 201))

# Duplicate email
check("Duplicate email returns 4xx",
      requests.post(REG_URL, json={"email": test_email, "password": "TestPass2024!", "name": "Dup"}),
      400, lambda r: r.status_code in (400, 409, 422))

# No password
check("Missing password returns 422",
      requests.post(REG_URL, json={"email": f"nopwd_{ts}@sandbox.local"}),
      422, lambda r: r.status_code in (400, 422))

# SQL injection in email field
check("SQL injection in email is rejected",
      requests.post(REG_URL, json={"email": "' OR 1=1 --@x.com", "password": "Test123!"}),
      422, lambda r: r.status_code in (400, 422, 500))


# ── 3. Login ──────────────────────────────────────────────────────────────────
section("3. Login")
LOGIN_URL = f"{BASE}/api/v1/auth/login"
token = None

# Valid login (only if registration succeeded)
r_login = requests.post(LOGIN_URL, json={"email": test_email, "password": "TestPass2024!"})
if r_login.status_code == 200:
    data = r_login.json()
    token = data.get("access_token") or data.get("token")
    check("Valid login returns 200 + token", r_login, 200,
          lambda r: bool(token))
else:
    check("Login attempt", r_login, 200)  # will fail/show real status

# Wrong password
check("Wrong password → 401",
      requests.post(LOGIN_URL, json={"email": test_email, "password": "WrongPass!"}),
      401, lambda r: r.status_code in (401, 400))

# Non-existent user
check("Non-existent user → 401/404",
      requests.post(LOGIN_URL, json={"email": "nobody@nowhere.com", "password": "TestPass2024!"}),
      401, lambda r: r.status_code in (401, 404, 400))


# ── 4. JWT manipulation ───────────────────────────────────────────────────────
section("4. JWT manipulation")

SB_SECRET = os.environ.get("SB_JWT_SECRET", "")

if token:
    headers = {"Authorization": f"Bearer {token}"}
    PROTECTED = f"{BASE}/api/v1/auth/me"

    # Valid token works
    check("Valid token → 200 on /me",
          requests.get(PROTECTED, headers=headers), 200)

    # No token → 401
    check("No token → 401",
          requests.get(PROTECTED), 401)

    # Tampered token (flip one char)
    bad_token = token[:-3] + "xxx"
    check("Tampered token → 401",
          requests.get(PROTECTED, headers={"Authorization": f"Bearer {bad_token}"}),
          401, lambda r: r.status_code in (401, 422))

    # Algorithm confusion: forge with HS256 using empty secret
    try:
        fake = pyjwt.encode({"sub": "admin@sandbox.local", "role": "admin",
                             "exp": datetime.now(tz=timezone.utc) + timedelta(hours=1)},
                            "", algorithm="HS256")
        check("Empty-secret forged token → 401",
              requests.get(PROTECTED, headers={"Authorization": f"Bearer {fake}"}),
              401, lambda r: r.status_code in (401, 422))
    except Exception as e:
        results.append(("JWT forge test", f"⚠️ SKIP ({e})", "-", 401))

    # Algorithm confusion: alg=none
    try:
        parts = token.split(".")
        import base64
        header = base64.urlsafe_b64decode(parts[0] + "==").decode()
        header_mod = header.replace('"RS256"', '"none"').replace('"HS256"', '"none"')
        fake_none = base64.urlsafe_b64encode(header_mod.encode()).decode().rstrip("=") + "." + parts[1] + "."
        check("alg=none token → 401",
              requests.get(PROTECTED, headers={"Authorization": f"Bearer {fake_none}"}),
              401, lambda r: r.status_code in (401, 422))
    except Exception as e:
        results.append(("alg=none test", f"⚠️ SKIP ({e})", "-", 401))

    # Privilege escalation: forge with same secret but role=admin
    if SB_SECRET:
        try:
            decoded = pyjwt.decode(token, SB_SECRET, algorithms=["HS256"])
            decoded["role"] = "admin"
            decoded["is_admin"] = True
            escalated = pyjwt.encode(decoded, SB_SECRET, algorithm="HS256")
            r_esc = requests.get(f"{BASE}/api/v1/admin", headers={"Authorization": f"Bearer {escalated}"})
            check("Role-escalated token → admin endpoint 403 (not 200)",
                  r_esc, 403, lambda r: r.status_code in (403, 404, 405))
        except Exception as e:
            results.append(("Role escalation test", f"⚠️ SKIP ({e})", "-", 403))


# ── 5. Rate limiting ──────────────────────────────────────────────────────────
section("5. Rate limiting")
import threading

hits_429 = []
def _hit():
    r = requests.post(LOGIN_URL, json={"email": "ratelimit@test.com", "password": "Test!"})
    hits_429.append(r.status_code)

threads = [threading.Thread(target=_hit) for _ in range(40)]
for t in threads: t.start()
for t in threads: t.join()
got_429 = hits_429.count(429)
check("40 rapid login attempts triggers rate limit (≥1 × 429)",
      type("R", (), {"status_code": 429 if got_429 else 200})(),
      429, lambda r: got_429 > 0)
console.print(f"    {got_429}/40 requests returned 429")


# ── 6. Coupon endpoint (must fail closed) ─────────────────────────────────────
section("6. Coupon validation (must fail closed)")
COUPON_URL = f"{BASE}/api/v1/marketing/validate-coupon"
check("Random coupon → 401/invalid (not 200 with discount)",
      requests.post(COUPON_URL, json={"code": "GETFREE100", "order_total": 500}),
      401, lambda r: r.status_code in (401, 403, 404, 422) or
                     (r.status_code == 200 and r.json().get("valid") is False))

check("SQL injection coupon → rejected",
      requests.post(COUPON_URL, json={"code": "' OR 1=1 --", "order_total": 500}),
      401, lambda r: r.status_code in (401, 403, 404, 422))


# ── 7. Sensitive endpoint access ──────────────────────────────────────────────
section("7. Admin / internal endpoints (all must be 401/403/404)")
for path in ["/api/v1/admin", "/api/v1/admin/users", "/api/v1/internal",
             "/api/v1/system/admin", "/.env", "/.git/HEAD",
             "/api/v1/admin/config", "/admin", "/phpmyadmin"]:
    r = requests.get(f"{BASE}{path}")
    check(f"GET {path} → not 200",
          r, r.status_code,
          lambda rr: rr.status_code not in (200, 500))


# ── 8. Collect endpoint auth ──────────────────────────────────────────────────
section("8. /collect endpoint auth (must require secret)")
COLLECT = f"{BASE}/api/v1/system/collect"
check("No secret header → 403",
      requests.post(COLLECT, json={"parts": [], "done": True, "source": "test"}),
      403, lambda r: r.status_code in (403, 401, 422))

SB_COLLECT = os.environ.get("SB_COLLECT_SECRET", "")
if SB_COLLECT:
    check("Correct collect secret → not 403",
          requests.post(COLLECT,
                        headers={"X-Collect-Secret": SB_COLLECT},
                        json={"parts": [], "done": True, "source": "test", "vehicle_slug": "test/test"}),
          200, lambda r: r.status_code not in (403, 401))


# ── Summary table ─────────────────────────────────────────────────────────────
console.rule("[bold green]Results[/bold green]")
table = Table("Test", "Result", "Got", "Expected")
for name, status, got, exp in results:
    table.add_row(name, status, str(got), str(exp))
console.print(table)

passes = sum(1 for _, s, _, _ in results if "PASS" in s)
total  = len(results)
console.print(f"\n  [bold]{'✅' if passes==total else '⚠️ '} {passes}/{total} passed[/bold]")
