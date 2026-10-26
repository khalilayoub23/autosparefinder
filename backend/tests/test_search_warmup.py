"""Startup search warm-up and the live external-supplier lookup it used to trigger.

Regression for 2026-10-09: every backend start logged HTTP 403 from PartSouq, Autodoc and Amayama.
Root cause: the warm-up calls search_parts(..., redis=None); for a non-empty query search_parts
started a background lookup against every external supplier whose ONLY output is a Redis cache entry
- with redis=None the answers (403s, and real eBay Browse API calls) were discarded. Also, startup
AWAITED the optional warm-up, holding API readiness behind it.

No network: supplier HTTP goes through httpx.MockTransport; the search function is a stub.
"""
import asyncio
import logging
import re
from pathlib import Path

import httpx
import pytest

import search_warmup
from routes.parts import _sanitize_external_offer, _should_fetch_external_suppliers

BACKEND = Path(__file__).resolve().parent.parent


# ── 1. the root cause: no cache sink -> no external lookup ───────────────────────
def test_external_lookup_needs_a_query_and_a_cache(monkeypatch):
    monkeypatch.setenv("ENABLE_EXTERNAL_SUPPLIERS", "1")
    assert _should_fetch_external_suppliers("engine", object()) is True
    assert _should_fetch_external_suppliers("engine", None) is False      # the warm-up / internal callers
    assert _should_fetch_external_suppliers("", object()) is False
    monkeypatch.setenv("ENABLE_EXTERNAL_SUPPLIERS", "0")
    assert _should_fetch_external_suppliers("engine", object()) is False


def test_search_parts_starts_the_external_lookup_only_through_the_gate():
    src = (BACKEND / "routes" / "parts.py").read_text(encoding="utf-8")
    body = src[src.index("async def search_parts("):src.index("# GET /api/v1/parts/categories")]
    assert body.count("asyncio.create_task(_fetch_ext_suppliers(") == 1
    gate = body.index("if _should_fetch_external_suppliers(query, redis):")
    assert gate < body.index("asyncio.create_task(_fetch_ext_suppliers(")
    assert 'os.getenv("ENABLE_EXTERNAL_SUPPLIERS"' not in body          # one rule, in the helper


# ── 2. the three upstream 403s: one request each, no retry, nothing customer-facing ──
def _mock_403(monkeypatch, module, seen):
    real = httpx.AsyncClient

    def factory(*a, **kw):
        def handler(request):
            seen.append(str(request.url.host))
            return httpx.Response(403, text="Forbidden")
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


@pytest.mark.parametrize("modname,cls,host", [
    ("services.suppliers.partsouq_supplier", "PartSouqSupplier", "partsouq.com"),
    ("services.suppliers.autodoc_supplier", "AutodocSupplier", "www.autodoc.eu"),
    ("services.suppliers.amayama_supplier", "AmayamaSupplier", "amayama.com"),
])
async def test_upstream_403_is_one_request_no_retry_and_never_a_customer_offer(monkeypatch, modname, cls, host):
    import importlib
    mod = importlib.import_module(modname)
    seen = []
    _mock_403(monkeypatch, mod, seen)
    results = await getattr(mod, cls)().search("ENGINE", limit=3)
    assert seen == [host]                                   # exactly one request, no retry loop
    offers = [_sanitize_external_offer(r.__dict__, 3.7) for r in results]
    assert all(o is None for o in offers)                   # 403 placeholders (price 0) are dropped


# ── 3. warm-up runner: classified, bounded, never raises, no external lookup ─────
class _Session:
    opened = 0

    async def __aenter__(self):
        _Session.opened += 1
        return object()

    async def __aexit__(self, *a):
        return False


CASES = [{"query": "engine", "timeout_s": 1}, {"query": "boom"}, {"query": "slow", "timeout_s": 0.05},
         {"query": "", "vehicle_manufacturer": "Toyota", "vehicle_model": "Corolla", "vehicle_year": 2018}]


async def test_warmup_classifies_each_case_and_never_raises(caplog):
    seen = []

    async def search_fn(**kw):
        seen.append(kw)
        if kw["query"] == "boom":
            raise RuntimeError("db went away")
        if kw["query"] == "slow":
            await asyncio.sleep(5)
        return {}

    _Session.opened = 0
    with caplog.at_level(logging.WARNING):
        res = await search_warmup.run_warmup_cases(CASES, search_fn, _Session, default_timeout_s=1)
    assert (res["total"], res["ok"], res["timeout"], res["error"]) == (4, 2, 1, 1)
    assert res["failures"] == [{"case": "boom", "kind": "error", "type": "RuntimeError"},
                               {"case": "slow", "kind": "timeout"}]
    assert _Session.opened == 4                                   # a fresh session per case
    assert all(kw["redis"] is None and kw["request"] is None for kw in seen)
    assert search_warmup.summary_line(res) == "[SearchWarmup] primed 2/4 search shapes (timeouts=1, errors=1)"
    text = caplog.text
    assert "error for boom: RuntimeError: db went away (non-fatal)" in text and "timeout after 0s for slow" in text


async def test_successful_warmup_reports_all_cases_primed():
    async def search_fn(**kw):
        return {"all_parts": []}

    res = await search_warmup.run_warmup_cases(CASES[:2], search_fn, _Session, default_timeout_s=1)
    assert res == {"total": 2, "ok": 2, "timeout": 0, "error": 0, "failures": []}


