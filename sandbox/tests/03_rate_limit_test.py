#!/usr/bin/env python3
"""
Rate limiting and anti-abuse test.
Tests per-endpoint limits, burst behavior, and IP spoofing via X-Forwarded-For.

Usage (inside sandbox_tools):
    python3 /workspace/tests/03_rate_limit_test.py
"""

import os, time, threading, statistics
import requests
from rich.console import Console
from rich.table import Table

console = Console()
BASE = os.environ.get("TARGET", "http://sandbox_backend:8000")

def burst(method, path, n=60, params=None, json_body=None, headers=None):
    """Fire n concurrent requests, return list of status codes + latencies."""
    codes = []
    lock  = threading.Lock()

    def _hit():
        t0 = time.time()
        try:
            r = requests.request(method, f"{BASE}{path}", params=params,
                                 json=json_body, headers=headers,
                                 timeout=8, allow_redirects=False)
            code = r.status_code
        except Exception:
            code = 0
        with lock:
            codes.append((code, time.time() - t0))

    threads = [threading.Thread(target=_hit) for _ in range(n)]
    for t in threads: t.start()
    for t in threads: t.join()
    return codes


console.rule("[bold cyan]AutoSpareFinder — Rate Limit Tests[/bold cyan]")
table = Table("Endpoint", "Burst", "429s", "Result", "Notes")

# ── Search endpoint (30/min expected) ─────────────────────────────────────────
codes = burst("GET", "/api/v1/parts/search", n=50, params={"q": "brake"})
c429 = sum(1 for c,_ in codes if c==429)
table.add_row("/parts/search", "50", str(c429),
              "✅" if c429 > 0 else "❌ NO LIMIT",
              "expects 429 after ~30 hits")

# ── Autocomplete (30/min expected) ────────────────────────────────────────────
codes = burst("GET", "/api/v1/parts/autocomplete", n=50, params={"q": "b"})
c429 = sum(1 for c,_ in codes if c==429)
table.add_row("/parts/autocomplete", "50", str(c429),
              "✅" if c429 > 0 else "❌ NO LIMIT",
              "expects 429 after ~30 hits")

# ── Login (should rate limit hard) ────────────────────────────────────────────
codes = burst("POST", "/api/v1/auth/login", n=40,
              json_body={"email": "x@x.com", "password": "Test!"})
c429 = sum(1 for c,_ in codes if c==429)
table.add_row("/auth/login", "40", str(c429),
              "✅" if c429 > 0 else "❌ NO LIMIT",
              "brute-force protection")

# ── X-Forwarded-For spoofing bypass ───────────────────────────────────────────
codes = burst("GET", "/api/v1/parts/search", n=50,
              params={"q": "brake"},
              headers={"X-Forwarded-For": "1.2.3.4"})
c429_spoof = sum(1 for c,_ in codes if c==429)
# If we got FEWER 429s with a spoofed IP, rate limits are keying on XFF (bad)
codes_normal = burst("GET", "/api/v1/parts/search", n=50, params={"q": "brake"})
c429_normal  = sum(1 for c,_ in codes_normal if c==429)

if c429_spoof < c429_normal - 5:
    note = f"⚠️ XFF bypass: {c429_spoof} vs {c429_normal} 429s"
else:
    note = f"XFF gives same rate ({c429_spoof} 429s)"
table.add_row("/parts/search (XFF spoof)", "50", str(c429_spoof),
              "✅" if c429_spoof >= c429_normal - 5 else "❌ BYPASS",
              note)

console.print(table)
console.print("\n[dim]429 = rate limit triggered · ❌ = limit NOT enforced (security gap)[/dim]")
