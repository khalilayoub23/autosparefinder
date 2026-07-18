"""
Script: maintenance/issue_api_key.py
Purpose: Issue (or revoke/list) partner API keys for the public API (routes/public_api.py).
         The raw key is shown ONCE at creation; only its sha256 is stored.

Usage (inside the backend container):
  python3 /app/maintenance/issue_api_key.py --partner "Acme Cars" [--rate 120] [--scope read]
  python3 /app/maintenance/issue_api_key.py --list
  python3 /app/maintenance/issue_api_key.py --revoke <key_prefix>

Data Imported / Modified: api_keys (insert / update is_active). No catalog data.

Author: AutoSpareFinder Agent
Last Updated: 2026-07-18
"""
import argparse
import asyncio
import hashlib
import os
import secrets

import asyncpg

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")


async def issue(partner: str, rate: int, scopes: list[str]) -> None:
    raw = "asf_live_" + secrets.token_hex(24)          # 48 hex chars
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:16]
    conn = await asyncpg.connect(DB)
    await conn.execute(
        "INSERT INTO api_keys(key_hash, key_prefix, partner_name, scopes, rate_limit_per_min) "
        "VALUES($1,$2,$3,$4,$5)",
        key_hash, prefix, partner, scopes, rate,
    )
    await conn.close()
    print("API key created — copy it now, it will NOT be shown again:\n")
    print(f"  {raw}\n")
    print(f"  partner        : {partner}")
    print(f"  prefix         : {prefix}")
    print(f"  rate limit     : {rate}/min")
    print(f"  scopes         : {scopes}")
    print("\nUse it as the request header:  X-API-Key: <the key above>")


async def list_keys() -> None:
    conn = await asyncpg.connect(DB)
    rows = await conn.fetch(
        "SELECT key_prefix, partner_name, rate_limit_per_min, is_active, request_count, last_used_at "
        "FROM api_keys ORDER BY created_at DESC"
    )
    await conn.close()
    if not rows:
        print("(no keys)")
        return
    for r in rows:
        state = "active" if r["is_active"] else "REVOKED"
        print(f"  {r['key_prefix']}…  {r['partner_name']:<24} {r['rate_limit_per_min']}/min  "
              f"{state}  reqs={r['request_count']}  last={r['last_used_at']}")


async def revoke(prefix: str) -> None:
    conn = await asyncpg.connect(DB)
    n = await conn.execute("UPDATE api_keys SET is_active=false WHERE key_prefix LIKE $1", prefix + "%")
    await conn.close()
    print(f"revoked: {n}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--partner")
    ap.add_argument("--rate", type=int, default=60)
    ap.add_argument("--scope", action="append", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--revoke")
    a = ap.parse_args()
    if a.list:
        asyncio.run(list_keys())
    elif a.revoke:
        asyncio.run(revoke(a.revoke))
    elif a.partner:
        asyncio.run(issue(a.partner, a.rate, a.scope or ["read"]))
    else:
        ap.error("give --partner NAME (to create), --list, or --revoke PREFIX")


if __name__ == "__main__":
    main()
