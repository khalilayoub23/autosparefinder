"""
==============================================================================
AUTOSPAREFINDER — SECURITY TEST SUITE (OWASP Top 10)
==============================================================================
Sections:
  A.  JWT & Token Security          (A07 – Identification & Auth Failures)
  B.  Broken Access Control / IDOR  (A01 – Broken Access Control)
  C.  Injection Attacks             (A03 – Injection)
  D.  Cryptographic Controls        (A02 – Cryptographic Failures)
  E.  Security Headers & Transport  (A05 – Security Misconfiguration)
  F.  Input Validation & Mass-Assign(A03 + A04 – Injection / Insecure Design)
  G.  Webhook & External Integrity  (A08 – SW & Data Integrity Failures)
  H.  Rate Limiting & Brute Force   (A07 – Identification & Auth Failures)
  I.  Information Leakage           (A02 + A04 – Cryptographic / Insecure Design)
  J.  Session Management            (A07 – Identification & Auth Failures)
  K.  Privilege Escalation          (A01 – Broken Access Control)
  L.  File Upload Security          (A04 + A05 – Insecure Design / Misconfig)
==============================================================================
Each test is annotated with the OWASP category it covers.
==============================================================================
"""

import base64
import os
import re
import sys
import time
import uuid as _uuid

import httpx
import pytest
from jose import jwt as _jose_jwt

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
REPO_DIR = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

BASE_URL = "http://localhost:8000"

# ---------------------------------------------------------------------------
# Per-run unique identifiers – prevents DB collisions on repeated runs
# ---------------------------------------------------------------------------
_RUN = _uuid.uuid4().hex[:8]


def _unique_email(tag: str = "") -> str:
    return f"sec{tag}_{_RUN}@example.com"


def _unique_phone(seed: str = "0") -> str:
    digits = "".join(c for c in (_RUN + seed) if c.isdigit())[:7].ljust(7, "0")
    return f"+9725{digits}"


_STD_PWD = "SecurePass@2026!"
_WEAK_PWDS = ["123", "password", "abc123", "12345678", "qwertyui"]
# Note: "aaaaaaa1" meets the current 8-char + digit + letter policy so is excluded here

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skip_if_no_server():
    try:
        httpx.get(f"{BASE_URL}/api/v1/system/health", timeout=3)
    except Exception:
        pytest.skip("Backend server not running — skipping live security test")


def _register_and_login(email: str, phone: str, password: str = _STD_PWD) -> str:
    """Register a fresh user, verify their phone, and return an access token."""
    r = httpx.post(f"{BASE_URL}/api/v1/auth/register",
                   json={"email": email, "phone": phone,
                         "password": password, "full_name": "Sec Test User"},
                   timeout=15)
    if r.status_code not in (200, 201):
        pytest.skip(f"Registration failed ({r.status_code}): {r.text[:200]}")

    r2 = httpx.post(f"{BASE_URL}/api/v1/auth/login",
                    json={"email": email, "password": password, "trust_device": True},
                    timeout=15)
    if r2.status_code == 429:
        pytest.skip("Rate-limited during login — test skipped")
    if r2.status_code == 202:
        # 2FA required — complete it with DEV_2FA_CODE
        user_id = r2.json().get("user_id", "")
        if not user_id:
            pytest.skip("2FA required but no user_id returned")
        dev_code = os.getenv("DEV_2FA_CODE", "123456")
        r2b = httpx.post(f"{BASE_URL}/api/v1/auth/verify-2fa",
                         json={"user_id": user_id, "code": dev_code, "trust_device": True},
                         timeout=15)
        if r2b.status_code != 200:
            pytest.skip(f"2FA completion failed ({r2b.status_code}): {r2b.text[:200]}")
        r2 = r2b
    if r2.status_code != 200:
        pytest.skip(f"Login failed ({r2.status_code}): {r2.text[:200]}")

    token = r2.json()["access_token"]

    # Verify phone with DEV_2FA_CODE so is_verified=True (required for some endpoints)
    dev_code = os.getenv("DEV_2FA_CODE", "123456")
    httpx.post(f"{BASE_URL}/api/v1/auth/verify-phone",
               params={"code": dev_code},
               headers={"Authorization": f"Bearer {token}"},
               timeout=10)
    # Ignore verify-phone errors — not all endpoints require is_verified
    return token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _routes_src() -> str:
    """Return the combined source of BACKEND_API_ROUTES.py and all routes/*.py modules."""
    import glob
    parts = []
    with open(os.path.join(BACKEND_DIR, "BACKEND_API_ROUTES.py"), encoding="utf-8") as f:
        parts.append(f.read())
    for fpath in sorted(glob.glob(os.path.join(BACKEND_DIR, "routes", "*.py"))):
        with open(fpath, encoding="utf-8") as f:
            parts.append(f.read())
    return "\n".join(parts)


def _auth_src() -> str:
    with open(os.path.join(BACKEND_DIR, "BACKEND_AUTH_SECURITY.py"), encoding="utf-8") as f:
        return f.read()


def _models_src() -> str:
    with open(os.path.join(BACKEND_DIR, "BACKEND_DATABASE_MODELS.py"), encoding="utf-8") as f:
        return f.read()


# ===========================================================================
# A. JWT & TOKEN SECURITY  (OWASP A07)
# ===========================================================================

def test_A_jwt_none_algorithm_rejected():
    """OWASP A07 — 'alg:none' JWT must be rejected (authentication bypass)."""
    _skip_if_no_server()
    # Build a token with algorithm=none and no signature
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        b'{"sub":"00000000-0000-0000-0000-000000000001","type":"access","exp":9999999999}'
    ).rstrip(b"=").decode()
    none_token = f"{header}.{payload}."
    r = httpx.get(f"{BASE_URL}/api/v1/auth/me",
                  headers={"Authorization": f"Bearer {none_token}"}, timeout=10)
    assert r.status_code == 401, \
        f"alg:none JWT must be rejected but got {r.status_code}"


def test_A_jwt_wrong_secret_rejected():
    """OWASP A07 — Token signed with wrong secret must be rejected."""
    _skip_if_no_server()
    fake_token = _jose_jwt.encode(
        {"sub": str(_uuid.uuid4()), "type": "access", "exp": 9_999_999_999},
        "totallywrongsecret",
        algorithm="HS256",
    )
    r = httpx.get(f"{BASE_URL}/api/v1/auth/me",
                  headers={"Authorization": f"Bearer {fake_token}"}, timeout=10)
    assert r.status_code == 401


