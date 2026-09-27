"""
Script: aliexpress_token_persistence_test.py
Purpose: Mocked (NO real AliExpress authorization) verification of encrypted OAuth token
         persistence: callback -> DB (suppliers.credentials) -> load -> auto-refresh.
Process: creates a throwaway supplier row, monkeypatches the row name + the AliExpress HTTP
         calls, asserts tokens never appear in HTTP body / logs / raw JSONB, then deletes the row.
Data Imported/Modified: temporary row in `suppliers` (deleted in finally). Real AliExpress row untouched.
Last Updated: 2026-09-21
Run: docker exec autospare_backend python3 /app/devtests/aliexpress_token_persistence_test.py
"""
import asyncio, io, json, logging, subprocess, sys, time
import sqlalchemy as sa

import services.suppliers.aliexpress_supplier as m
from services.suppliers.aliexpress_supplier import AliExpressSupplier
from BACKEND_DATABASE_MODELS import async_session_factory
import routes.suppliers as rs
import routes.auth as ra
import httpx
from fastapi import FastAPI

ROW = "ZZ_ALI_TOKEN_TEST"
m._SUPPLIER_ROW_NAME = ROW
A1, R1 = "FAKEACCESS1_" + "a" * 30, "FAKEREFRESH1_" + "b" * 30
A2, R2 = "FAKEACCESS2_" + "c" * 30, "FAKEREFRESH2_" + "d" * 30
SECRETS = [A1, R1, A2, R2]
results = []
def check(name, ok):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name)

logbuf = io.StringIO()
h = logging.StreamHandler(logbuf); logging.getLogger().addHandler(h); logging.getLogger().setLevel(logging.DEBUG)

async def raw_creds():
    async with async_session_factory() as db:
        r = (await db.execute(sa.text("SELECT credentials::text FROM suppliers WHERE name=:n"), {"n": ROW})).fetchone()
        return r[0] if r else None

class FakeResp:
    status_code = 200
    def __init__(self, d): self._d = d
    def json(self): return self._d
    def raise_for_status(self): pass

async def main():
    async with async_session_factory() as db:
        await db.execute(sa.text("DELETE FROM suppliers WHERE name=:n"), {"n": ROW})
        await db.execute(sa.text("INSERT INTO suppliers (id,name,is_active) VALUES (gen_random_uuid(),:n,false)"), {"n": ROW})
        await db.commit()
    try:
        # 1-3: callback success path with mocked exchange
        exp1 = int(time.time() * 1000) - 3600_000   # already expired -> forces refresh later
        async def fake_exchange(self, code):
            return {"access_token": A1, "refresh_token": R1, "expire_time": exp1}
        orig = AliExpressSupplier.exchange_code_for_token
        _ORIG_EXCHANGE = orig
        app = FastAPI(); app.include_router(ra.router); app.include_router(rs.router)
        cl = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        AliExpressSupplier.exchange_code_for_token = fake_exchange
        try:
            r_get = await cl.get("/api/aliexpress/callback", params={"code": "mock"})
            r_form = await cl.post("/api/aliexpress/callback", data={"code": "mock"})
            r_json = await cl.post("/api/aliexpress/callback", json={"code": "mock"})
            r_q_post = await cl.post("/api/aliexpress/callback?code=mock")
            r_sup_get = await cl.get("/api/suppliers/aliexpress/callback", params={"code": "mock"})
            r_sup_post = await cl.post("/api/suppliers/aliexpress/callback", data={"code": "mock"})
        finally:
            AliExpressSupplier.exchange_code_for_token = _ORIG_EXCHANGE
        for name, r in [("GET /api/aliexpress/callback", r_get), ("POST form /api/...", r_form), ("POST json /api/...", r_json),
                        ("POST query /api/...", r_q_post), ("GET /api/suppliers/...", r_sup_get), ("POST /api/suppliers/...", r_sup_post)]:
            check(f"{name} -> 200 masked HTML", r.status_code == 200 and "text/html" in r.headers.get("content-type", "")
                  and "stored (encrypted)" in r.text and not any(x in r.text for x in SECRETS))
        check("legacy raw-token JSON response gone", "access_token\":" not in r_get.text and "refresh_token\":" not in r_get.text)
        r_none = await cl.get("/api/aliexpress/callback"); r_none_p = await cl.post("/api/aliexpress/callback")
        check("missing code -> 400 (GET and POST)", r_none.status_code == 400 and r_none_p.status_code == 400)
        r_err = await cl.get("/api/aliexpress/callback", params={"error": "<b>x</b>"})
        check("error param escaped", r_err.status_code == 400 and "<b>" not in r_err.text)
        await cl.aclose()
        resp = r_get
        body = resp.text
        raw = await raw_creds()
        check("persisted: enc blob + expire_time present", '"enc"' in raw and str(exp1) in raw)
        check("persisted JSONB holds no plaintext tokens", not any(s in raw for s in SECRETS))

        # 4: fresh instance loads from DB (env tokens are stale/different)
        s2 = AliExpressSupplier()
        check("fresh instance starts without test tokens", s2._access_token != A1)
        check("load_persisted applies", await s2._load_persisted())
        check("loaded access+refresh+expiry match", (s2._access_token, s2._refresh_token, s2._token_expire) == (A1, R1, exp1))

        # 5: refresh path uses persisted creds, no /app/.env
        posted = {}
        async def fake_post(self, url, data=None, **kw):
            posted.update(data or {})
            return FakeResp({"access_token": A2, "refresh_token": R2, "expire_time": int(time.time()*1000) + 30*86400*1000})
        orig_post = httpx.AsyncClient.post
        httpx.AsyncClient.post = fake_post
        s3 = AliExpressSupplier()
        ready = await s3._ensure_token()          # loads persisted (expired) -> auto-refresh
        httpx.AsyncClient.post = orig_post
        check("refresh used the PERSISTED refresh_token", posted.get("refresh_token") == R1)
        check("refresh yields new in-memory token", s3._access_token == A2 and ready)
        s4 = AliExpressSupplier()
        await s4._load_persisted()
        check("refreshed tokens re-persisted (fresh load sees A2/R2)", (s4._access_token, s4._refresh_token) == (A2, R2))
        import os
        check("no /app/.env required", not os.path.exists("/app/.env"))
        raw2 = await raw_creds()
        check("post-refresh JSONB has no plaintext tokens", not any(s in raw2 for s in SECRETS))

        # 6: cross-process durability
        code = ("import asyncio,services.suppliers.aliexpress_supplier as m;m._SUPPLIER_ROW_NAME=%r;"
                "s=m.AliExpressSupplier();ok=asyncio.run(s._load_persisted());print('OK' if ok and s._access_token==%r else 'NO')") % (ROW, A2)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd="/app").stdout
        check("separate process loads tokens from DB", "OK" in out)

        # 7: logs
        check("no token values in logs", not any(s in logbuf.getvalue() for s in SECRETS))
    finally:
        async with async_session_factory() as db:
            await db.execute(sa.text("DELETE FROM suppliers WHERE name=:n"), {"n": ROW}); await db.commit()
        print("cleanup: test row removed")
    print("RESULT", "ALL PASS" if all(results) else "FAILURES", f"({sum(results)}/{len(results)})")

asyncio.run(main())
