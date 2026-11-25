"""Mission 4: the UI audit fixtures (docs/evidence/2026-11-25-ui-m4/fixtures.py) must have EXACTLY the keys the production routes
return. Keys are read from the route source (AST), so a route change that is not mirrored in the fixtures fails here, and the
'mocked but contract-faithful' claim of the audit stays true."""
import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import pytest

FIX = ROOT / "docs/evidence/2026-11-25-ui-m4/fixtures.py"
if not FIX.exists():                       # the container mounts only backend/ -> /app (CLAUDE.md §4): host-side evidence is absent there
    pytest.skip("repo docs/ not mounted in this environment", allow_module_level=True)
spec = importlib.util.spec_from_file_location("ui_fixtures", FIX)
fx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fx)


def dict_keys_with(file, func, marker):
    tree = ast.parse((ROOT / "backend" / file).read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == func:
            for d in ast.walk(fn):
                if isinstance(d, ast.Dict):
                    keys = {k.value for k in d.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                    if marker in keys:
                        yield keys
    return


def one(file, func, marker, extra=()):
    found = list(dict_keys_with(file, func, marker))
    assert found, (file, func, marker)
    return max(found, key=len) | set(extra)


def test_cart_item():
    # _cart_to_response builds the item dict, then adds shippingCost in a second loop
    keys = one("routes/cart.py", "_cart_to_response", "partId", extra=("shippingCost",))
    for it in fx.CART["items"]:
        assert set(it) == keys, set(it) ^ keys


def test_orders_list_and_detail():
    assert all(set(o) == one("routes/orders.py", "get_orders", "order_number") for o in fx.ORDERS["orders"])
    assert set(fx.ORDER_DETAIL) == one("routes/orders.py", "get_order", "subtotal")
    assert all(set(i) == one("routes/orders.py", "get_order", "part_name") for i in fx.ORDER_DETAIL["items"])


def test_admin_tables():
    assert all(set(o) == one("routes/admin.py", "get_all_orders_admin", "user_email") for o in fx.ADMIN_ORDERS["orders"])
    assert all(set(u) == one("routes/admin.py", "get_admin_users", "failed_login_count") for u in fx.ADMIN_USERS["users"])
    assert all(set(s) == one("routes/admin.py", "get_admin_suppliers", "reliability_score") for s in fx.ADMIN_SUPPLIERS["suppliers"])
    assert all(set(s) == one("routes/admin.py", "get_admin_supplier_orders", "is_done") for s in fx.ADMIN_SUPPLIER_ORDERS["supplier_orders"])