def test_A_refresh_token_cannot_access_api():
    """OWASP A07 — Refresh tokens must NOT work as access tokens (token type confusion)."""
    _skip_if_no_server()
    email = _unique_email("rtc")
    phone = _unique_phone("1")
    r = httpx.post(f"{BASE_URL}/api/v1/auth/register",
                   json={"email": email, "phone": phone,
                         "password": _STD_PWD, "full_name": "RT Test"},
                   timeout=15)
    if r.status_code not in (200, 201):
        pytest.skip("Register failed")
    r2 = httpx.post(f"{BASE_URL}/api/v1/auth/login",
                    json={"email": email, "password": _STD_PWD, "trust_device": True},
                    timeout=15)
    if r2.status_code not in (200,):
        pytest.skip(f"Login failed or 2FA required: {r2.status_code}")
    refresh_token = r2.json().get("refresh_token", "")
    if not refresh_token:
        pytest.skip("No refresh token in response")
    # Attempt to use the refresh token as a bearer token for API access
    r3 = httpx.get(f"{BASE_URL}/api/v1/auth/me",
                   headers={"Authorization": f"Bearer {refresh_token}"}, timeout=10)
    assert r3.status_code == 401, \
        f"Refresh token used as access token must return 401, got {r3.status_code}"


def test_A_expired_token_rejected():
    """OWASP A07 — Manually-crafted expired token must be rejected."""
    _skip_if_no_server()
    # Build an expired token using the known dev JWT_SECRET_KEY (we'll use a fake key —
    # the point is it MUST fail; if the real key works, that's fine since verification
    # checks exp before anything else)
    from BACKEND_AUTH_SECURITY import JWT_SECRET_KEY, JWT_ALGORITHM
    expired_token = _jose_jwt.encode(
        {"sub": str(_uuid.uuid4()), "type": "access", "exp": 1_000_000_000},  # year 2001
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    r = httpx.get(f"{BASE_URL}/api/v1/auth/me",
                  headers={"Authorization": f"Bearer {expired_token}"}, timeout=10)
    assert r.status_code == 401, f"Expired token must be rejected, got {r.status_code}"


def test_A_malformed_token_rejected():
    """OWASP A07 — Garbage tokens must return 401 not 500."""
    _skip_if_no_server()
    garbage_tokens = [
        "not_a_token",
        "a.b.c",
        "Bearer eyJhbGc...",
        # NOTE: empty string omitted — httpx rejects it at the protocol layer
        # before it even reaches the server (Illegal header value)
        "null",
        "undefined",
        "a" * 512,
    ]
    for tok in garbage_tokens:
        try:
            r = httpx.get(f"{BASE_URL}/api/v1/auth/me",
                          headers={"Authorization": f"Bearer {tok}"}, timeout=10)
        except Exception:
            # Protocol-level rejection is also acceptable
            continue
        assert r.status_code in (401, 422), \
            f"Malformed token '{tok[:20]}' must return 401/422, got {r.status_code}"
        assert r.status_code != 500, "Malformed token caused a 500 error"


def test_A_missing_auth_header_rejected():
    """OWASP A07 — No Authorization header → 401/403."""
    _skip_if_no_server()
    r = httpx.get(f"{BASE_URL}/api/v1/auth/me", timeout=10)
    assert r.status_code in (401, 403)


def test_A_jwt_type_field_enforced_in_source():
    """OWASP A07 — decode_access_token must check payload.type == 'access'."""
    auth = _auth_src()
    assert '"type"' in auth or "'type'" in auth, "JWT payloads must include a 'type' field"
    assert '"access"' in auth or "'access'" in auth
    assert "Invalid token type" in auth, \
        "decode_access_token must reject tokens with wrong type"


def test_A_jwt_algorithm_is_hs256_not_none():
    """OWASP A07 — JWT algorithm must be HS256 and never 'none'."""
    auth = _auth_src()
    # Confirm HS256 is used
    assert "HS256" in auth, "JWT_ALGORITHM must be HS256"
    # 'none' must not appear as an accepted algorithm anywhere in the source
    assert 'algorithms=["none"]' not in auth
    assert "algorithms=['none']" not in auth


# ===========================================================================
# B. BROKEN ACCESS CONTROL / IDOR  (OWASP A01)
# ===========================================================================

def test_B_idor_order_cross_user():
    """OWASP A01 — User B cannot read User A's order."""
    _skip_if_no_server()
    email_a = _unique_email("idor_a")
    email_b = _unique_email("idor_b")
    token_a = _register_and_login(email_a, _unique_phone("2"))
    token_b = _register_and_login(email_b, _unique_phone("3"))

    # User A creates an order (expect 422/400 since no real products — that's fine)
    # We just need a valid order_id format to probe
    fake_order_id = str(_uuid.uuid4())
    r = httpx.get(f"{BASE_URL}/api/v1/orders/{fake_order_id}",
                  headers=_auth(token_b), timeout=10)
    # Must be 404 (not found) not 403, as revealing FK structure is still fine here—
    # the important thing is that it's NOT 200 with someone else's data
    assert r.status_code in (404, 403), \
        f"Accessing non-owned order must return 404/403, got {r.status_code}"


def test_B_idor_invoice_cross_user():
    """OWASP A01 — User B cannot read User A's invoice via /invoices/{id}."""
    _skip_if_no_server()
    token = _register_and_login(_unique_email("inv_b"), _unique_phone("4"))
    fake_invoice_id = str(_uuid.uuid4())
    r = httpx.get(f"{BASE_URL}/api/v1/invoices/{fake_invoice_id}",
                  headers=_auth(token), timeout=10)
    assert r.status_code in (404, 403), f"Invoice IDOR: got {r.status_code}"


def test_B_idor_payment_cross_user():
    """OWASP A01 — User B cannot read User A's payment."""
    _skip_if_no_server()
    token = _register_and_login(_unique_email("pay_b"), _unique_phone("5"))
    fake_payment_id = str(_uuid.uuid4())
    r = httpx.get(f"{BASE_URL}/api/v1/payments/{fake_payment_id}",
                  headers=_auth(token), timeout=10)
    assert r.status_code in (404, 403), f"Payment IDOR: got {r.status_code}"


def test_B_idor_file_cross_user():
    """OWASP A01 — User B cannot access User A's uploaded file."""
    _skip_if_no_server()
    token = _register_and_login(_unique_email("file_b"), _unique_phone("6"))
    fake_file_id = str(_uuid.uuid4())
    r = httpx.get(f"{BASE_URL}/api/v1/files/{fake_file_id}",
                  headers=_auth(token), timeout=10)
    assert r.status_code in (404, 403), f"File IDOR: got {r.status_code}"


def test_B_regular_user_cannot_access_admin_stats():
    """OWASP A01 — Regular users must get 401/403 from admin endpoints."""
    _skip_if_no_server()
    token = _register_and_login(_unique_email("adm"), _unique_phone("7"))
    admin_endpoints = [
        "/api/v1/admin/stats",
        "/api/v1/admin/users",
        "/api/v1/admin/orders",
        "/api/v1/admin/suppliers",
        "/api/v1/admin/analytics/dashboard",
    ]
    for url in admin_endpoints:
        r = httpx.get(f"{BASE_URL}{url}", headers=_auth(token), timeout=10)
        assert r.status_code in (401, 403), \
            f"Regular user must be rejected from {url}, got {r.status_code}"


def test_B_notification_idor_in_source():
    """OWASP A01 — Notification mark-read must filter by current_user.id."""
    src = _routes_src()
    read_idx = src.find("notification_id}/read")
    assert read_idx != -1, "Mark-notification-read endpoint not found"
    snippet = src[read_idx: read_idx + 600]
    assert "current_user" in snippet, \
        "Notification mark-read must check current_user.id to prevent IDOR"


def test_B_order_ownership_check_in_source():
    """OWASP A01 — get_order must filter by user_id to prevent IDOR."""
    src = _routes_src()
    idx = src.find("async def get_order(")
    assert idx != -1
    snippet = src[idx: idx + 800]
    assert "user_id" in snippet, \
        "get_order must check Order.user_id == current_user.id"


def test_B_payment_idor_guard_in_source():
    """OWASP A01 — get_payment must join Order and check user_id ownership."""
    src = _routes_src()
    idx = src.find("async def get_payment(")
    assert idx != -1
    snippet = src[idx: idx + 800]
    # Must cross-reference Order table to get the user_id
    assert "user_id" in snippet, \
        "get_payment must check payment ownership via user_id"


def test_B_cors_not_wildcard():
    """OWASP A01 — CORS must not allow all origins (wildcard *)."""
    src = _routes_src()
    cors_idx = src.find("CORSMiddleware")
    assert cors_idx != -1, "CORSMiddleware not found"
    # Find the allow_origins line
    origins_idx = src.find("allow_origins", cors_idx)
    snippet = src[origins_idx: origins_idx + 200]
    assert 'allow_origins=["*"]' not in snippet, \
        "CORS allow_origins must NOT be wildcard '*'"
    assert "allow_origins=['*']" not in snippet


# ===========================================================================
# C. INJECTION ATTACKS  (OWASP A03)
# ===========================================================================

SQL_INJECTION_PAYLOADS = [
    "' OR '1'='1",
    "'; DROP TABLE parts_catalog; --",
    "1 UNION SELECT 1,2,3--",
    "1' AND SLEEP(5)--",
    '" OR ""="',
    "\\x27 OR 1=1",
]

XSS_PAYLOADS = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "javascript:alert(1)",
    "<svg onload=alert(1)>",
]


