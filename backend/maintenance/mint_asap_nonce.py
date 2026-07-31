#!/usr/bin/env python3
"""
Script:  maintenance/mint_asap_nonce.py
Purpose: Mint a short-lived, SINGLE-USE upload nonce for /api/v1/system/asap-collect.

Why: ASAP's CSV load sheets sit behind the owner's asapnetwork.org login, so the
server (403'd, no session) cannot fetch them — the owner's browser relays them.
That relay needs to authenticate, but pasting the long-lived COLLECT_SECRET into a
browser page context exposes it to that page and to anything recording the session.
This mints a throwaway credential instead: 15-minute TTL, deleted on first use.

Usage:  python3 /app/maintenance/mint_asap_nonce.py
Last Updated: 2026-07-28
"""
import asyncio
import os
import secrets

TTL_S = 900


async def main() -> None:
    import redis.asyncio as redis
    r = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    nonce = secrets.token_urlsafe(18)
    await r.set(f"asap:upload_nonce:{nonce}", "1", ex=TTL_S)
    await r.aclose()
    print(nonce)


asyncio.run(main())
