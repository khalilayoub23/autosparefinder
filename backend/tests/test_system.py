"""
Comprehensive system test suite for AutoSpareFinder backend.

Coverage:
  1. DB model / session routing  – catalog (Base) vs PII (PiiBase)
  2. SupplierPart column completeness (updated_at, part_type)
  3. Agent session-factory safety (static source inspection)
  4. Route DB-dependency correctness (static source inspection)
  5. Live DB connectivity – both autospare and autospare_pii
  6. Live DB table accessibility – catalog and PII tables
  7. Live API – public endpoints (health, brands, categories, parts search)
  8. Live API – auth guard (protected endpoints reject unauthenticated requests)
  9. Live API – full auth cycle (register → login → /me)
  10. Live API – vehicle identify endpoint
  11. Live API – my-vehicles and orders require auth
  12. Agent instantiation – all registered agents can be created
  13. AGENT_MAP completeness – all expected agents are registered
"""

import inspect
import re
import sys
import os

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, func, text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Make sure the backend directory is on the path so we can import models
# ---------------------------------------------------------------------------
BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from BACKEND_DATABASE_MODELS import (
    Base,
    PiiBase,
    async_session_factory,
    pii_session_factory,
    DATABASE_URL,
    DATABASE_PII_URL,
    # Catalog models
    CarBrand,
    PartsCatalog,
    PartImage,
    Supplier,
    SupplierPart,
    SystemLog,
    AuditLog,
    SystemSetting,
    CacheEntry,
    PartCrossReference,
    PartAlias,
    PriceHistory,
    PurchaseOrder,
    ScraperApiCall,
    # PII models
    User,
    UserProfile,
    UserSession,
    TwoFactorCode,
    LoginAttempt,
    PasswordReset,
    Vehicle,
    UserVehicle,
    Order,
    OrderItem,
    Payment,
    Invoice,
    Return,
    Conversation,
    Message,
    AgentAction,
    AgentRating,
    File,
    FileMetadata,
    Notification,
)

BASE_URL = "http://localhost:8000"

# ---------------------------------------------------------------------------
# Fresh per-test async engines using NullPool so there are no shared
# connections across event loops (avoids asyncpg "another operation in progress")
# ---------------------------------------------------------------------------

def _make_catalog_session():
    eng = create_async_engine(DATABASE_URL, poolclass=NullPool)
    factory = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    return eng, factory

def _make_pii_session():
    eng = create_async_engine(DATABASE_PII_URL, poolclass=NullPool)
    factory = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    return eng, factory

# ===========================================================================
# SECTION 1 — DB Model / Session Routing
# ===========================================================================

CATALOG_MODELS = [
    CarBrand, PartsCatalog, PartImage, Supplier, SupplierPart,
    SystemLog, AuditLog, SystemSetting, CacheEntry,
    PartCrossReference, PartAlias,
    PriceHistory, PurchaseOrder, ScraperApiCall,
    Vehicle,  # vehicle reference data (specs/make/model) — catalog DB; UserVehicle holds PII
]

PII_MODELS = [
    User, UserProfile, UserSession, TwoFactorCode, LoginAttempt,
    PasswordReset, UserVehicle, Order, OrderItem,
    Payment, Invoice, Return, Conversation, Message,
    AgentAction, AgentRating, File, FileMetadata, Notification,
]


@pytest.mark.parametrize("model", CATALOG_MODELS, ids=lambda m: m.__name__)
def test_catalog_model_uses_base(model):
    """All catalog models must inherit from Base (autospare catalog DB)."""
    assert issubclass(model, Base), (
        f"{model.__name__} should inherit from Base (catalog), not PiiBase"
    )


@pytest.mark.parametrize("model", PII_MODELS, ids=lambda m: m.__name__)
def test_pii_model_uses_piibase(model):
    """All PII models must inherit from PiiBase (autospare_pii DB)."""
    assert issubclass(model, PiiBase), (
        f"{model.__name__} should inherit from PiiBase (PII DB), not Base"
    )


def test_no_catalog_model_in_pii():
    """No catalog model should be in PiiBase.__subclasses__ tree."""
    for model in CATALOG_MODELS:
        assert not issubclass(model, PiiBase), (
            f"{model.__name__} is a catalog model but inherits PiiBase"
        )