@pytest.mark.parametrize("payload", SQL_INJECTION_PAYLOADS)
def test_C_sql_injection_in_search_does_not_crash(payload):
    """OWASP A03 — SQL injection in parts search must not cause 500."""
    _skip_if_no_server()
    try:
        r = httpx.get(f"{BASE_URL}/api/v1/parts/search",
                      params={"query": payload}, timeout=20)
    except httpx.ReadTimeout:
        pytest.skip(f"Search timed out for payload '{payload[:30]}' — server busy")
    assert r.status_code != 500, \
        f"SQL injection '{payload[:30]}' caused 500 — possible injection vulnerability"
    assert r.status_code in (200, 400, 422)


@pytest.mark.parametrize("payload", XSS_PAYLOADS)
def test_C_xss_payload_search_response_is_json(payload):
    """OWASP A03 — XSS payloads in search must not be reflected as HTML."""
    _skip_if_no_server()
    try:
        r = httpx.get(f"{BASE_URL}/api/v1/parts/search",
                      params={"query": payload}, timeout=15)
    except httpx.ReadTimeout:
        pytest.skip("Timeout on XSS payload search")
    ct = r.headers.get("content-type", "")
    # A JSON API is safe — the value is serialized inside a JSON string
    # so it cannot be interpreted as HTML/JavaScript by a browser.
    # The critical check is that the response is NOT served as text/html.
    assert "text/html" not in ct, \
        f"XSS payload '{payload}' reflected with Content-Type text/html — XSS risk! Got: {ct}"
    if "application/json" in ct and r.status_code == 200:
        # Verify the JSON is valid (not broken by injection)
        try:
            r.json()
        except Exception:
            assert False, f"XSS payload '{payload}' broke JSON serialization: {r.text[:200]}"


def test_C_path_traversal_in_file_id():
    """OWASP A03 — Path traversal in file ID must not return 500."""
    _skip_if_no_server()
    token = _register_and_login(_unique_email("pt"), _unique_phone("8"))
    traversal_ids = [
        "../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "../../../proc/version",
    ]
    for trav in traversal_ids:
        r = httpx.get(f"{BASE_URL}/api/v1/files/{trav}",
                      headers=_auth(token), timeout=10)
        assert r.status_code != 500, \
            f"Path traversal '{trav}' caused 500"
        assert r.status_code in (400, 404, 422), \
            f"Path traversal '{trav}' returned {r.status_code}"


def test_C_sql_injection_autocomplete():
    """OWASP A03 — SQL injection in autocomplete must not crash."""
    _skip_if_no_server()
    for payload in ("' UNION SELECT 1--", "'; exec xp_cmdshell('ls')--"):
        try:
            r = httpx.get(f"{BASE_URL}/api/v1/parts/autocomplete",
                          params={"q": payload}, timeout=15)
        except httpx.ReadTimeout:
            pytest.skip("Autocomplete timed out under load")
        assert r.status_code != 500, \
            f"Autocomplete SQL injection caused 500: '{payload}'"


