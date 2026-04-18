#!/usr/bin/env bash
set -euo pipefail

# Run 100 concurrent users against key parts endpoints and save a JSON report.
# Default flow includes hierarchy + search in each virtual user.
#
# Optional env:
#   USERS=100
#   INCLUDE_SEARCH=1
#   BASE_URL=http://nginx
#   HOST_HEADER=autosparefinder.co.il
#   FORWARDED_PROTO=https
#   OUT_DIR=logs/loadtests

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

USERS="${USERS:-100}"
INCLUDE_SEARCH="${INCLUDE_SEARCH:-1}"
BASE_URL="${BASE_URL:-http://nginx}"
HOST_HEADER="${HOST_HEADER:-autosparefinder.co.il}"
FORWARDED_PROTO="${FORWARDED_PROTO:-https}"
OUT_DIR="${OUT_DIR:-logs/loadtests}"

mkdir -p "$OUT_DIR"
TS="$(date -u +"%Y%m%dT%H%M%SZ")"
OUT_FILE="${OUT_DIR}/loadtest_100_users_${TS}.json"

# Execute inside backend container so networking and dependencies are consistent.
docker compose exec -T backend python - <<'PY' > "$OUT_FILE"
import asyncio
import json
import os
import statistics
import time
from collections import Counter, defaultdict

import httpx

BASE = os.getenv("BASE_URL", "http://nginx")
HEADERS = {
    "Host": os.getenv("HOST_HEADER", "autosparefinder.co.il"),
    "X-Forwarded-Proto": os.getenv("FORWARDED_PROTO", "https"),
    "Accept": "application/json",
}
USERS = int(os.getenv("USERS", "100"))
INCLUDE_SEARCH = os.getenv("INCLUDE_SEARCH", "1") == "1"

VEHICLES = [
    ("Chevrolet", "TRAVERSE", 2022),
    ("Citroen", "BERLINGO", 2018),
    ("Peugeot", "BOXER3", 2018),
]
CATEGORIES = ["engine", "brakes", "fuel-air", "service-general"]


def p50(vals):
    return statistics.median(vals) if vals else None


def p95(vals):
    if not vals:
        return None
    xs = sorted(vals)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * 0.95
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] * (c - k) + xs[c] * (k - f)


async def one_user(uid: int, client: httpx.AsyncClient):
    mfr, model, year = VEHICLES[uid % len(VEHICLES)]
    category = CATEGORIES[uid % len(CATEGORIES)]

    flow = [
        ("models", f"/api/v1/parts/models?manufacturer={mfr}"),
        ("submodels", f"/api/v1/parts/submodels?manufacturer={mfr}&model={model}"),
        ("years", f"/api/v1/parts/years?manufacturer={mfr}&model={model}"),
    ]
    if INCLUDE_SEARCH:
        flow.append(
            (
                "search",
                f"/api/v1/parts/search?query=&page=1&per_page=20&vehicle_manufacturer={mfr}&vehicle_model={model}&vehicle_year={year}&category={category}",
            )
        )

    t0 = time.perf_counter()
    reqs = []
    for ep, path in flow:
        s = 0
        err = None
        st = time.perf_counter()
        try:
            r = await client.get(path, headers=HEADERS)
            s = r.status_code
        except Exception as ex:
            err = str(ex)
        reqs.append(
            {
                "endpoint": ep,
                "status": s,
                "ok": 200 <= s < 300,
                "latency_ms": (time.perf_counter() - st) * 1000,
                "error": err,
            }
        )

    return {
        "user_id": uid,
        "all_ok": all(x["ok"] for x in reqs),
        "user_latency_ms": (time.perf_counter() - t0) * 1000,
        "requests": reqs,
    }


async def main():
    timeout = httpx.Timeout(35.0, connect=5.0)
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=100)

    async with httpx.AsyncClient(base_url=BASE, timeout=timeout, limits=limits) as client:
        start = time.perf_counter()
        results = await asyncio.gather(*[one_user(i, client) for i in range(USERS)])
        wall_ms = (time.perf_counter() - start) * 1000

    endpoint_lat = defaultdict(list)
    endpoint_status = defaultdict(Counter)
    endpoint_ok = defaultdict(int)
    endpoint_total = defaultdict(int)

    users_ok = 0
    user_lat = []
    for u in results:
        users_ok += 1 if u["all_ok"] else 0
        user_lat.append(u["user_latency_ms"])
        for r in u["requests"]:
            ep = r["endpoint"]
            endpoint_total[ep] += 1
            endpoint_lat[ep].append(r["latency_ms"])
            endpoint_status[ep][r["status"]] += 1
            endpoint_ok[ep] += 1 if r["ok"] else 0

    per_endpoint = {}
    for ep in sorted(endpoint_total.keys()):
        vals = endpoint_lat[ep]
        per_endpoint[ep] = {
            "requests": endpoint_total[ep],
            "ok": endpoint_ok[ep],
            "ok_rate": round(endpoint_ok[ep] / endpoint_total[ep], 4),
            "status_counts": dict(endpoint_status[ep]),
            "latency_ms": {
                "p50": round(p50(vals), 1),
                "p95": round(p95(vals), 1),
                "max": round(max(vals), 1),
            },
        }

    print(
        json.dumps(
            {
                "timestamp": time.time(),
                "users": USERS,
                "include_search": INCLUDE_SEARCH,
                "wall_time_ms": round(wall_ms, 1),
                "users_all_ok": users_ok,
                "users_all_ok_rate": round(users_ok / USERS, 4),
                "user_latency_ms": {
                    "p50": round(p50(user_lat), 1),
                    "p95": round(p95(user_lat), 1),
                    "max": round(max(user_lat), 1),
                },
                "per_endpoint": per_endpoint,
            },
            ensure_ascii=False,
        )
    )


asyncio.run(main())
PY

echo "load_test_report=${OUT_FILE}"
python3 - <<PY
import json
with open("$OUT_FILE", "r", encoding="utf-8") as f:
    d = json.load(f)
print(f"users={d['users']} all_ok_rate={d['users_all_ok_rate']:.3f} wall_ms={d['wall_time_ms']}")
for ep, x in d.get("per_endpoint", {}).items():
    print(f"{ep}: ok_rate={x['ok_rate']:.3f} p50={x['latency_ms']['p50']} p95={x['latency_ms']['p95']} statuses={x['status_counts']}")
PY
