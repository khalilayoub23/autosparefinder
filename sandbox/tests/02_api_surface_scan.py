#!/usr/bin/env python3
"""
API surface scanner — discovers endpoints, checks for info leakage,
verifies sensitive paths are protected, and tests CORS headers.

Usage (from sandbox_tools):
    python3 /workspace/tests/02_api_surface_scan.py [--external]
"""

import os, sys, json, re
import requests
from rich import print as rprint
from rich.table import Table
from rich.console import Console

console = Console()
BASE = os.environ.get("TARGET", "http://sandbox_backend:8000")
if "--external" in sys.argv:
    BASE = os.environ.get("TARGET_EXTERNAL", "https://autosparefinder.co.il")
    console.print(f"[yellow]Testing EXTERNAL target: {BASE}[/yellow]")
else:
    console.print(f"[cyan]Testing SANDBOX target: {BASE}[/cyan]")

findings = []

def probe(method, path, **kwargs):
    url = f"{BASE.rstrip('/')}/{path.lstrip('/')}"
    try:
        r = requests.request(method, url, timeout=10, allow_redirects=False, **kwargs)
        return r
    except Exception as e:
        return None


# ── 1. Info disclosure in /health ─────────────────────────────────────────────
console.rule("1. Health endpoint info disclosure")
r = probe("GET", "/api/v1/system/health")
if r:
    body = r.text.lower()
    leaks = [k for k in ["password", "secret", "key", "token", "database_url"] if k in body]
    if leaks:
        findings.append(("HIGH", "/health leaks sensitive keys", str(leaks)))
        console.print(f"  ❌ LEAKS: {leaks}")
    else:
        console.print(f"  ✅ No secrets in /health response")
    # Check if version/stack is exposed
    if any(k in body for k in ["python", "uvicorn", "fastapi", "postgresql", "version"]):
        findings.append(("LOW", "/health exposes stack info", body[:200]))
        console.print(f"  ⚠️  Stack info visible in /health")


# ── 2. Error message disclosure ───────────────────────────────────────────────
console.rule("2. Error message / stack trace disclosure")
# Malformed JSON
r = probe("POST", "/api/v1/auth/login",
          data="not json at all", headers={"Content-Type": "application/json"})
if r:
    if r.status_code == 500:
        findings.append(("MEDIUM", "500 on malformed JSON (should be 422)", ""))
        console.print(f"  ❌ 500 on malformed JSON — check error handler")
    elif "traceback" in r.text.lower() or "exception" in r.text.lower():
        findings.append(("MEDIUM", "Stack trace leaked in error response", r.text[:300]))
        console.print(f"  ❌ Traceback leaked in response")
    else:
        console.print(f"  ✅ [{r.status_code}] Malformed JSON handled cleanly")

# Path traversal in thumbnails
for payload in ["../etc/passwd", "..%2Fetc%2Fpasswd", "thumbs/../../etc/passwd"]:
    r = probe("GET", f"/api/v1/thumbnails/{payload}")
    if r and r.status_code == 200 and "root:" in r.text:
        findings.append(("CRITICAL", f"Path traversal in /thumbnails/{payload}", ""))
        console.print(f"  ❌ CRITICAL: Path traversal succeeded!")
    elif r:
        console.print(f"  ✅ [{r.status_code}] Traversal blocked: /thumbnails/{payload}")


# ── 3. CORS headers ───────────────────────────────────────────────────────────
console.rule("3. CORS")
for path in ["/api/v1/parts/search", "/api/v1/auth/me", "/api/public/v1/health"]:
    r = probe("OPTIONS", path,
              headers={"Origin": "https://evil.attacker.com",
                       "Access-Control-Request-Method": "GET"})
    if r:
        acao = r.headers.get("Access-Control-Allow-Origin", "")
        if acao == "*" or acao == "https://evil.attacker.com":
            findings.append(("MEDIUM", f"Overly-permissive CORS on {path}", f"ACAO: {acao}"))
            console.print(f"  ❌ CORS allows evil.attacker.com on {path}")
        else:
            console.print(f"  ✅ CORS OK on {path} (ACAO={acao!r})")