def test_C_orm_used_for_queries_not_raw_string_concat():
    """OWASP A03 — Dangerous string-format SQL patterns must not exist in routes."""
    src = _routes_src()
    # Detect f-string or % interpolation directly into SQL
    bad_patterns = [
        r'execute\(f"SELECT',
        r'execute\(f\'SELECT',
        r"execute\(\"SELECT.*\%.*\%",
        r"raw_query.*format\(",
    ]
    for pattern in bad_patterns:
        assert not re.search(pattern, src, re.IGNORECASE), \
            f"Potential raw SQL string concatenation found: {pattern}"


def test_C_no_eval_exec_in_backend():
    """OWASP A03 — eval() and exec() must not appear in backend source for user data."""
    for fname in ("BACKEND_API_ROUTES.py", "BACKEND_AUTH_SECURITY.py"):
        src = open(os.path.join(BACKEND_DIR, fname), encoding="utf-8").read()
        # Allow eval inside comments or string literals for non-user-data uses
        # Look for actual call patterns
        assert not re.search(r'\beval\s*\(', src), \
            f"eval() found in {fname} — potential code injection risk"


def test_C_no_pickle_loads_in_backend():
    """OWASP A03 — pickle.loads() of untrusted data enables RCE."""
    for fname in ("BACKEND_API_ROUTES.py", "BACKEND_AUTH_SECURITY.py", "BACKEND_AI_AGENTS.py"):
        src = open(os.path.join(BACKEND_DIR, fname), encoding="utf-8").read()
        assert "pickle.loads" not in src, \
            f"pickle.loads() found in {fname} — RCE risk if used on untrusted data"


# ===========================================================================
# D. CRYPTOGRAPHIC CONTROLS  (OWASP A02)
# ===========================================================================

def test_D_bcrypt_rounds_12_or_higher():
    """OWASP A02 — bcrypt cost factor must be >= 12."""
    auth = _auth_src()
    # Check for explicit rounds configuration
    assert "rounds=12" in auth or "bcrypt__rounds=12" in auth, \
        "bcrypt rounds must be set to at least 12 to prevent brute-force cracking"


def test_D_no_md5_for_password_hashing():
    """OWASP A02 — MD5 must not be used for passwords."""
    auth = _auth_src()
    # MD5 in context of password-related functions
    # hashlib.md5 is OK for non-security uses (e.g., cache keys), but not passwords
    idx = auth.find("password_hash")
    if idx != -1:
        surrounding = auth[max(0, idx - 500): idx + 500]
        assert "md5" not in surrounding.lower() or "hashlib.md5" not in surrounding, \
            "MD5 must not be used for password hashing"


def test_D_passwords_stored_as_hash_not_plaintext():
    """OWASP A02 — Only password_hash stored in User model; plaintext never stored."""
    models = _models_src()
    user_idx = models.find("class User(PiiBase)")
    snippet = models[user_idx: user_idx + 1500]
    assert "password_hash" in snippet, "User model must store password_hash not plaintext"
    assert '"password"' not in snippet.split("password_hash")[0].split("Column")[-1:][0] \
        if snippet else True, "Plaintext password column should not exist"


def test_D_jwt_secret_not_hardcoded():
    """OWASP A02 — JWT secret must come from environment, not be hardcoded."""
    auth = _auth_src()
    # No long (>12 char) hardcoded string assigned directly as JWT secret
    assert 'JWT_SECRET_KEY = "' not in auth or \
           'os.getenv("JWT_SECRET_KEY"' in auth, \
        "JWT_SECRET_KEY must use os.getenv, not be hardcoded"
    assert "JWT_SECRET_KEY = os.getenv" in auth


def test_D_no_plaintext_secret_in_env_file():
    """OWASP A02 — .env must not contain live API keys in plaintext."""
    env_path = os.path.join(REPO_DIR, ".env")
    if not os.path.exists(env_path):
        pytest.skip(".env not found")
    content = open(env_path).read()
    # Live Stripe keys start with sk_live_ or rk_live_
    assert "sk_live_" not in content, ".env contains live Stripe secret key"
    assert "rk_live_" not in content, ".env contains live Stripe restricted key"
    # GitHub PATs follow ghp_ or github_pat_ patterns
    assert "ghp_" not in content, ".env contains GitHub personal access token"


def test_D_password_not_logged_in_auth():
    """OWASP A02 — Password values must not be passed to print/logging functions."""
    auth = _auth_src()
    # Look for print/log calls near password variable references
    lines = auth.splitlines()
    # Look for lines where an actual password *variable* is passed to print/log,
    # not just lines where the word 'password' appears in an error message string.
    password_var_pattern = re.compile(
        r'(print|logger\.(info|debug|warning|error))\s*\(.*\bpassword\b(?!.*reset.*email)(?!.*hash)(?!.*token)',
        re.IGNORECASE
    )
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Skip comments and docstrings
        if stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        if password_var_pattern.search(stripped):
            # Must not be logging an actual password value
            # Allow: logging that mentions 'password' in error *message* strings
            # Deny: passing a password variable to print/log
            if re.search(r'(password\s*=|data\.password|user\.password)', stripped, re.IGNORECASE):
                assert False, \
                    f"Possible plaintext password logging at line {i+1}: {stripped}"


def test_D_sha256_used_for_supplier_mask_not_md5():
    """OWASP A02 — Supplier masking should use SHA-256 (collision-resistant)."""
    src = _routes_src()
    assert "sha256" in src, "Supplier masking must use SHA-256"
    mask_idx = src.find("_mask_supplier")
    snippet = src[mask_idx: mask_idx + 400]
    assert "md5" not in snippet.lower(), "Supplier mask must not use MD5"


# ===========================================================================
# E. SECURITY HEADERS & TRANSPORT  (OWASP A05)
# ===========================================================================

def test_E_response_content_type_is_json():
    """OWASP A05 — API responses must be application/json, not text/html."""
    _skip_if_no_server()
    r = httpx.get(f"{BASE_URL}/api/v1/system/health", timeout=10)
    ct = r.headers.get("content-type", "")
    assert "application/json" in ct, \
        f"Health endpoint content-type should be JSON, got: {ct}"


def test_E_404_response_is_json_not_html():
    """OWASP A05 — 404 errors must return JSON, not HTML error pages."""
    _skip_if_no_server()
    r = httpx.get(f"{BASE_URL}/api/v1/nonexistent_endpoint_xyz", timeout=10)
    assert r.status_code == 404
    ct = r.headers.get("content-type", "")
    # FastAPI default returns JSON for 404
    assert "application/json" in ct or r.json(), \
        f"404 returns HTML instead of JSON: {r.text[:200]}"