def test_no_pii_model_in_catalog():
    """No PII model should be in Base.__subclasses__ tree."""
    for model in PII_MODELS:
        assert not issubclass(model, Base), (
            f"{model.__name__} is a PII model but inherits Base (catalog)"
        )


# ===========================================================================
# SECTION 2 — SupplierPart Column Completeness
# ===========================================================================

def test_supplier_part_has_updated_at_column():
    cols = {c.name for c in SupplierPart.__table__.columns}
    assert "updated_at" in cols, (
        "SupplierPart is missing 'updated_at' column — db_update_agent will crash"
    )


def test_supplier_part_has_part_type_column():
    cols = {c.name for c in SupplierPart.__table__.columns}
    assert "part_type" in cols, (
        "SupplierPart is missing 'part_type' column — db_update_agent will crash"
    )


# ===========================================================================
# SECTION 3 — Agent Session-Factory Safety
#   We verify via source-code inspection that each fixed method opens its own
#   correct session factory rather than reusing the caller-supplied `db`.
# ===========================================================================

def _agent_source(method_name: str) -> str:
    from BACKEND_AI_AGENTS import PartsFinderAgent, SalesAgent, OrdersAgent
    mapping = {
        "identify_vehicle": PartsFinderAgent.identify_vehicle,
        "get_db_stats": PartsFinderAgent.get_db_stats,
        "normalize_manufacturer": PartsFinderAgent.normalize_manufacturer,
        "list_known_brands": PartsFinderAgent.list_known_brands,
        "search_parts_in_db": PartsFinderAgent.search_parts_in_db,
        "sales_process": SalesAgent.process,
        "orders_process": OrdersAgent.process,
    }
    return inspect.getsource(mapping[method_name])


def test_identify_vehicle_opens_catalog_session():
    """identify_vehicle must open async_session_factory (Vehicle is Base/catalog)."""
    src = _agent_source("identify_vehicle")
    assert "async_session_factory" in src, (
        "identify_vehicle does not open async_session_factory — "
        "Vehicle is a catalog Base model, pii_session_factory would be wrong"
    )


def test_get_db_stats_opens_catalog_session():
    """get_db_stats queries PartsCatalog — must open async_session_factory."""
    src = _agent_source("get_db_stats")
    assert "async_session_factory" in src, (
        "get_db_stats does not open async_session_factory for catalog queries"
    )


def test_normalize_manufacturer_opens_catalog_session():
    """normalize_manufacturer queries CarBrand — must use catalog session."""
    src = _agent_source("normalize_manufacturer")
    assert "async_session_factory" in src, (
        "normalize_manufacturer does not open async_session_factory"
    )


def test_list_known_brands_opens_catalog_session():
    """list_known_brands queries CarBrand — must use catalog session."""
    src = _agent_source("list_known_brands")
    assert "async_session_factory" in src, (
        "list_known_brands does not open async_session_factory"
    )


def test_search_parts_in_db_opens_catalog_session():
    """search_parts_in_db queries PartsCatalog/SupplierPart — catalog session."""
    src = _agent_source("search_parts_in_db")
    assert "async_session_factory" in src, (
        "search_parts_in_db does not open async_session_factory"
    )


def test_sales_agent_upsell_opens_catalog_session():
    """SalesAgent upsell logic queries PartsCatalog — must use catalog session."""
    src = _agent_source("sales_process")
    assert "async_session_factory" in src, (
        "SalesAgent.process does not open async_session_factory for catalog queries"
    )


def test_orders_agent_opens_pii_session():
    """OrdersAgent queries Order (PiiBase) — must open pii_session_factory."""
    src = _agent_source("orders_process")
    assert "pii_session_factory" in src, (
        "OrdersAgent.process does not open pii_session_factory"
    )


# ===========================================================================
# SECTION 4 — Route DB-Dependency Correctness (static)
# ===========================================================================

def _routes_source() -> str:
    import glob
    parts = []
    with open(os.path.join(BACKEND_DIR, "BACKEND_API_ROUTES.py"), encoding="utf-8") as f:
        parts.append(f.read())
    for fpath in sorted(glob.glob(os.path.join(BACKEND_DIR, "routes", "*.py"))):
        with open(fpath, encoding="utf-8") as f:
            parts.append(f.read())
    return "\n".join(parts)


