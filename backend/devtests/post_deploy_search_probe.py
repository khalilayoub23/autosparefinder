"""
devtests/post_deploy_search_probe.py — post-deployment check of the live search API (FIXES_TRACKER #69 §16).

Calls the REAL HTTP endpoint (uvicorn on 127.0.0.1:8000 inside the backend container), so it measures API
latency, then checks every returned part INDEPENDENTLY against part_vehicle_fitment / compatible_vehicles
in a read-only, Redis-blocked session. The HTTP calls are ordinary customer-equivalent requests (they
populate the normal 120 s response cache with the deployed code's own results); this script itself writes
nothing.

Run ONLY after the deployment (on the old code a numeric-model search is an unbounded scan):
  docker exec -w /app autospare_backend python3 devtests/post_deploy_search_probe.py --out /tmp/post_deploy_probe.jsonl
Exit code: 0 = every stop condition clear; 2 = at least one STOP (printed).
STOP conditions: HTTP status != 200 on an expect-results case; any part outside the allowed models
(wildcard); an expect-empty case returning parts; latency > 30 s.
"""
import argparse
import asyncio
import json
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/devtests")

CASES = [  # make, model, year, category, expect
    ("Toyota", "Corolla", 2021, None, "results"),
    ("Toyota", "Corolla", 2021, "brakes", "results"),
    ("Honda", "Civic", 2017, None, "results"),
    ("Peugeot", "3008", 2019, None, "results"),
    ("Mazda", "MAZDA 3", 2018, None, "results"),
    ("MG", "MG4", 2023, None, "results"),
    ("Land Rover", "Range Rover Evoque", 2018, None, "results"),
]   # junk-model inputs are covered by devtests/numeric_model_e2e.py (they may hit the 30 s JSON scan)


def _ids(d):
    return [str((r.get("part") or {}).get("id")) for k in ("original_options", "oem_options", "aftermarket_options")
            for r in (d.get(k) or []) if (r.get("part") or {}).get("id")]


async def main(out, base):
    from search_fitment_comparison import isolate_process
    from numeric_model_e2e import _independent_check
    isolate_process()                                     # this process: read-only DB, no Redis
    import vehicle_model_match as vm
    from BACKEND_DATABASE_MODELS import async_session_factory
    from manufacturer_normalization import customer_vehicle_model_forms
    stops = []
    with open(out, "w", encoding="utf-8") as fh:
        for mk, md, yr, cat, expect in CASES:
            q = {"vehicle_manufacturer": mk, "vehicle_model": md, "vehicle_year": yr}
            if cat:
                q["category"] = cat
            url = f"{base}/api/v1/parts/search?" + urllib.parse.urlencode(q)
            t = time.monotonic()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "post-deploy-probe"}),
                                            timeout=60) as r:
                    status, body = r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                status, body = e.code, {}
            except Exception as e:
                status, body = f"{type(e).__name__}", {}
            api_s = round(time.monotonic() - t, 2)
            ids = _ids(body)
            forms = customer_vehicle_model_forms(mk, md)
            async with async_session_factory() as db:
                allowed = await vm.allowed_fitment_models(db, [mk], forms) if forms else []
            chk = await _independent_check(async_session_factory, ids, mk, yr, allowed, [f.lower() for f in forms])
            rec = {"case": [mk, md, yr, cat], "expect": expect, "http": status, "api_seconds": api_s, **chk}
            why = []
            if expect == "results" and status != 200:
                why.append(f"HTTP {status}")
            if chk.get("outside_allowed"):
                why.append(f"{chk['outside_allowed']} parts outside the allowed models")
            if expect == "empty" and ids:
                why.append(f"expected no parts, got {len(ids)}")
            if api_s > 30:
                why.append(f"latency {api_s}s > 30s")
            rec["stop"] = why
            if why:
                stops.append((rec["case"], why))
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            print(json.dumps({k: rec[k] for k in ("case", "http", "api_seconds", "parts", "outside_allowed", "stop")},
                             ensure_ascii=False), flush=True)
    print("STOP" if stops else "ALL CLEAR", stops)
    return 2 if stops else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.out, a.base)))