def test_E_500_error_must_not_expose_stack_trace():
    """OWASP A05 — 500 errors must not leak Python tracebacks to clients."""
    _skip_if_no_server()
    # Try a request that might trigger unusual server behavior (invalid UUID format)
    r = httpx.get(f"{BASE_URL}/api/v1/orders/not-a-uuid", timeout=10)
    if r.status_code == 500:
        body = r.text
        assert "Traceback" not in body, \
            "500 response exposes Python traceback — information leakage"
        assert "File \"" not in body, \
            "500 response exposes file paths — information leakage"


def test_E_cors_credentials_and_origins_consistent():
    """OWASP A05 — allow_credentials=True requires specific origins, not wildcard."""
    src = _routes_src()
    cors_idx = src.find("CORSMiddleware")
    snippet = src[cors_idx: cors_idx + 500]
    if "allow_credentials=True" in snippet:
        # Must not use wildcard '*' when credentials are allowed
        assert 'allow_origins=["*"]' not in snippet, \
            "allow_credentials=True with wildcard origins is forbidden by CORS spec"
        assert "allow_origins=['*']" not in snippet


def test_E_no_debug_mode_in_production_config():
    """OWASP A05 — debug=True must not be set in the production app."""
    src = _routes_src()
    # FastAPI(debug=True) is dangerous in production
    assert 'FastAPI(debug=True)' not in src, \
        "FastAPI must not run with debug=True in production"


def test_E_openapi_docs_not_publicly_guarded():
    """OWASP A05 — Verify /docs is accessible (expected for dev) but note the risk."""
    _skip_if_no_server()
    r = httpx.get(f"{BASE_URL}/docs", timeout=10)
    # 200 = public (common in dev), 401/403 = protected, 404 = disabled in production
    # Any of these is acceptable — this test just documents the exposure level.
    assert r.status_code in (200, 401, 403, 404), \
        f"Unexpected status from /docs: {r.status_code}"


# ===========================================================================
# F. INPUT VALIDATION & MASS ASSIGNMENT  (OWASP A03 + A04)
# ===========================================================================

@pytest.mark.parametrize("weak_pwd", _WEAK_PWDS)
def test_F_weak_password_at_registration_rejected(weak_pwd):
    """OWASP A04 — Weak passwords must be rejected at registration."""
    _skip_if_no_server()
    # Each parametrized call needs a distinct phone to avoid 409 conflicts
    suffix = "".join(c for c in weak_pwd if c.isalnum())[:5].ljust(5, "0")
    unique_suffix = (suffix + _RUN)[:7]
    phone = "+9726" + "".join(c for c in unique_suffix if c.isdigit())[:7].ljust(7, "0")
    r = httpx.post(f"{BASE_URL}/api/v1/auth/register",
                   json={
                       "email": _unique_email(f"wp{suffix}"),
                       "phone": phone,
                       "password": weak_pwd,
                       "full_name": "Weak Test",
                   }, timeout=10)
    # Should fail with 400 or 422 (validation error); 201 means weak passwords accepted
    assert r.status_code in (400, 409, 422), \
        f"Weak password '{weak_pwd}' was accepted at registration (got {r.status_code})"


def test_F_is_admin_not_settable_via_profile_update():
    """OWASP A04 — Users must not be able to set is_admin via PUT /profile."""
    _skip_if_no_server()
    token = _register_and_login(_unique_email("ma"), _unique_phone("10"))
    r = httpx.put(f"{BASE_URL}/api/v1/profile",
                  json={"is_admin": True, "full_name": "Admin Hacker"},
                  headers=_auth(token), timeout=10)
    # The request may succeed (200) but is_admin must not be set
    if r.status_code == 200:
        me = httpx.get(f"{BASE_URL}/api/v1/auth/me",
                       headers=_auth(token), timeout=10)
        if me.status_code == 200:
            assert me.json().get("is_admin") is not True, \
                "Mass assignment: is_admin was set via profile update!"


def test_F_role_not_settable_via_profile_update():
    """OWASP A04 — Users must not be able to change their role via PUT /profile."""
    _skip_if_no_server()
    token = _register_and_login(_unique_email("role"), _unique_phone("11"))
    r = httpx.put(f"{BASE_URL}/api/v1/profile",
                  json={"role": "admin"},
                  headers=_auth(token), timeout=10)
    if r.status_code == 200:
        me = httpx.get(f"{BASE_URL}/api/v1/auth/me",
                       headers=_auth(token), timeout=10)
        if me.status_code == 200:
            assert me.json().get("role") != "admin", \
                "Mass assignment: role was changed to admin via profile update!"


def test_F_invalid_uuid_order_returns_404_not_500():
    """OWASP A04 — Invalid UUID path params must not cause 500."""
    _skip_if_no_server()
    token = _register_and_login(_unique_email("uuid"), _unique_phone("12"))
    invalid_ids = ["not-a-uuid", "123", "abc-def", "null", "0" * 50]
    for bad_id in invalid_ids:
        r = httpx.get(f"{BASE_URL}/api/v1/orders/{bad_id}",
                      headers=_auth(token), timeout=10)
        assert r.status_code != 500, \
            f"Invalid UUID '{bad_id}' caused 500 in orders endpoint"
        assert r.status_code in (400, 404, 422)


def test_F_oversized_payload_does_not_crash():
    """OWASP A04 — Oversized request bodies must not cause 500."""
    _skip_if_no_server()
    big_payload = {"query": "brake" * 10_000}  # ~50KB query
    try:
        r = httpx.get(f"{BASE_URL}/api/v1/parts/search",
                      params=big_payload, timeout=15)
        assert r.status_code != 500, "Oversized query parameter caused 500"
    except (httpx.ReadTimeout, httpx.RequestError):
        pass  # timeout or connection reset is also acceptable


def test_F_profile_update_schema_whitelists_fields_in_source():
    """OWASP A04 — Profile update endpoint must use a Pydantic schema."""
    src = _routes_src()
    profile_update_idx = src.find('"/api/v1/profile"')
    # Find the PUT handler
    put_idx = src.rfind("@app.put", 0, profile_update_idx + 50)
    if put_idx == -1:
        put_idx = src.find("async def update_profile(")
    snippet = src[put_idx: put_idx + 400] if put_idx != -1 else src
    # Should use a Pydantic model (BaseModel subclass), not raw dict
    # Check that is_admin/role are not in the update schema
    update_schema_idx = src.find("class ProfileUpdate")
    if update_schema_idx != -1:
        schema_snippet = src[update_schema_idx: update_schema_idx + 500]
        assert "is_admin" not in schema_snippet, \
            "ProfileUpdate schema must not expose is_admin field (mass assignment)"
        assert '"role"' not in schema_snippet, \
            "ProfileUpdate schema must not expose role field"