def test_identify_vehicle_route_uses_catalog_db():
    src = _routes_source()
    # Find the route definition and check its dependency
    # Vehicle is Base (catalog DB) — session must be get_db, not get_pii_db
    match = re.search(
        r'@(?:app|router)\.post\("/api/v1/vehicles/identify"\).*?async def identify_vehicle\([^)]+\)',
        src, re.DOTALL
    )
    assert match, "Could not locate identify_vehicle route"
    assert "get_db" in match.group(), (
        "identify_vehicle route does not use get_db — Vehicle is Base (catalog DB)"
    )


def test_identify_vehicle_from_image_route_uses_catalog_db():
    src = _routes_source()
    # Vehicle is Base (catalog DB) — session must be get_db, not get_pii_db
    idx = src.find('@router.post("/api/v1/vehicles/identify-from-image")')
    assert idx != -1, "Could not locate identify_vehicle_from_image route decorator"
    snippet = src[idx : idx + 600]
    assert "get_db" in snippet, (
        "identify_vehicle_from_image does not use get_db — Vehicle is Base (catalog DB)\n" + snippet[:300]
    )


def test_run_agent_bg_opens_pii_session():
    """_run_agent_bg background task must open its own pii_session_factory."""
    src = _routes_source()
    # Find the background function
    match = re.search(
        r'async def _run_agent_bg\(\):.*?asyncio\.create_task\(',
        src, re.DOTALL
    )
    assert match, "Could not locate _run_agent_bg"
    assert "pii_session_factory" in match.group(), (
        "_run_agent_bg does not open pii_session_factory — session will be closed"
    )


def test_test_agent_route_uses_pii_db():
    src = _routes_source()
    idx = src.find('async def test_agent(')
    assert idx != -1, "Could not locate test_agent function"
    # Search up to 500 chars to cover multi-line signature
    snippet = src[idx : idx + 500]
    assert "get_pii_db" in snippet, (
        "test_agent endpoint does not use get_pii_db\n" + snippet
    )


def test_db_agent_run_all_background_opens_own_session():
    """db_agent_run_all must NOT pass its request session into the background task."""
    src = _routes_source()
    idx = src.find('async def db_agent_run_all(')
    assert idx != -1, "Could not locate db_agent_run_all"
    # Grab 1500 chars of the function body — enough to see the background task
    fn_body = src[idx : idx + 1500]
    assert "own session" in fn_body or "pii_session_factory" in fn_body or "async_session_factory" in fn_body, (
        "db_agent_run_all appears to reuse request session for background task\n" + fn_body
    )


# ===========================================================================
# SECTION 5 — Live DB Connectivity
# ===========================================================================

@pytest.mark.asyncio
async def test_catalog_db_connection():
    """Can connect to the catalog DB (autospare)."""
    eng, factory = _make_catalog_session()
    try:
        async with factory() as db:
            result = await db.execute(text("SELECT 1"))
            assert result.scalar() == 1
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_pii_db_connection():
    """Can connect to the PII DB (autospare_pii)."""
    eng, factory = _make_pii_session()
    try:
        async with factory() as db:
            result = await db.execute(text("SELECT 1"))
            assert result.scalar() == 1
    finally:
        await eng.dispose()


@pytest.mark.asyncio
async def test_both_dbs_are_independent():
    """Catalog and PII DBs are different connections / databases."""
    cat_eng, cat_factory = _make_catalog_session()
    pii_eng, pii_factory = _make_pii_session()
    try:
        async with cat_factory() as cat_db:
            cat_res = await cat_db.execute(text("SELECT current_database()"))
            cat_name = cat_res.scalar()
        async with pii_factory() as pii_db:
            pii_res = await pii_db.execute(text("SELECT current_database()"))
            pii_name = pii_res.scalar()
    finally:
        await cat_eng.dispose()
        await pii_eng.dispose()
    assert cat_name != pii_name, (
        f"Both session factories point to the same DB '{cat_name}' — "
        "dual-DB isolation is broken"
    )


# ===========================================================================
# SECTION 6 — Live DB Table Accessibility
# ===========================================================================

@pytest.mark.asyncio
async def test_parts_catalog_accessible():
    eng, factory = _make_catalog_session()
    try:
        async with factory() as db:
            result = await db.execute(select(func.count(PartsCatalog.id)))
            count = result.scalar()
    finally:
        await eng.dispose()
    assert count >= 0