# ── 4. startup does not wait for the optional warm-up ───────────────────────────
def test_startup_schedules_the_warmup_in_the_background():
    src = (BACKEND / "BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*await _warm_search_paths\(\)", src, re.M)
    assert re.search(r'_SEARCH_WARMUP_TASK = asyncio\.create_task\(_guarded_task\(_warm_search_paths\(\)\)', src)
    warm = src[src.index("async def _warm_search_paths"):src.index("# _cart_to_response helper")]
    assert "run_warmup_cases(SEARCH_WARMUP_CASES, search_parts, async_session_factory" in warm
    assert "except Exception as exc:" in warm                       # outer guard kept: never raises


# ── 5. pre-deployment gate (2026-10-10): readiness, bounds, coverage, defaults ───
import ast  # noqa: E402

_API_SRC = (BACKEND / "BACKEND_API_ROUTES.py").read_text(encoding="utf-8")
_API_TREE = ast.parse(_API_SRC)


def _calls(node, name):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name]


def test_nothing_in_the_app_awaits_the_warmup():
    awaited = [n for n in ast.walk(_API_TREE) if isinstance(n, ast.Await) and _calls(n, "_warm_search_paths")]
    assert awaited == []                                   # no code path can hold readiness behind it
    calls = _calls(_API_TREE, "_warm_search_paths")
    assert len(calls) == 1                                 # one call site: the background task below
    tasks = [n for n in ast.walk(_API_TREE) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "create_task" and _calls(n, "_guarded_task") and _calls(n, "_warm_search_paths")]
    assert len(tasks) == 1


def test_warmup_function_cannot_raise_into_the_event_loop():
    fn = next(n for n in ast.walk(_API_TREE) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_warm_search_paths")
    outer = [n for n in fn.body if isinstance(n, ast.Try)]
    assert len(outer) == 1 and any(isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in outer[0].handlers)
    # everything after the enabled-check and the optional delay sits inside that try
    assert fn.body[-1] is outer[0]


def test_real_warmup_cases_have_a_finite_total_time_bound():
    assign = next(n for n in ast.walk(_API_TREE) if isinstance(n, ast.AnnAssign)
                  and isinstance(n.target, ast.Name) and n.target.id == "SEARCH_WARMUP_CASES")
    cases = ast.literal_eval(assign.value)
    default = float(re.search(r'SEARCH_WARMUP_QUERY_TIMEOUT_S", "(\d+)"', _API_SRC).group(1))
    timeouts = [float(c.get("timeout_s", default)) for c in cases]
    assert len(cases) == 6 and all(0 < t <= 120 for t in timeouts)
    assert sum(timeouts) == 232.0                           # worst case, in the background, once per start


async def test_each_case_is_attempted_exactly_once_even_when_it_fails():
    calls = []

    async def search_fn(**kw):
        calls.append(kw["query"])
        raise ConnectionError("upstream 403 / db down")

    res = await search_warmup.run_warmup_cases(CASES, search_fn, _Session, default_timeout_s=1)
    assert calls == [c.get("query", "") for c in CASES]      # no retry, no storm
    assert (res["ok"], res["error"], res["timeout"]) == (0, 4, 0)


async def test_scheduling_the_warmup_returns_before_it_finishes():
    done = asyncio.Event()

    async def slow_warmup():
        await asyncio.sleep(0.2)
        done.set()

    t0 = asyncio.get_running_loop().time()
    task = asyncio.create_task(slow_warmup())               # the same shape startup uses
    assert asyncio.get_running_loop().time() - t0 < 0.05 and not done.is_set()
    await task
    assert done.is_set()


def test_customer_search_default_is_preserved(monkeypatch):
    monkeypatch.delenv("ENABLE_EXTERNAL_SUPPLIERS", raising=False)
    # #100: the default is ON in production (unchanged) and OFF everywhere else (outbound_policy)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert _should_fetch_external_suppliers("brake pads", object()) is True     # production default ON with a cache
    monkeypatch.setenv("ENVIRONMENT", "sandbox")
    assert _should_fetch_external_suppliers("brake pads", object()) is False    # never live suppliers from a sandbox
    src = (BACKEND / "routes" / "parts.py").read_text(encoding="utf-8")
    # the public search route still receives a real Redis client
    sig = src[src.index("async def search_parts("):src.index('"""', src.index("async def search_parts("))]
    assert "redis=Depends(get_redis)" in sig


def test_every_search_parts_caller_is_covered_by_the_single_gate():
    callers = {}
    for path in list((BACKEND / "routes").glob("*.py")) + list((BACKEND / "maintenance").glob("*.py")) + \
            [BACKEND / "search_warmup.py"]:
        text_ = path.read_text(encoding="utf-8")
        n = len(re.findall(r"await search_parts\(|search_fn\(", text_))
        if n:
            callers[path.name] = n
    assert callers == {"parts.py": 3, "vehicles.py": 1, "run_fitment_step1_parity_check.py": 1, "search_warmup.py": 1}
    # and the cache-only external lookup exists nowhere else
    hits = [p.name for p in BACKEND.rglob("*.py")
            if "tests" not in p.parts and "_fetch_ext_suppliers" in p.read_text(encoding="utf-8", errors="ignore")]
    assert hits == ["parts.py"]
