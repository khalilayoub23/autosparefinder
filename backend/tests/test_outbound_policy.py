"""#100: live external-supplier traffic is fail-closed outside production, and no test can reach a real host.

The shared sandbox sent 971 requests to real suppliers and public APIs (2026-10-10..22): ENABLE_EXTERNAL_SUPPLIERS
was unset there and the old default was "on", so every site search started a background supplier lookup.
"""
import asyncio
import socket

import pytest

from network_guard import NetworkGuard, is_local
from outbound_policy import external_suppliers_enabled


@pytest.mark.parametrize("env,enabled", [
    ({"ENVIRONMENT": "production"}, True),                                    # production: unchanged default
    ({"ENVIRONMENT": "Production "}, True),
    ({"ENVIRONMENT": "production", "ENABLE_EXTERNAL_SUPPLIERS": ""}, True),
    ({"ENVIRONMENT": "production", "ENABLE_EXTERNAL_SUPPLIERS": "0"}, False),  # explicit off wins
    ({"ENVIRONMENT": "sandbox"}, False),                                       # the #100 case: unset -> off
    ({"ENVIRONMENT": "test"}, False),
    ({}, False),                                                               # no ENVIRONMENT at all -> off
    ({"ENVIRONMENT": "prod"}, False),                                          # misleading label -> off
    ({"ENVIRONMENT": "sandbox", "ENABLE_EXTERNAL_SUPPLIERS": "1"}, True),      # explicit opt-in only
    ({"ENVIRONMENT": "sandbox", "ENABLE_EXTERNAL_SUPPLIERS": "yes"}, True),
    ({"ENVIRONMENT": "sandbox", "ENABLE_EXTERNAL_SUPPLIERS": "maybe"}, False),  # unrecognised -> fail closed
    ({"ENVIRONMENT": "production", "ENABLE_EXTERNAL_SUPPLIERS": "enabled"}, False),
])
def test_rule(env, enabled):
    on, source = external_suppliers_enabled(env)
    assert on is enabled and source


def test_search_fan_out_gate_follows_the_rule(monkeypatch):
    from routes.parts import _should_fetch_external_suppliers
    monkeypatch.delenv("ENABLE_EXTERNAL_SUPPLIERS", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "sandbox")
    assert _should_fetch_external_suppliers("brake pads", object()) is False
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert _should_fetch_external_suppliers("brake pads", object()) is True


def _names(suppliers):
    return sorted(s.name for s in suppliers)


def test_supplier_registry_holds_only_the_local_catalogue_outside_production(monkeypatch):
    import services.supplier_aggregator as agg
    monkeypatch.delenv("ENABLE_EXTERNAL_SUPPLIERS", raising=False)
    for environment in ("sandbox", "test", ""):
        monkeypatch.setenv("ENVIRONMENT", environment)
        assert _names(agg._get_active_suppliers()) == ["local_db"]


def test_supplier_registry_is_unchanged_in_production(monkeypatch):
    """Production (ENVIRONMENT=production, variable unset) still registers every external supplier."""
    import services.supplier_aggregator as agg
    monkeypatch.delenv("ENABLE_EXTERNAL_SUPPLIERS", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    names = _names(agg._get_active_suppliers())
    assert {"local_db", "autodoc", "partsouq", "amayama"} <= set(names) and len(names) > 10, names


def test_aggregated_searches_make_no_external_request_outside_production(monkeypatch):
    """The paths that produced the incident: search_all_suppliers / search_by_oem_all with the registry as this
    environment builds it. The local catalogue supplier is replaced so no database is needed."""
    import services.supplier_aggregator as agg
    guard = NetworkGuard().install(monkeypatch)

    class _Local:
        name = "local_db"

        async def search(self, q, limit=10):
            return []

        async def search_by_oem(self, q, limit=10):
            return []
    monkeypatch.delenv("ENABLE_EXTERNAL_SUPPLIERS", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "sandbox")
    registry = [s if s.name != "local_db" else _Local() for s in agg._get_active_suppliers()]
    monkeypatch.setattr(agg, "ACTIVE_SUPPLIERS", registry)
    assert asyncio.run(agg.search_all_suppliers("xpeng brake pads")) == []
    assert asyncio.run(agg.search_by_oem_all("XP12345")) == []
    assert guard.blocked == []


@pytest.mark.parametrize("cls", ["AutodocSupplier", "PartSouqSupplier", "AmayamaSupplier"])
def test_even_an_explicitly_enabled_supplier_cannot_reach_its_site_from_a_test(cls, monkeypatch):
    """Defence in depth: if a test enables a real supplier, the request is refused before any packet is sent."""
    import importlib
    mod = {"AutodocSupplier": "services.suppliers.autodoc_supplier",
           "PartSouqSupplier": "services.suppliers.partsouq_supplier",
           "AmayamaSupplier": "services.suppliers.amayama_supplier"}[cls]
    supplier = getattr(importlib.import_module(mod), cls)()
    guard = NetworkGuard().install(monkeypatch)
    try:
        asyncio.run(supplier.search("15400-PLM-A01", 5))
    except Exception:
        pass                                     # suppliers may swallow or raise; the point is what was attempted
    assert guard.blocked, "the supplier made no lookup at all - the test proves nothing"
    assert all(not is_local(h) for h in guard.blocked)


def test_guard_allows_local_services_and_refuses_public_hosts(monkeypatch):
    for host in ("sandbox_postgres", "asfrel-sandbox_redis-1", "meilisearch", "localhost", "127.0.0.1", "10.0.0.5",
                 "172.21.0.3", "::1", "", None):
        assert is_local(host), host
    for host in ("partsouq.com", "www.autodoc.eu", "amayama.com", "api.ebay.com", "data.gov.il", "1.1.1.1",
                 "8.8.8.8", "evil.sandbox.example.com"):
        assert not is_local(host), host
    guard = NetworkGuard().install(monkeypatch)
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("partsouq.com", 443)
    assert guard.blocked == ["partsouq.com"]