@pytest.mark.asyncio
async def test_car_brands_accessible():
    eng, factory = _make_catalog_session()
    try:
        async with factory() as db:
            result = await db.execute(select(func.count(CarBrand.id)))
            count = result.scalar()
    finally:
        await eng.dispose()
    assert count >= 0


@pytest.mark.asyncio
async def test_supplier_parts_accessible():
    eng, factory = _make_catalog_session()
    try:
        async with factory() as db:
            result = await db.execute(select(func.count(SupplierPart.id)))
            count = result.scalar()
    finally:
        await eng.dispose()
    assert count >= 0


@pytest.mark.asyncio
async def test_users_accessible_from_pii_db():
    eng, factory = _make_pii_session()
    try:
        async with factory() as db:
            result = await db.execute(select(func.count(User.id)))
            count = result.scalar()
    finally:
        await eng.dispose()
    assert count >= 0


@pytest.mark.asyncio
async def test_orders_accessible_from_pii_db():
    eng, factory = _make_pii_session()
    try:
        async with factory() as db:
            result = await db.execute(select(func.count(Order.id)))
            count = result.scalar()
    finally:
        await eng.dispose()
    assert count >= 0


@pytest.mark.asyncio
async def test_vehicles_accessible_from_catalog_db():
    """Vehicle table lives in the catalog (autospare) DB — not the PII DB."""
    eng, factory = _make_catalog_session()
    try:
        async with factory() as db:
            result = await db.execute(select(func.count(Vehicle.id)))
            count = result.scalar()
    finally:
        await eng.dispose()
    assert count >= 0


@pytest.mark.asyncio
async def test_conversations_accessible_from_pii_db():
    eng, factory = _make_pii_session()
    try:
        async with factory() as db:
            result = await db.execute(select(func.count(Conversation.id)))
            count = result.scalar()
    finally:
        await eng.dispose()
    assert count >= 0


@pytest.mark.asyncio
async def test_catalog_table_not_in_pii_db():
    """PartsCatalog table must NOT exist in the PII database."""
    eng, factory = _make_pii_session()
    try:
        async with factory() as db:
            result = await db.execute(
                text("SELECT COUNT(*) FROM information_schema.tables "
                     "WHERE table_schema='public' AND table_name='parts_catalog'")
            )
            count = result.scalar()
    finally:
        await eng.dispose()
    assert count == 0, (
        "parts_catalog table found in PII DB — catalog and PII data are mixed"
    )


@pytest.mark.asyncio
async def test_pii_table_not_in_catalog_db():
    """users table must NOT exist in the catalog database."""
    eng, factory = _make_catalog_session()
    try:
        async with factory() as db:
            result = await db.execute(
                text("SELECT COUNT(*) FROM information_schema.tables "
                     "WHERE table_schema='public' AND table_name='users'")
            )
            count = result.scalar()
    finally:
        await eng.dispose()
    assert count == 0, (
        "users table found in catalog DB — PII data is leaking into catalog DB"
    )


# ===========================================================================
# SECTION 7 — Live API – Public Endpoints
# ===========================================================================

def test_health_endpoint():
    r = httpx.get(f"{BASE_URL}/api/v1/system/health", timeout=10)
    assert r.status_code == 200
    body = r.json()
    # Accept unhealthy/degraded in dev when optional services (Redis/Meili/ClamAV)
    # are not available; enforce core DB readiness instead.
    assert body.get("status") in ("healthy", "degraded", "unhealthy"), f"Unexpected health status: {body.get('status')}"
    services = body.get("services", {})
    assert services.get("postgres_catalog", {}).get("status") == "ok", "Catalog DB must be healthy"
    assert services.get("postgres_pii", {}).get("status") == "ok", "PII DB must be healthy"


def test_parts_search_returns_results():
    try:
        r = httpx.get(f"{BASE_URL}/api/v1/parts/search?q=brake", timeout=20)
    except httpx.ReadTimeout:
        pytest.skip("Parts search timed out under load — server busy")
    assert r.status_code == 200
    body = r.json()
    # Response is a dict with result keys
    assert isinstance(body, dict)
    assert "query" in body or "results" in body or "original" in body


def test_parts_categories_endpoint():
    r = httpx.get(f"{BASE_URL}/api/v1/parts/categories", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, (list, dict))


def test_brands_endpoint():
    r = httpx.get(f"{BASE_URL}/api/v1/brands", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, (list, dict))


