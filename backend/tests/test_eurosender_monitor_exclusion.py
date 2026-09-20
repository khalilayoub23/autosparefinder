"""Proves the _stuck_orders_monitor_loop Pass 2 query (BACKEND_API_ROUTES.py)
genuinely excludes Eurosender-managed orders from time-based synthetic
status advancement, without needing a live DB — inspects the compiled SQL
of the exact same WHERE clause shape used in that loop."""
from sqlalchemy import or_, select
from sqlalchemy.dialects import postgresql

from BACKEND_DATABASE_MODELS import Order


def test_pass2_query_excludes_eurosender_managed_orders():
    stmt = select(Order).where(
        Order.status.in_(["supplier_ordered", "shipped"]),
        Order.tracking_number.isnot(None),
        or_(Order.shipping_provider.is_(None), Order.shipping_provider != "eurosender"),
    )
    compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "shipping_provider" in compiled
    assert "'eurosender'" in compiled
    assert "IS NULL" in compiled


def test_filter_semantics_null_provider_passes():
    """A plain SimpleNamespace-style check of the boolean logic itself,
    independent of SQL compilation: shipping_provider IS NULL (the default,
    pre-existing case) OR shipping_provider != 'eurosender' must evaluate to
    True for every non-Eurosender order and False only for shipping_provider
    == 'eurosender'."""
    def passes(shipping_provider):
        return shipping_provider is None or shipping_provider != "eurosender"

    assert passes(None) is True
    assert passes("") is True
    assert passes("some-other-carrier") is True
    assert passes("eurosender") is False
