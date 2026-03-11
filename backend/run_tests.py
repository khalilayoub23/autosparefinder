#!/usr/bin/env python3
"""Comprehensive API test runner"""
import requests
import sys
import psycopg2

BASE = "http://localhost:8000/api/v1"

def ok(label, cond, detail=""):
    status = "✅" if cond else "❌"
    print(f"{status} {label}" + (f": {detail}" if detail else ""))
    return cond

def section(name):
    print(f"\n{'='*50}")
    print(f"  {name}")
    print('='*50)

# --- AUTH ---
section("AUTH")
r = requests.post(f"{BASE}/auth/login", json={"email":"admin@autospare.com","password":"Admin2026!"})
ok("Admin login", r.status_code==200, f"status={r.status_code}")
token = r.json().get("access_token","") if r.status_code==200 else ""
ok("Got token", bool(token), f"{token[:20]}...")

headers = {"Authorization": f"Bearer {token}"}

# Register new user with Israeli phone
r2 = requests.post(f"{BASE}/auth/register", json={
    "full_name":"Test User", "email":"testuser_auto@example.com",
    "phone":"0501234567", "password":"Test123!"
})
ok("Register Israeli phone", r2.status_code in (200,201,409), f"status={r2.status_code} body={r2.text[:80]}")

# Register with international phone
r3 = requests.post(f"{BASE}/auth/register", json={
    "full_name":"Intl User", "email":"intl_auto@example.com",
    "phone":"+18777804236", "password":"Test123!"
})
ok("Register intl phone (+1...)", r3.status_code in (200,201,409), f"status={r3.status_code} body={r3.text[:80]}")

# --- PARTS SEARCH ---
section("PARTS SEARCH")

r = requests.get(f"{BASE}/parts/search", params={"vehicle_manufacturer":"JAECOO","category":"דלק"})
ok("JAECOO + דלק search", r.status_code==200, f"status={r.status_code}")
if r.status_code == 200:
    d = r.json()
    orig = d.get("original") or d.get("oem")
    part = orig.get("part") if orig else None
    ok("JAECOO part found", bool(part), part["name"] if part else "none")
    if part:
        ok("JAECOO part_type OEM", part.get("part_type") in ("OEM","Original"), part.get("part_type"))
        sups = orig.get("suppliers",[])
        ok("JAECOO has suppliers", len(sups)>0, f"{len(sups)} suppliers")
        if sups:
            ok("JAECOO price_ils set", sups[0].get("price_ils") is not None, f"price_ils={sups[0].get('price_ils')}")
            ok("JAECOO real availability", sups[0].get("availability") in ("in_stock","on_order"), sups[0].get("availability"))

r = requests.get(f"{BASE}/parts/search", params={"vehicle_manufacturer":"Hyundai","category":"בלמים"})
ok("Hyundai + בלמים search", r.status_code==200)
if r.status_code == 200:
    d = r.json()
    orig = d.get("original") or d.get("oem")
    part = orig.get("part") if orig else None
    ok("Hyundai part found", bool(part), part["name"] if part else "none")

r = requests.get(f"{BASE}/parts/search", params={"vehicle_manufacturer":"Mercedes","category":"מנוע"})
ok("Mercedes + מנוע search", r.status_code==200)
if r.status_code == 200:
    d = r.json()
    orig = d.get("original") or d.get("oem")
    part = orig.get("part") if orig else None
    ok("Mercedes part found", bool(part), part["name"] if part else "none")

r = requests.get(f"{BASE}/parts/search", params={"query":"מסנן שמן"})
ok("Text search 'מסנן שמן'", r.status_code==200, f"status={r.status_code}")
if r.status_code==200:
    d = r.json()
    orig = d.get("original") or d.get("oem")
    part = orig.get("part") if orig else None
    ok("Text search returns part", bool(part), part["name"] if part else "none")

# Categories
r = requests.get(f"{BASE}/parts/categories")
ok("Categories endpoint", r.status_code==200)
if r.status_code==200:
    cats = r.json().get("categories",[])
    ok("Categories non-empty", len(cats)>0, f"{len(cats)} categories: {cats[:4]}")

# --- ADMIN ---
section("ADMIN DASHBOARD")
r = requests.get(f"{BASE}/admin/stats", headers=headers)
ok("Admin stats", r.status_code==200, f"status={r.status_code}")
if r.status_code==200:
    s = r.json()
    ok("stats.total_parts", s.get("total_parts",0)>0, s.get("total_parts"))
    ok("stats.total_users", s.get("total_users",0)>0, s.get("total_users"))
    print(f"   → parts={s.get('total_parts')}, users={s.get('total_users')}, orders={s.get('total_orders')}")

r = requests.get(f"{BASE}/admin/orders", headers=headers)
ok("Admin orders", r.status_code==200, f"status={r.status_code} body={r.text[:100]}")

r = requests.get(f"{BASE}/admin/users", headers=headers)
ok("Admin users", r.status_code==200, f"status={r.status_code}")
if r.status_code==200:
    raw = r.json()
    users = raw.get("users", raw) if isinstance(raw, dict) else raw
    ok("Users list", isinstance(users, list) and len(users)>0, f"{len(users)} users: {[u.get('email') for u in users[:3]]}")

r = requests.get(f"{BASE}/admin/suppliers", headers=headers)
ok("Admin suppliers", r.status_code==200, f"status={r.status_code}")
if r.status_code==200:
    raw = r.json()
    sups = raw.get("suppliers", raw) if isinstance(raw, dict) else raw
    ok("Suppliers list", isinstance(sups, list) and len(sups)>0, f"{len(sups)} suppliers: {[s.get('name','?') for s in sups]}")

r = requests.get(f"{BASE}/admin/analytics/sales", headers=headers, params={"period":"month"})
ok("Admin analytics/sales", r.status_code==200, f"status={r.status_code} body={r.text[:150]}")

r = requests.get(f"{BASE}/admin/price-sync/status", headers=headers)
ok("Admin price-sync/status", r.status_code in (200,404), f"status={r.status_code} body={r.text[:100]}")

# Supplier parts counts per brand
section("SUPPLIER PARTS COUNTS")
conn = psycopg2.connect("postgresql://autospare:autospare_dev@localhost:5432/autospare")
cur = conn.cursor()
cur.execute("""
  SELECT SUBSTRING(pc.sku FROM 1 FOR POSITION('-' IN pc.sku)-1) AS brand,
         COUNT(*) AS cnt,
         SUM(CASE WHEN sp.availability='in_stock' THEN 1 ELSE 0 END) AS in_stock
  FROM supplier_parts sp
  JOIN parts_catalog pc ON pc.id=sp.part_id
  GROUP BY 1 ORDER BY cnt DESC LIMIT 12
""")
rows = cur.fetchall()
total = sum(r[1] for r in rows)
print(f"  {'Brand':<12} {'Total':>8} {'In Stock':>10}")
for brand,cnt,ins in rows:
    print(f"  {brand:<12} {cnt:>8} {ins or 0:>10}")
print(f"  {'TOTAL':<12} {total:>8}")
conn.close()

# --- HEALTH ---
section("HEALTH")
r = requests.get("http://localhost:8000/health")
ok("Health endpoint", r.status_code==200, r.text[:80])

section("DONE")