# ── 4. HTTP method enforcement ────────────────────────────────────────────────
console.rule("4. HTTP method enforcement")
for path, bad_method in [
    ("/api/v1/system/health", "DELETE"),
    ("/api/v1/parts/search",  "PUT"),
    ("/api/v1/auth/login",    "GET"),
]:
    r = probe(bad_method, path)
    if r and r.status_code == 200:
        findings.append(("LOW", f"{bad_method} {path} returned 200 (unexpected)", ""))
        console.print(f"  ⚠️  [{r.status_code}] {bad_method} {path}")
    elif r:
        console.print(f"  ✅ [{r.status_code}] {bad_method} {path} correctly rejected")


# ── 5. Security headers ───────────────────────────────────────────────────────
console.rule("5. Security headers")
r = probe("GET", "/api/v1/system/health")
if r:
    headers_needed = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": None,
        "Referrer-Policy": None,
        "Content-Security-Policy": None,
    }
    for hdr, expected in headers_needed.items():
        val = r.headers.get(hdr, "")
        if not val:
            findings.append(("LOW", f"Missing header: {hdr}", ""))
            console.print(f"  ⚠️  Missing: {hdr}")
        elif expected and expected.lower() not in val.lower():
            findings.append(("LOW", f"{hdr} has unexpected value: {val}", ""))
            console.print(f"  ⚠️  {hdr}: {val}")
        else:
            console.print(f"  ✅ {hdr}: {val or 'present'}")


# ── 6. Supplier price / internal data leakage in search ───────────────────────
console.rule("6. Internal data not leaked in search")
r = probe("GET", "/api/v1/parts/search", params={"q": "oil filter", "limit": 3})
if r and r.status_code == 200:
    body = r.text
    internal_fields = ["importer_price_ils", "online_price_ils", "base_price",
                       "supplier_name", "supplier_url", "credentials",
                       "is_active", "cost"]
    leaks = [f for f in internal_fields if f in body]
    if leaks:
        findings.append(("HIGH", "Search leaks internal pricing fields", str(leaks)))
        console.print(f"  ❌ Search response leaks: {leaks}")
    else:
        console.print(f"  ✅ No internal price fields in search response")
else:
    console.print(f"  [grey]Search returned {r.status_code if r else 'timeout'} — skip[/grey]")


# ── 7. Verify robots.txt ──────────────────────────────────────────────────────
console.rule("7. robots.txt")
r = probe("GET", "/robots.txt")
if r and r.status_code == 200:
    if "Disallow" in r.text:
        console.print(f"  ✅ robots.txt present with Disallow rules")
    else:
        findings.append(("LOW", "robots.txt exists but has no Disallow rules", ""))
        console.print(f"  ⚠️  robots.txt exists but has no Disallow")
else:
    findings.append(("LOW", "No robots.txt (AI crawlers unguided)", ""))
    console.print(f"  ⚠️  No robots.txt")


# ── Summary ───────────────────────────────────────────────────────────────────
console.rule("[bold]Findings Summary[/bold]")
if not findings:
    console.print("  [bold green]No issues found.[/bold green]")
else:
    table = Table("Severity", "Finding", "Detail")
    for sev, msg, detail in findings:
        color = {"CRITICAL": "red", "HIGH": "red", "MEDIUM": "yellow", "LOW": "cyan"}.get(sev, "white")
        table.add_row(f"[{color}]{sev}[/{color}]", msg, detail[:80])
    console.print(table)

# Save results
import datetime
out = {"scanned_at": datetime.datetime.utcnow().isoformat(), "target": BASE, "findings": findings}
out_path = "/workspace/results/api_surface_scan.json"
try:
    import json
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    console.print(f"\n  Results saved: {out_path}")
except Exception:
    pass