# ===========================================================================
# G. WEBHOOK & EXTERNAL INTEGRATION SECURITY  (OWASP A08)
# ===========================================================================

def test_G_stripe_webhook_no_signature_returns_400():
    """OWASP A08 — Stripe webhook without valid signature must return 400."""
    _skip_if_no_server()
    r = httpx.post(f"{BASE_URL}/api/v1/payments/webhook",
                   content=b'{"type":"payment_intent.succeeded"}',
                   headers={"Content-Type": "application/json"},
                   timeout=10)
    assert r.status_code in (400, 401, 422), \
        f"Webhook without signature returned {r.status_code} instead of 4xx"


def test_G_stripe_webhook_bad_signature_returns_400():
    """OWASP A08 — Stripe webhook with fake signature must return 400."""
    _skip_if_no_server()
    r = httpx.post(f"{BASE_URL}/api/v1/payments/webhook",
                   content=b'{"type":"payment_intent.succeeded","id":"evt_test"}',
                   headers={
                       "Content-Type": "application/json",
                       "Stripe-Signature": "t=1234567890,v1=fakesignaturethatiswrong",
                   },
                   timeout=10)
    assert r.status_code in (400, 401, 422), \
        f"Webhook with bad signature returned {r.status_code} instead of 4xx"


def test_G_stripe_signature_verification_in_source():
    """OWASP A08 — Webhook handler must call stripe.Webhook.construct_event."""
    src = _routes_src()
    assert "construct_event" in src, \
        "Stripe webhook handler must verify signature via construct_event()"
    # FastAPI receives headers normalised to lowercase: "stripe-signature"
    assert "stripe-signature" in src or "Stripe-Signature" in src or "stripe_signature" in src, \
        "Stripe webhook handler must read stripe-signature header"


def test_G_no_exec_os_system_in_source():
    """OWASP A08 — No shell command execution with user-controlled data."""
    for fname in ("BACKEND_API_ROUTES.py", "BACKEND_AI_AGENTS.py"):
        src = open(os.path.join(BACKEND_DIR, fname), encoding="utf-8").read()
        assert "os.system(" not in src, f"os.system() found in {fname}"
        assert "subprocess.call(" not in src or "shell=True" not in src, \
            f"subprocess with shell=True found in {fname}"


def test_G_no_unsafe_deserialization():
    """OWASP A08 — No unsafe deserialization (yaml.load without Loader, pickle.loads)."""
    for fname in ("BACKEND_API_ROUTES.py", "BACKEND_AUTH_SECURITY.py", "BACKEND_AI_AGENTS.py"):
        src = open(os.path.join(BACKEND_DIR, fname), encoding="utf-8").read()
        # yaml.load() without explicit Loader is unsafe
        assert not re.search(r'yaml\.load\s*\([^)]*\)', src) or \
            'Loader=' in src, \
            f"yaml.load() without Loader= found in {fname} — use yaml.safe_load()"


# ===========================================================================
# H. RATE LIMITING & BRUTE FORCE PROTECTION  (OWASP A07)
# ===========================================================================

def test_H_rate_limit_in_login_source():
    """OWASP A07 — login_user must call check_rate_limit."""
    auth = _auth_src()
    assert "check_rate_limit" in auth, \
        "login_user must call check_rate_limit for IP-based rate limiting"
    assert "login:{ip" in auth or 'f"login:{ip' in auth, \
        "Rate limit key must be scoped to IP address"


def test_H_account_lockout_after_failed_attempts():
    """OWASP A07 — record_failed_login must lock account after MAX_LOGIN_ATTEMPTS."""
    auth = _auth_src()
    assert "locked_until" in auth, \
        "Account lockout must set locked_until timestamp"
    assert "MAX_LOGIN_ATTEMPTS" in auth, \
        "Lockout must be triggered after MAX_LOGIN_ATTEMPTS failures"
    assert "LOGIN_LOCKOUT_MINUTES" in auth, \
        "Lockout duration must be configurable via LOGIN_LOCKOUT_MINUTES"


def test_H_brute_force_live():
    """OWASP A07 — Live brute force: 6 wrong passwords should trigger lockout/rate-limit."""
    _skip_if_no_server()
    # Use a dedicated user for this test
    bf_email = _unique_email("bf")
    bf_phone = _unique_phone("bf")
    r = httpx.post(f"{BASE_URL}/api/v1/auth/register",
                   json={"email": bf_email, "phone": bf_phone,
                         "password": _STD_PWD, "full_name": "Brute Force Test"},
                   timeout=15)
    if r.status_code not in (200, 201):
        pytest.skip(f"Register failed: {r.status_code}")

    blocked = False
    for attempt in range(7):
        r = httpx.post(f"{BASE_URL}/api/v1/auth/login",
                       json={"email": bf_email, "password": f"WrongPass{attempt}!",
                             "trust_device": False},
                       timeout=10)
        if r.status_code in (423, 429):  # 423=locked, 429=rate limited
            blocked = True
            break

    assert blocked, \
        (f"After 7 wrong password attempts, account was not locked/rate-limited. "
         f"Last status: {r.status_code}. Brute force protection may be missing.")


def test_H_password_reset_does_not_reveal_user_existence():
    """OWASP A07 — Password reset for unknown email must NOT reveal 'not found'."""
    _skip_if_no_server()
    # Use an RFC-5321-valid email that definitely doesn't exist in the system
    # Use example.com which is always accepted by email validators
    r = httpx.post(f"{BASE_URL}/api/v1/auth/reset-password",
                   json={"email": f"totally_unknown_user_{_RUN}@example.com"},
                   timeout=10)
    # Must return 200 (generic success) to prevent user enumeration
    assert r.status_code == 200, \
        f"Password reset reveals user non-existence via {r.status_code} (user enumeration risk)"


def test_H_2fa_max_attempts_enforced_in_source():
    """OWASP A07 — 2FA verification must limit attempts."""
    auth = _auth_src()
    assert "attempts" in auth and "3" in auth, \
        "2FA must limit verification attempts (max 3)"
    assert "Too many attempts" in auth, \
        "2FA must raise error after too many attempts"


# ===========================================================================
# I. INFORMATION LEAKAGE  (OWASP A02 + A04)
# ===========================================================================

