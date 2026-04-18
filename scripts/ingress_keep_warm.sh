#!/usr/bin/env bash
set -euo pipefail

# Low-impact ingress keep-warm for parts search + hierarchy endpoints.
# Designed to reduce first-hit spikes without adding meaningful steady load.
#
# Usage:
#   ./scripts/ingress_keep_warm.sh
#
# Optional env:
#   BASE_URL=http://127.0.0.1
#   HOST_HEADER=autosparefinder.co.il
#   FORWARDED_PROTO=https
#   VEHICLES="Chevrolet|TRAVERSE|2022;Citroen|BERLINGO|2018;Peugeot|BOXER3|2018"
#   CATEGORIES="engine,brakes"
#   INCLUDE_HIERARCHY=1
#   MAX_REQUESTS=24
#   MIN_INTERVAL_S=2.2
#   JITTER_MAX_MS=350
#   TIMEOUT_S=30
#   LOG_JSON=0

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

BASE_URL="${BASE_URL:-http://127.0.0.1}"
HOST_HEADER="${HOST_HEADER:-autosparefinder.co.il}"
FORWARDED_PROTO="${FORWARDED_PROTO:-https}"
VEHICLES="${VEHICLES:-Chevrolet|TRAVERSE|2022;Citroen|BERLINGO|2018;Peugeot|BOXER3|2018}"
CATEGORIES="${CATEGORIES:-engine,brakes}"
INCLUDE_HIERARCHY="${INCLUDE_HIERARCHY:-1}"
MAX_REQUESTS="${MAX_REQUESTS:-24}"
MIN_INTERVAL_S="${MIN_INTERVAL_S:-2.2}"
JITTER_MAX_MS="${JITTER_MAX_MS:-350}"
TIMEOUT_S="${TIMEOUT_S:-30}"
LOG_JSON="${LOG_JSON:-0}"

export BASE_URL HOST_HEADER FORWARDED_PROTO VEHICLES CATEGORIES INCLUDE_HIERARCHY
export MAX_REQUESTS MIN_INTERVAL_S JITTER_MAX_MS TIMEOUT_S LOG_JSON

if ! command -v curl >/dev/null 2>&1; then
  echo "[keep-warm] curl is required"
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "[keep-warm] python3 is required"
  exit 1
fi

# Use python for URL encoding and precise jitter/interval handling.
python3 - <<'PY'
import json
import os
import random
import time
import urllib.parse
import urllib.request

base_url = os.environ["BASE_URL"].rstrip("/")
host_header = os.environ["HOST_HEADER"]
forwarded_proto = os.environ["FORWARDED_PROTO"]
vehicles_raw = os.environ["VEHICLES"]
categories_raw = os.environ["CATEGORIES"]
include_hierarchy = os.environ["INCLUDE_HIERARCHY"] == "1"
max_requests = max(1, int(os.environ["MAX_REQUESTS"]))
min_interval_s = max(0.0, float(os.environ["MIN_INTERVAL_S"]))
jitter_max_ms = max(0, int(os.environ["JITTER_MAX_MS"]))
timeout_s = max(1, int(os.environ["TIMEOUT_S"]))
log_json = os.environ["LOG_JSON"] == "1"

vehicles = []
for item in [x.strip() for x in vehicles_raw.split(";") if x.strip()]:
    parts = [x.strip() for x in item.split("|")]
    if len(parts) != 3:
        continue
    mfr, model, year_s = parts
    try:
        year = int(year_s)
    except ValueError:
        continue
    vehicles.append((mfr, model, year))

categories = [x.strip() for x in categories_raw.split(",") if x.strip()]
if not vehicles or not categories:
    raise SystemExit("[keep-warm] no valid vehicles/categories configured")

headers = {
    "Host": host_header,
    "X-Forwarded-Proto": forwarded_proto,
    "Accept": "application/json",
}

routes = []
for mfr, model, year in vehicles:
    if include_hierarchy:
        routes.append(("models", f"/api/v1/parts/models?manufacturer={urllib.parse.quote(mfr, safe='')}", mfr, model, year, ""))
        routes.append(("submodels", f"/api/v1/parts/submodels?manufacturer={urllib.parse.quote(mfr, safe='')}&model={urllib.parse.quote(model, safe='')}", mfr, model, year, ""))
        routes.append(("years", f"/api/v1/parts/years?manufacturer={urllib.parse.quote(mfr, safe='')}&model={urllib.parse.quote(model, safe='')}", mfr, model, year, ""))

    for cat in categories:
        q = urllib.parse.urlencode(
            {
                "query": "",
                "page": 1,
                "per_page": 20,
                "vehicle_manufacturer": mfr,
                "vehicle_model": model,
                "vehicle_year": year,
                "category": cat,
            }
        )
        routes.append(("search", f"/api/v1/parts/search?{q}", mfr, model, year, cat))

if len(routes) > max_requests:
    random.shuffle(routes)
    routes = routes[:max_requests]

records = []
last_start = 0.0
for idx, (kind, path, mfr, model, year, cat) in enumerate(routes, start=1):
    if last_start:
        elapsed = time.perf_counter() - last_start
        to_sleep = min_interval_s - elapsed
        if to_sleep > 0:
            time.sleep(to_sleep)

    if jitter_max_ms > 0:
        time.sleep(random.uniform(0, jitter_max_ms / 1000.0))

    req = urllib.request.Request(base_url + path, headers=headers)
    t0 = time.perf_counter()
    status = 0
    error = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = resp.status
            _ = resp.read(64)
    except Exception as exc:
        error = str(exc)
    dt_ms = (time.perf_counter() - t0) * 1000.0
    last_start = time.perf_counter()

    rec = {
        "idx": idx,
        "kind": kind,
        "manufacturer": mfr,
        "model": model,
        "year": year,
        "category": cat,
        "status": status,
        "latency_ms": round(dt_ms, 1),
        "ok": 200 <= status < 300,
    }
    if error:
        rec["error"] = error
    records.append(rec)

ok_count = sum(1 for r in records if r["ok"])
summary = {
    "total": len(records),
    "ok": ok_count,
    "fail": len(records) - ok_count,
    "base_url": base_url,
    "host": host_header,
    "include_hierarchy": include_hierarchy,
    "max_requests": max_requests,
    "min_interval_s": min_interval_s,
    "jitter_max_ms": jitter_max_ms,
}

if log_json:
    print(json.dumps({"summary": summary, "records": records}, ensure_ascii=False))
else:
    print(
        "[keep-warm] total={total} ok={ok} fail={fail} min_interval_s={min_interval_s} jitter_max_ms={jitter_max_ms}".format(
            **summary
        )
    )
    for r in records:
        print(
            "[keep-warm] #{idx:02d} {kind:9s} {manufacturer}/{model}/{year} {category:12s} status={status} latency_ms={latency_ms}".format(
                idx=r["idx"],
                kind=r["kind"],
                manufacturer=r["manufacturer"],
                model=r["model"],
                year=r["year"],
                category=(r["category"] or "-"),
                status=r["status"],
                latency_ms=r["latency_ms"],
            )
        )
PY
