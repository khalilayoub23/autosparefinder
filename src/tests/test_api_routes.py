import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[1] / ".." / "BACKEND_API_ROUTES.py"


def test_api_routes_file_exists():
    assert SRC.exists(), "BACKEND_API_ROUTES.py must exist at repository root"


def test_key_endpoints_declared():
    src = SRC.read_text()
    # check presence of several critical endpoints from the spec
    required = [
        r"/api/v1/system/health",
        r"/api/v1/auth/register",
        r"/api/v1/auth/login",
        r"/api/v1/parts/search",
        r"/api/v1/orders",
        r"/api/v1/admin/stats",
    ]
    for r in required:
        assert re.search(re.escape(r), src), f"expected route {r} in BACKEND_API_ROUTES.py"


def test_minimum_route_count():
    src = SRC.read_text()
    # count occurrences of @app.<method>("/api/v1/") as proxy for endpoints
    matches = re.findall(r"@app\.(get|post|put|delete|patch|websocket)\(\"/api/v1/", src)
    assert len(matches) >= 25, f"expected at least 25 declared /api/v1 routes, found {len(matches)}"


def test_health_endpoint_response_signature():
    # static check that health endpoint returns 'status' key in example
    src = SRC.read_text()
    assert "/api/v1/system/health" in src
    assert '"status"' in src or 'healthy' in src or 'timestamp' in src