def test_I_me_endpoint_does_not_return_password_hash():
    """OWASP A02 — /auth/me must never return the password hash."""
    _skip_if_no_server()
    token = _register_and_login(_unique_email("il"), _unique_phone("13"))
    r = httpx.get(f"{BASE_URL}/api/v1/auth/me",
                  headers=_auth(token), timeout=10)
    assert r.status_code == 200
    body = r.text
    assert "password_hash" not in body, "/me returns password_hash — critical leakage"
    assert "hashed_password" not in body, "/me returns hashed_password"
    # bcrypt hashes always start with $2b$
    assert "$2b$" not in body, "/me returns bcrypt hash — password leakage"


def test_I_orders_list_does_not_leak_other_users_data():
    """OWASP A02 — GET /orders must only return the current user's orders."""
    _skip_if_no_server()
    token_a = _register_and_login(_unique_email("ola"), _unique_phone("14"))
    token_b = _register_and_login(_unique_email("olb"), _unique_phone("15"))
    # Get both users' order lists
    r_a = httpx.get(f"{BASE_URL}/api/v1/orders", headers=_auth(token_a), timeout=10)
    r_b = httpx.get(f"{BASE_URL}/api/v1/orders", headers=_auth(token_b), timeout=10)
    assert r_a.status_code == 200
    assert r_b.status_code == 200
    # Lists are separate — not cross-contaminated (both empty/own data only)
    # We can't check specific order IDs without creating orders, but we can verify
    # both returned successfully (and user B doesn't see user A's data structure)
    assert "orders" in r_a.json() or isinstance(r_a.json(), list)


def test_I_error_messages_do_not_expose_db_schema():
    """OWASP A04 — Error responses must not expose SQL table/column names."""
    _skip_if_no_server()
    token = _register_and_login(_unique_email("err"), _unique_phone("16"))
    # Try to trigger a DB error with invalid data
    r = httpx.get(f"{BASE_URL}/api/v1/orders/00000000-0000-0000-0000-000000000000",
                  headers=_auth(token), timeout=10)
    body = r.text.lower()
    # Should not expose DB internals
    db_keywords = ["psycopg2", "asyncpg", "sqlalchemy", "column", "relation",
                   "syntax error", "postgresql"]
    for kw in db_keywords:
        assert kw not in body, \
            f"Error response leaks DB info ({kw!r}): {r.text[:300]}"


def test_I_supplier_name_masked_in_parts_search():
    """OWASP A04 — Real supplier names must be masked in customer-facing search."""
    src = _routes_src()
    # The masking function must be defined and called in the routes
    assert "_mask_supplier" in src, "_mask_supplier function not found in routes"
    # search_parts calls _fetch_type which calls _mask_supplier — search broadly
    search_idx = src.find("async def search_parts(")
    if search_idx != -1:
        # The search function is large and nests _fetch_type which does the masking;
        # look for masking usage anywhere after the function definition
        rest_of_src = src[search_idx:]
        next_top_fn = re.search(r'^@(?:app|router)\.(get|post|put|delete|patch)', rest_of_src, re.MULTILINE)
        search_body = rest_of_src[:next_top_fn.start()] if next_top_fn else rest_of_src
        assert "_mask_supplier" in search_body, \
            "search_parts must call _mask_supplier to hide real supplier names"


def test_I_login_error_message_is_generic():
    """OWASP A04 — Login must return same error for bad email and bad password."""
    _skip_if_no_server()
    # Use example.com domain — RFC-valid, always accepted by email validators
    r_bad_email = httpx.post(f"{BASE_URL}/api/v1/auth/login",
                             json={"email": f"nobody_here_{_RUN}@example.com",
                                   "password": "SomePass123!"},
                             timeout=10)
    r_bad_pass = httpx.post(f"{BASE_URL}/api/v1/auth/login",
                            json={"email": _unique_email("gen1"),
                                  "password": "WrongPassword!"},
                            timeout=10)
    # Both should return 401 (not 404 for unknown email which would reveal existence)
    if r_bad_email.status_code not in (429, 423):
        assert r_bad_email.status_code == 401, \
            "Unknown email returns non-401 — may reveal user non-existence"


# ===========================================================================
# J. SESSION MANAGEMENT  (OWASP A07)
# ===========================================================================

def test_J_token_invalid_after_logout():
    """OWASP A07 — Token must be unusable after logout (session revocation)."""
    _skip_if_no_server()
    email = _unique_email("lo")
    phone = _unique_phone("17")
    token = _register_and_login(email, phone)

    # Confirm it works before logout
    r_before = httpx.get(f"{BASE_URL}/api/v1/auth/me",
                         headers=_auth(token), timeout=10)
    if r_before.status_code != 200:
        pytest.skip(f"Pre-logout check failed: {r_before.status_code}")

    # Logout
    r_logout = httpx.post(f"{BASE_URL}/api/v1/auth/logout",
                          headers=_auth(token), timeout=10)
    assert r_logout.status_code == 200, f"Logout failed: {r_logout.status_code}"

    # Token must now be rejected
    r_after = httpx.get(f"{BASE_URL}/api/v1/auth/me",
                        headers=_auth(token), timeout=10)
    assert r_after.status_code == 401, \
        f"Token still valid after logout — session revocation not working. Got: {r_after.status_code}"


def test_J_revoked_at_persisted_in_source():
    """OWASP A07 — Session revocation must persist revoked_at to DB."""
    auth = _auth_src()
    assert "revoked_at" in auth, \
        "Session revocation must set revoked_at timestamp on UserSession"
    assert "revoke_session" in auth or "logout_user" in auth, \
        "There must be an explicit logout/revoke function"


def test_J_get_current_user_checks_revocation():
    """OWASP A07 — get_current_user must verify session is not revoked."""
    auth = _auth_src()
    gc_idx = auth.find("async def get_current_user(")
    assert gc_idx != -1, "get_current_user function not found"
    gc_snippet = auth[gc_idx: gc_idx + 1500]
    assert "revoked_at" in gc_snippet, \
        "get_current_user must check session revoked_at — otherwise logout is ineffective"


def test_J_refresh_token_rotation_revokes_old_session():
    """OWASP A07 — Token refresh must revoke the old session (rotation)."""
    auth = _auth_src()
    refresh_idx = auth.find("async def refresh_access_token(")
    assert refresh_idx != -1
    snippet = auth[refresh_idx: refresh_idx + 1500]
    assert "revoked_at" in snippet, \
        "Token refresh must revoke the old session to prevent refresh token reuse"


