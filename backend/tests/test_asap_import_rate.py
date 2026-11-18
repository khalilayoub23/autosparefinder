import asyncio
import importers.asap_import as A


class _Conn:
    def __init__(self, vals): self.vals = vals
    async def fetchval(self, sql, key): return self.vals.get(key)


def test_uses_live_rate_not_hardcoded_fallback():
    assert asyncio.run(A.live_usd_ils(_Conn({"ils_per_usd": "3.250000"}))) == 3.25


def test_legacy_key_then_loud_fallback(capsys):
    assert asyncio.run(A.live_usd_ils(_Conn({"currency_exchange_rate_usd_to_ils": "3.3"}))) == 3.3
    assert asyncio.run(A.live_usd_ils(_Conn({"ils_per_usd": "garbage"}))) == 3.72
    assert "WARNING" in capsys.readouterr().out


def test_importer_never_calls_rate_helper_without_db():
    import inspect
    assert "await get_usd_to_ils_rate()" not in inspect.getsource(A)
