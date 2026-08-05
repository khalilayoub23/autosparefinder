#!/usr/bin/env python3
"""
Feature / integration test harness — test new features in the sandbox before
pushing to production. Drop your test code in the TESTS block below.

Usage (inside sandbox_tools):
    python3 /workspace/tests/07_feature_test.py
"""

import os, json, requests
from rich.console import Console
from rich.table import Table

console = Console()
BASE = os.environ.get("TARGET", "http://sandbox_backend:8000")
results = []

def test(name, fn):
    """Run fn(), record pass/fail."""
    try:
        ok, note = fn()
        status = "✅ PASS" if ok else f"❌ FAIL"
        results.append((name, status, note or ""))
        console.print(f"  {status}  {name}  {note or ''}")
    except Exception as e:
        results.append((name, "💥 ERROR", str(e)))
        console.print(f"  💥 ERROR  {name}  {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# ADD YOUR FEATURE TESTS BELOW
# ═══════════════════════════════════════════════════════════════════════════════

console.rule("[bold cyan]Feature Tests[/bold cyan]")

# Example: test that health endpoint returns the expected shape
def test_health():
    r = requests.get(f"{BASE}/api/v1/system/health", timeout=5)
    if r.status_code != 200:
        return False, f"status={r.status_code}"
    data = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
    return True, f"status={r.status_code}"

test("Health endpoint returns 200", test_health)

# Example: test search returns the expected field shape
def test_search_fields():
    r = requests.get(f"{BASE}/api/v1/parts/search", params={"q": "filter", "limit": 1}, timeout=8)
    if r.status_code not in (200, 204):
        return r.status_code == 200, f"status={r.status_code}"
    data = r.json()
    hits = data.get("hits", data.get("results", data if isinstance(data, list) else []))
    if not hits:
        return True, "no results (sandbox DB empty)"
    part = hits[0]
    internal_fields = {"importer_price_ils", "online_price_ils", "base_price", "supplier_name"}
    leaked = internal_fields & set(part.keys())
    return not leaked, f"leaked={leaked or 'none'}"

test("Search does not leak internal fields", test_search_fields)

# Example: public API requires X-API-Key
def test_public_api_auth():
    r = requests.get(f"{BASE}/api/public/v1/search", params={"q": "brake"}, timeout=5)
    return r.status_code in (401, 403), f"status={r.status_code}"

test("Public API rejects unauthenticated requests", test_public_api_auth)

# ─── ADD YOUR TESTS HERE ───────────────────────────────────────────────────────
#
# def test_my_new_feature():
#     r = requests.post(f"{BASE}/api/v1/my/endpoint", json={...})
#     return r.status_code == 200, r.text[:100]
#
# test("My new feature works", test_my_new_feature)
#
# ──────────────────────────────────────────────────────────────────────────────


# ── Summary ───────────────────────────────────────────────────────────────────
console.rule("[bold]Results[/bold]")
table = Table("Test", "Result", "Notes")
for name, status, note in results:
    table.add_row(name, status, note)
console.print(table)
passes = sum(1 for _, s, _ in results if "PASS" in s)
console.print(f"\n  [bold]{passes}/{len(results)} passed[/bold]")