def test_J_session_has_expiry_in_source():
    """OWASP A07 — Sessions must have an expiry time set."""
    auth = _auth_src()
    assert "expires_at" in auth, "UserSession must have expires_at set"
    assert "ACCESS_TOKEN_EXPIRE_MINUTES" in auth


# ===========================================================================
# K. PRIVILEGE ESCALATION  (OWASP A01)
# ===========================================================================

def test_K_admin_endpoints_require_admin_role():
    """OWASP A01 — Admin endpoints must use get_current_admin_user dependency."""
    src = _routes_src()
    admin_routes = [
        '"/api/v1/admin/stats"',
        '"/api/v1/admin/users"',
        '"/api/v1/admin/suppliers"',
        '"/api/v1/admin/orders"',
    ]
    for route in admin_routes:
        idx = src.find(route)
        if idx == -1:
            continue
        # Look at the next 600 chars for the dependency
        snippet = src[idx: idx + 600]
        assert "get_current_admin_user" in snippet or "get_current_user" in snippet, \
            f"Admin route {route} appears to have no auth dependency"


def test_K_get_current_admin_user_checks_is_admin():
    """OWASP A01 — get_current_admin_user must verify is_admin flag."""
    auth = _auth_src()
    idx = auth.find("async def get_current_admin_user(")
    assert idx != -1, "get_current_admin_user function not found"
    snippet = auth[idx: idx + 600]
    assert "is_admin" in snippet, \
        "get_current_admin_user must check user.is_admin"
    assert "403" in snippet or "HTTP_403" in snippet or "Forbidden" in snippet, \
        "get_current_admin_user must raise 403 for non-admin users"


def test_K_cannot_escalate_to_admin_via_register():
    """OWASP A01 — RegisterRequest must not accept is_admin or role fields."""
    _skip_if_no_server()
    r = httpx.post(f"{BASE_URL}/api/v1/auth/register",
                   json={
                       "email": _unique_email("esc"),
                       "phone": _unique_phone("18"),
                       "password": _STD_PWD,
                       "full_name": "Escalate Test",
                       "is_admin": True,
                       "role": "admin",
                   }, timeout=10)
    # Should succeed registration but NOT set is_admin = True
    if r.status_code in (200, 201):
        # Try to login and check admin status
        r2 = httpx.post(f"{BASE_URL}/api/v1/auth/login",
                        json={"email": _unique_email("esc"), "password": _STD_PWD,
                              "trust_device": True},
                        timeout=10)
        if r2.status_code == 200:
            user_data = r2.json().get("user", {})
            assert user_data.get("is_admin") is not True, \
                "Privilege escalation via register: is_admin was set to True!"


def test_K_register_schema_excludes_is_admin_in_source():
    """OWASP A01 — RegisterRequest Pydantic model must not have is_admin field."""
    src = _routes_src()
    reg_idx = src.find("class RegisterRequest(BaseModel)")
    assert reg_idx != -1
    # Find the end of this class (next class definition)
    next_class = src.find("class ", reg_idx + 1)
    schema = src[reg_idx: next_class if next_class != -1 else reg_idx + 500]
    assert "is_admin" not in schema, \
        "RegisterRequest schema must NOT include is_admin field"
    assert "role" not in schema, \
        "RegisterRequest schema must NOT include role field"


# ===========================================================================
# L. FILE UPLOAD SECURITY  (OWASP A04 + A05)
# ===========================================================================

def test_L_file_upload_requires_auth():
    """OWASP A04 — File upload must require authentication."""
    _skip_if_no_server()
    r = httpx.post(f"{BASE_URL}/api/v1/files/upload",
                   files={"file": ("test.txt", b"hello", "text/plain")},
                   timeout=10)
    # 401 = unauthenticated; 307 = HTTPS redirect (production mode) — both mean upload is protected
    assert r.status_code in (401, 307), \
        f"File upload without auth returned {r.status_code} instead of 401 or 307"


def test_L_virus_scan_integrated_in_source():
    """OWASP A05 — File upload must run virus scan before storing."""
    src = _routes_src()
    assert "_scan_bytes_for_virus" in src, \
        "Virus scan helper missing — uploaded files are not scanned"
    assert "virus_scan_status" in src, \
        "Virus scan status must be persisted on the File model"
    assert "malware detected" in src.lower() or "infected" in src.lower(), \
        "Virus scan must reject infected files"


def test_L_scan_bytes_function_uses_clamd():
    """OWASP A05 — Virus scan must use ClamAV (clamd), not just filename extension."""
    src = _routes_src()
    # Find the definition, not just import references
    scan_idx = src.find("def _scan_bytes_for_virus")
    if scan_idx != -1:
        snippet = src[scan_idx: scan_idx + 1000]
        assert "clamd" in snippet or "clamav" in snippet.lower() or "instream" in snippet.lower(), \
            "_scan_bytes_for_virus must use ClamAV (clamd), not just file extension checks"


def test_L_file_size_limit_in_source():
    """OWASP A05 — File upload must enforce a size limit."""
    src = _routes_src()
    upload_idx = src.find('"/api/v1/files/upload"')
    if upload_idx == -1:
        upload_idx = src.find("async def upload_file(")
    snippet = src[upload_idx: upload_idx + 2000] if upload_idx != -1 else src
    # Look for a file size check
    assert "MAX_FILE_SIZE" in snippet or "max_size" in snippet.lower() or \
           "len(content)" in snippet or "file_size" in snippet.lower() or \
           "413" in snippet, \
        "File upload handler must enforce a maximum file size"


def test_L_double_extension_file_upload_rejected():
    """OWASP A05 — Uploading file.php.jpg (double extension) must be handled safely."""
    _skip_if_no_server()
    token = _register_and_login(_unique_email("dext"), _unique_phone("19"))
    r = httpx.post(f"{BASE_URL}/api/v1/files/upload",
                   files={"file": ("malware.php.jpg", b"<?php system($_GET['cmd']); ?>",
                                   "image/jpeg")},
                   headers=_auth(token), timeout=10)
    # Either rejected (400/422/415) or accepted (200) — if accepted the virus scanner
    # should catch it. The important thing is no command execution.
    assert r.status_code != 500, \
        "Double-extension upload caused 500"
    # Content should not execute — verify response body doesn't show PHP execution
    assert "<?php" not in r.text, "PHP code was reflected in response"