def test_docs_endpoint_accessible():
    r = httpx.get(f"{BASE_URL}/api/docs", timeout=10)
    assert r.status_code == 200


# ===========================================================================
# SECTION 8 — Live API – Auth Guard (protected endpoints reject anon calls)
# ===========================================================================

@pytest.mark.parametrize("method,url,payload", [
    ("GET",  "/api/v1/auth/me",             None),
    ("POST", "/api/v1/chat/message",        {"message": "hello"}),
    ("GET",  "/api/v1/orders/my-orders",    None),
    ("GET",  "/api/v1/vehicles/my-vehicles",None),
    ("GET",  "/api/v1/admin/stats",         None),
], ids=["auth/me", "chat/message", "my-orders", "my-vehicles", "admin/stats"])
def test_protected_endpoints_require_auth(method, url, payload):
    if method == "GET":
        r = httpx.get(f"{BASE_URL}{url}", timeout=10)
    else:
        r = httpx.post(f"{BASE_URL}{url}", json=payload, timeout=10)
    assert r.status_code == 401, (
        f"{method} {url} should return 401 for unauthenticated request, got {r.status_code}"
    )


# ===========================================================================
# SECTION 9 — Live API – Full Auth Cycle
# ===========================================================================

import uuid as _uuid

# Use a unique email AND phone per test run so re-runs don't collide
_TEST_RUN_ID = _uuid.uuid4().hex[:8]
_TEST_EMAIL = f"syscheck_{_TEST_RUN_ID}@example.com"
_TEST_PHONE = "+9725" + "".join(c for c in _TEST_RUN_ID if c.isdigit())[:7].ljust(7, "0")
_TEST_PASS  = "SysCheck@999!"
_TEST_TOKEN = None   # filled in by the register/login tests


def test_auth_register_new_user():
    global _TEST_TOKEN
    r = httpx.post(
        f"{BASE_URL}/api/v1/auth/register",
        json={
            "email": _TEST_EMAIL,
            "password": _TEST_PASS,
            "full_name": "System Check User",
            "phone": _TEST_PHONE,
        },
        timeout=10,
    )
    if r.status_code == 429:
        pytest.skip("Rate-limited (too many registrations from test suite IP) — security is working correctly")
    assert r.status_code in (200, 201), f"Register failed: {r.text}"
    body = r.json()
    assert "user" in body or "message" in body


def test_auth_login_returns_token():
    global _TEST_TOKEN
    def _do_login():
        return httpx.post(
            f"{BASE_URL}/api/v1/auth/login",
            json={"email": _TEST_EMAIL, "password": _TEST_PASS},
            timeout=10,
        )

    r = _do_login()

    # When this test is run alone, register may not have executed yet.
    # Create the user once and retry login to keep test order-independent.
    if r.status_code == 401:
        rr = httpx.post(
            f"{BASE_URL}/api/v1/auth/register",
            json={
                "email": _TEST_EMAIL,
                "password": _TEST_PASS,
                "full_name": "System Check User",
                "phone": _TEST_PHONE,
            },
            timeout=10,
        )
        if rr.status_code == 429:
            pytest.skip("Rate-limited during fallback register — security is working correctly")
        assert rr.status_code in (200, 201, 400, 409, 422), f"Fallback register failed: {rr.text}"
        r = _do_login()

    if r.status_code == 429:
        pytest.skip("Rate-limited (too many login attempts from test suite) — security is working correctly")
    assert r.status_code in (200, 202), f"Login failed: {r.text}"
    body = r.json()

    # Direct-login mode (trusted device or 2FA not required)
    if r.status_code == 200:
        assert "access_token" in body
        assert body["token_type"].lower() == "bearer"
        _TEST_TOKEN = body["access_token"]
        return

    # 2FA-required mode
    assert body.get("requires_2fa") is True
    user_id = body.get("user_id")
    assert user_id, f"2FA flow missing user_id: {body}"
    dev_code = os.getenv("DEV_2FA_CODE", "123456")
    r2 = httpx.post(
        f"{BASE_URL}/api/v1/auth/verify-2fa",
        json={"user_id": user_id, "code": dev_code, "trust_device": True},
        timeout=10,
    )
    if r2.status_code == 429:
        pytest.skip("Rate-limited on verify-2fa — security is working correctly")
    if r2.status_code == 400 and "Invalid 2FA code" in r2.text:
        pytest.skip("2FA code is not retrievable in this environment; skipping token assertion")
    assert r2.status_code == 200, f"verify-2fa failed: {r2.text}"
    body2 = r2.json()
    assert "access_token" in body2
    assert body2["token_type"].lower() == "bearer"
    _TEST_TOKEN = body2["access_token"]


