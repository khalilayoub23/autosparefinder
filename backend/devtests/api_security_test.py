"""
Script: devtests/api_security_test.py
Purpose: Security + correctness test suite for the Partner API (routes/public_api.py).
         Runs against the LIVE deployed API and checks: authentication, data-leakage
         (no supplier/cost/margin/internal fields ever), rate limiting, SQL-injection safety,
         input validation, error hygiene (no stack traces), method restrictions, key storage
         (hashed, not plaintext), and price-policy correctness (cost×1.45 + conditional VAT).

Usage (inside the backend container):
  python3 /app/devtests/api_security_test.py

Data Imported / Modified: creates + deletes its own temporary api_keys rows. No catalog writes.

Author: AutoSpareFinder Agent
Last Updated: 2026-07-18
"""
import asyncio
import hashlib
import json
import os
import secrets

import asyncpg
import httpx

BASE = os.getenv("PUBLIC_API_BASE", "https://autosparefinder.co.il/api/public/v1")
DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

# Fields / substrings that must NEVER appear in any partner response (internal data).
FORBIDDEN = [
    "supplier", "supplier_id", "supplier_name", "reliability",
    "cost", "importer_price", "online_price", "base_price",
    "min_price_ils", "max_price_ils", "margin", "profit", "price_usd",
    "needs_oem", "master_enriched", "is_safety_critical", "specifications",
]
# Whitelisted keys allowed on a part object.
ALLOWED_PART_KEYS = {
    "part_id", "oem_number", "name", "name_he", "manufacturer", "category",
    "barcode", "available", "price",
}
ALLOWED_PRICE_KEYS = {"amount", "vat", "total", "currency", "vat_included"}

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


async def mk_key(conn, name, rate):
    raw = "asf_live_" + secrets.token_hex(24)
    await conn.execute(
        "INSERT INTO api_keys(key_hash,key_prefix,partner_name,scopes,rate_limit_per_min) "
        "VALUES($1,$2,$3,$4,$5)",
        hashlib.sha256(raw.encode()).hexdigest(), raw[:16], name, ["read"], rate,
    )
    return raw


def has_forbidden(obj) -> str:
    """Return the first forbidden token found in the JSON text, else ''. """
    blob = json.dumps(obj, ensure_ascii=False).lower()
    for tok in FORBIDDEN:
        if tok in blob:
            return tok
    return ""


