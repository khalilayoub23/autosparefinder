"""/api/v1/brands/with-parts: two full-catalogue GROUP BY scans (10.8-15.9 s) ran on every
request with no cache (FIXES_TRACKER #69 §11). Cached 10 min + single-flight."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from routes import brands  # noqa: E402


def test_concurrent_and_repeat_requests_run_the_scan_once(monkeypatch):
    calls = {"n": 0}

    async def _scan(_db):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return {"brands": [{"name": "Toyota"}], "total": 1}

    monkeypatch.setattr(brands, "_brands_with_parts_uncached", _scan)
    monkeypatch.setitem(brands._brands_with_parts_cache, "value", None)
    monkeypatch.setattr(brands, "_BRANDS_WITH_PARTS_LOCK", asyncio.Lock())

    async def go():
        brands._BRANDS_WITH_PARTS_LOCK = asyncio.Lock()          # bind to this loop
        res = await asyncio.gather(*(brands.get_brands_with_parts(db=None) for _ in range(5)))
        res.append(await brands.get_brands_with_parts(db=None))
        return res

    out = asyncio.run(go())
    assert calls["n"] == 1
    assert all(r == {"brands": [{"name": "Toyota"}], "total": 1} for r in out)


def test_cache_expires(monkeypatch):
    calls = {"n": 0}

    async def _scan(_db):
        calls["n"] += 1
        return {"brands": [], "total": 0}

    monkeypatch.setattr(brands, "_brands_with_parts_uncached", _scan)
    monkeypatch.setitem(brands._brands_with_parts_cache, "value", None)

    async def go():
        brands._BRANDS_WITH_PARTS_LOCK = asyncio.Lock()
        await brands.get_brands_with_parts(db=None)
        brands._brands_with_parts_cache["at"] -= brands._BRANDS_WITH_PARTS_TTL_S + 1
        await brands.get_brands_with_parts(db=None)

    asyncio.run(go())
    assert calls["n"] == 2