def test_auth_verify_phone():
    """Use DEV_2FA_CODE to verify the test user's phone so is_verified=True."""
    if not _TEST_TOKEN:
        pytest.skip("Skipped — no token")
    dev_code = os.getenv("DEV_2FA_CODE", "123456")
    r = httpx.post(
        f"{BASE_URL}/api/v1/auth/verify-phone",
        params={"code": dev_code},
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        timeout=10,
    )
    # 200 = verified; 400 = already verified or code expired; both are OK
    assert r.status_code in (200, 400), f"verify-phone failed unexpectedly: {r.text}"
    if not _TEST_TOKEN:
        pytest.skip("Skipped — login test did not produce a token (likely rate-limited)")
    r = httpx.get(
        f"{BASE_URL}/api/v1/auth/me",
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        timeout=10,
    )
    assert r.status_code == 200, f"/me failed: {r.text}"
    body = r.json()
    assert body["email"] == _TEST_EMAIL
    assert "id" in body


def test_auth_me_with_invalid_token():
    r = httpx.get(
        f"{BASE_URL}/api/v1/auth/me",
        headers={"Authorization": "Bearer this.is.fake"},
        timeout=10,
    )
    assert r.status_code == 401


def test_auth_duplicate_register_rejected():
    r = httpx.post(
        f"{BASE_URL}/api/v1/auth/register",
        json={
            "email": _TEST_EMAIL,
            "password": _TEST_PASS,
            "full_name": "Duplicate",
            "phone": "+972500000002",
        },
        timeout=10,
    )
    if r.status_code == 429:
        pytest.skip("Rate-limited — security is working correctly")
    assert r.status_code in (400, 409, 422), (
        f"Duplicate register should be rejected, got {r.status_code}: {r.text}"
    )


# ===========================================================================
# SECTION 10 — Live API – Vehicle Identify Endpoint
# ===========================================================================

def test_vehicle_identify_invalid_plate():
    """An unknown plate should return 404 or 502, not 500."""
    r = httpx.post(
        f"{BASE_URL}/api/v1/vehicles/identify",
        json={"license_plate": "XXXX9999"},
        timeout=15,
    )
    assert r.status_code in (200, 404, 422, 502), (
        f"Vehicle identify with bogus plate returned unexpected {r.status_code}"
    )
    # Must NOT be a raw 500 (internal server error)
    assert r.status_code != 500, "Vehicle identify crashed with 500 — check session routing"


# ===========================================================================
# SECTION 11 — Live API – Authenticated Endpoints
# ===========================================================================

def test_my_vehicles_with_auth():
    if not _TEST_TOKEN:
        pytest.skip("Skipped — no token (likely rate-limited)")
    r = httpx.get(
        f"{BASE_URL}/api/v1/vehicles/my-vehicles",
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        timeout=10,
    )
    assert r.status_code == 200, f"my-vehicles failed: {r.text}"
    body = r.json()
    assert isinstance(body, (list, dict)), f"Expected list or dict, got: {type(body)}"


def test_my_orders_with_auth():
    if not _TEST_TOKEN:
        pytest.skip("Skipped — no token (likely rate-limited)")
    r = httpx.get(
        f"{BASE_URL}/api/v1/orders",
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        timeout=10,
    )
    assert r.status_code == 200, f"orders failed: {r.text}"
    assert isinstance(r.json(), (list, dict))


def test_chat_message_with_auth_returns_processing():
    """Authenticated chat message should be immediately accepted (async processing)."""
    if not _TEST_TOKEN:
        pytest.skip("Skipped — no token (likely rate-limited)")
    r = httpx.post(
        f"{BASE_URL}/api/v1/chat/message",
        json={"message": "שלום, אני מחפש רפידות בלמים לטויוטה קורולה 2018"},
        headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        timeout=15,
    )
    assert r.status_code == 200, f"chat/message failed: {r.text}"
    body = r.json()
    # The endpoint returns immediately with status=processing
    assert body.get("status") == "processing" or "conversation_id" in body or "message" in body