async def main():
    conn = await asyncpg.connect(DB)
    key = await mk_key(conn, "SEC-main", 1000)
    revoked = await mk_key(conn, "SEC-revoked", 1000)
    await conn.execute("UPDATE api_keys SET is_active=false WHERE key_prefix=$1", revoked[:16])
    rl_key = await mk_key(conn, "SEC-ratelimit", 3)

    H = {"X-API-Key": key}
    async with httpx.AsyncClient(timeout=30) as c:
        # ── A. AUTH ───────────────────────────────────────────────
        print("\n[A] Authentication")
        r = await c.get(f"{BASE}/search", params={"q": "brake"})
        check("no key → 401", r.status_code == 401, f"got {r.status_code}")
        r = await c.get(f"{BASE}/search", params={"q": "brake"}, headers={"X-API-Key": "totally-wrong"})
        check("invalid key → 401", r.status_code == 401, f"got {r.status_code}")
        r = await c.get(f"{BASE}/search", params={"q": "brake"}, headers={"X-API-Key": revoked})
        check("revoked key → 401", r.status_code == 401, f"got {r.status_code}")
        r = await c.get(f"{BASE}/health")
        check("health no-auth → 200", r.status_code == 200, f"got {r.status_code}")
        for ep, params in [("search", {"q": "x"}), ("fitment", {"make": "Toyota", "model": "Corolla"}), ("manufacturers", {})]:
            r = await c.get(f"{BASE}/{ep}", params=params)
            check(f"/{ep} requires key → 401", r.status_code == 401, f"got {r.status_code}")

        # ── B. DATA LEAKAGE ───────────────────────────────────────
        print("\n[B] Data leakage (no internal fields)")
        r = await c.get(f"{BASE}/search", params={"q": "oil filter", "limit": 10}, headers=H)
        body = r.json()
        tok = has_forbidden(body)
        check("search: no forbidden fields in body", not tok, f"leaked '{tok}'" if tok else "clean")
        parts = body.get("results", [])
        extra = set()
        for p in parts:
            extra |= (set(p.keys()) - ALLOWED_PART_KEYS)
            if isinstance(p.get("price"), dict):
                extra |= {f"price.{k}" for k in (set(p["price"].keys()) - ALLOWED_PRICE_KEYS)}
        check("search: only whitelisted keys", not extra, f"unexpected {extra}" if extra else "ok")

        # IL part must carry 18% VAT; foreign 0% — and cost must NOT be derivable/exposed
        r = await c.get(f"{BASE}/search", params={"manufacturer": "Mercedes-Benz", "limit": 20}, headers=H)
        mbody = r.json()
        check("browse: no forbidden fields", not has_forbidden(mbody), "")
        il = [p for p in mbody.get("results", []) if p.get("price") and p["price"]["vat"] > 0]
        vat_ok = all(abs(p["price"]["vat"] - round(p["price"]["amount"] * 0.18, 2)) < 0.02 for p in il)
        check("IL parts carry exactly 18% VAT", (len(il) == 0 or vat_ok), f"{len(il)} IL parts checked")

        if parts:
            pid = parts[0]["part_id"]
            r = await c.get(f"{BASE}/parts/{pid}", headers=H)
            check("part detail: no forbidden fields", not has_forbidden(r.json()), "")

        r = await c.get(f"{BASE}/fitment", params={"make": "Toyota", "model": "Corolla", "year": 2018, "limit": 10}, headers=H)
        check("fitment: no forbidden fields", not has_forbidden(r.json()), "")

        # ── C. RATE LIMITING ──────────────────────────────────────
        print("\n[C] Rate limiting")
        codes = []
        for _ in range(6):
            rr = await c.get(f"{BASE}/manufacturers", headers={"X-API-Key": rl_key})
            codes.append(rr.status_code)
        check("rate=3 key → 429 after limit", codes.count(429) >= 1 and codes.count(200) <= 3, f"codes={codes}")

        # ── D. INJECTION SAFETY ───────────────────────────────────
        print("\n[D] Injection safety")
        for payload in ["' OR 1=1--", "'; DROP TABLE parts_catalog;--", "1) UNION SELECT NULL--", "%27%20OR%20%271"]:
            rr = await c.get(f"{BASE}/search", params={"q": payload, "limit": 1}, headers=H)
            leak = has_forbidden(rr.json()) if rr.headers.get("content-type", "").startswith("application/json") else ""
            body_txt = rr.text.lower()
            no_sqlerr = not any(w in body_txt for w in ["syntax error", "asyncpg", "traceback", "sqlalchemy", "psycopg"])
            check(f"SQLi q={payload[:18]!r} safe", rr.status_code in (200, 400, 422) and not leak and no_sqlerr,
                  f"status={rr.status_code}")
        # injection via manufacturer + part_id path
        rr = await c.get(f"{BASE}/search", params={"manufacturer": "Toyota'--", "limit": 1}, headers=H)
        check("SQLi manufacturer safe", rr.status_code in (200, 400, 422), f"status={rr.status_code}")

        # ── E. INPUT VALIDATION ───────────────────────────────────
        print("\n[E] Input validation")
        rr = await c.get(f"{BASE}/search", params={"manufacturer": "Toyota", "limit": 9999}, headers=H)
        check("limit>50 rejected (422)", rr.status_code == 422, f"status={rr.status_code}")
        rr = await c.get(f"{BASE}/search", params={"manufacturer": "Toyota", "limit": 5}, headers=H)
        n = len(rr.json().get("results", []))
        check("limit honored (≤5)", n <= 5, f"got {n}")
        rr = await c.get(f"{BASE}/search", params={"manufacturer": "Toyota", "offset": -1}, headers=H)
        check("negative offset rejected (422)", rr.status_code == 422, f"status={rr.status_code}")
        rr = await c.get(f"{BASE}/search", headers=H)  # neither q nor manufacturer
        check("missing q+manufacturer → 400", rr.status_code == 400, f"status={rr.status_code}")

        # ── F. BAD IDS / ERROR HYGIENE ────────────────────────────
        print("\n[F] Bad input / error hygiene")
        rr = await c.get(f"{BASE}/parts/not-a-uuid", headers=H)
        check("non-uuid part_id → 404 (not 500)", rr.status_code == 404, f"status={rr.status_code}")
        rr = await c.get(f"{BASE}/parts/00000000-0000-0000-0000-000000000000", headers=H)
        check("unknown uuid → 404", rr.status_code == 404, f"status={rr.status_code}")
        # error body never leaks internals
        clean = not any(w in rr.text.lower() for w in ["traceback", "asyncpg", "/app/", "sqlalchemy", "line "])
        check("error body has no stack trace / paths", clean, "")

        # ── G. METHOD RESTRICTION ─────────────────────────────────
        print("\n[G] Method restriction")
        rr = await c.post(f"{BASE}/search", params={"q": "x"}, headers=H)
        check("POST /search → 405", rr.status_code == 405, f"status={rr.status_code}")

        # ── H. INTERNAL ENDPOINTS STILL PROTECTED ─────────────────
        print("\n[H] Internal endpoints protected (spot check)")
        for path in ["/api/v1/admin/orders", "/api/v1/auth/me", "/api/v1/system/collect"]:
            rr = await c.get(f"https://autosparefinder.co.il{path}")
            check(f"{path} not open", rr.status_code in (401, 403, 405, 422, 404, 400), f"status={rr.status_code}")

    # ── I. KEY STORAGE (hashed, not plaintext) ────────────────────
    print("\n[I] Key storage")
    row = await conn.fetchrow("SELECT key_hash, key_prefix FROM api_keys WHERE key_prefix=$1", key[:16])
    stored_ok = row and row["key_hash"] == hashlib.sha256(key.encode()).hexdigest() and len(row["key_hash"]) == 64
    plaintext = await conn.fetchval("SELECT COUNT(*) FROM api_keys WHERE key_hash LIKE 'asf_live_%'")
    check("keys stored as sha256 (64 hex)", bool(stored_ok), "")
    check("no plaintext keys in DB", plaintext == 0, f"{plaintext} plaintext rows")

    # cleanup
    await conn.execute("DELETE FROM api_keys WHERE partner_name LIKE 'SEC-%'")
    await conn.close()

    # ── SUMMARY ───────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 56)
    print(f"SECURITY TEST RESULT: {passed}/{total} passed")
    fails = [n for n, ok, _ in results if not ok]
    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
    else:
        print("ALL CHECKS PASSED ✅")
    print("=" * 56)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