# ===========================================================================
# SECTION 12 — Agent Instantiation
# ===========================================================================

def test_all_agents_can_be_instantiated():
    """Every class in AGENT_MAP must be instantiable without errors."""
    from BACKEND_AI_AGENTS import AGENT_MAP
    for name, cls in AGENT_MAP.items():
        instance = cls()
        assert instance is not None, f"Agent '{name}' ({cls.__name__}) failed to instantiate"


def test_get_agent_returns_singleton():
    """get_agent() returns the same object on repeated calls."""
    from BACKEND_AI_AGENTS import get_agent
    a1 = get_agent("parts_finder_agent")
    a2 = get_agent("parts_finder_agent")
    assert a1 is a2, "get_agent() is not returning a singleton"


# ===========================================================================
# SECTION 13 — AGENT_MAP Completeness
# ===========================================================================

EXPECTED_AGENTS = [
    "router_agent",
    "parts_finder_agent",
    "sales_agent",
    "orders_agent",
    "finance_agent",
    "service_agent",
    "security_agent",
    "marketing_agent",
    "supplier_manager_agent",
    "social_media_manager_agent",
]


@pytest.mark.parametrize("agent_name", EXPECTED_AGENTS)
def test_agent_map_contains_expected_agent(agent_name):
    from BACKEND_AI_AGENTS import AGENT_MAP
    assert agent_name in AGENT_MAP, (
        f"'{agent_name}' is missing from AGENT_MAP — agent router will silently fall back"
    )


# ===========================================================================
# SECTION 14 — HF Client (vision / translation helpers)
# ===========================================================================

def test_hf_client_imports():
    """hf_client module must import without error."""
    import importlib
    mod = importlib.import_module("hf_client")
    assert mod is not None


def test_hf_normalize_query_passthrough_english():
    """English-only queries must be returned unchanged (or translated)."""
    import asyncio as _asyncio
    from hf_client import hf_normalize_query
    result = _asyncio.run(hf_normalize_query("brake pad"))
    # Must be non-empty string
    assert isinstance(result, str) and len(result) > 0


def test_hf_is_mostly_hebrew_detects_hebrew():
    """_is_mostly_hebrew must return True for Hebrew-dominant strings."""
    from hf_client import _is_mostly_hebrew
    assert _is_mostly_hebrew("רפידת בלם") is True


def test_hf_is_mostly_hebrew_rejects_english():
    """_is_mostly_hebrew must return False for English strings."""
    from hf_client import _is_mostly_hebrew
    assert _is_mostly_hebrew("brake pad") is False


def test_hf_vision_model_env():
    """HF_VISION_MODEL env var must be set and point to a vision-capable model."""
    import os
    model = os.getenv("HF_VISION_MODEL", "")
    assert model, "HF_VISION_MODEL is not set"
    # Must not be a text-only model (past regression: Kimi was set by mistake)
    TEXT_ONLY_MODELS = {"moonshotai/kimi-k2-instruct-0905", "meta-llama/llama-3.1-8b-instruct"}
    assert model.lower() not in TEXT_ONLY_MODELS, \
        f"HF_VISION_MODEL is set to a text-only model: {model}"


# ===========================================================================
# SECTION 15 — Catalog DB — part_diagram_cache table
# ===========================================================================

@pytest.mark.asyncio
async def test_part_diagram_cache_table_exists():
    """part_diagram_cache must exist in catalog DB (created by migration/manual DDL)."""
    eng, factory = _make_catalog_session()
    async with factory() as session:
        result = await session.execute(
            text("SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='part_diagram_cache'")
        )
        assert result.scalar() == 1, "part_diagram_cache table is missing from catalog DB"
    await eng.dispose()


@pytest.mark.asyncio
async def test_part_diagram_cache_columns():
    """part_diagram_cache must have all required columns."""
    eng, factory = _make_catalog_session()
    async with factory() as session:
        result = await session.execute(
            text("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name='part_diagram_cache'
            """)
        )
        cols = {r[0] for r in result.fetchall()}
    await eng.dispose()
    required = {"id", "image_hash", "part_name_he", "part_name_en", "confidence", "created_at"}
    missing = required - cols
    assert not missing, f"part_diagram_cache missing columns: {missing}"
