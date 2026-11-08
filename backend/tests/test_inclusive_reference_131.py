"""#131: the Israeli consumer reference (max_price_ils) and min_price_ils must respect the supplier VAT basis: an inclusive
supplier's stored cost already contains 18% VAT (never x1.18 again), exclusive gets it once, unknown local contributes nothing."""
import asyncio
import re

import db_cleanup_agent as dca
import db_update_agent as dua


class _Res:
    def scalar_one(self): return 0


class _DB:
    sql = ""
    async def execute(self, stmt, params=None):
        _DB.sql = str(stmt)
        return _Res()


def _refresh_sql():
    asyncio.run(dua.refresh_min_max_for_parts(_DB(), ["00000000-0000-0000-0000-000000000000"], 3.16))
    return re.sub(r"\s+", " ", _DB.sql)


def test_refresh_uses_basis_for_min_and_max():
    sql = _refresh_sql()
    assert "vat_price_basis" in sql and "'inclusive' THEN 1 ELSE CAST(:vat AS numeric) END" in sql
    assert "NOT (" in sql and "'exclusive'" in sql                      # unknown/unclassified local supplier excluded


def test_cleanup_fill_normalizes_inclusive_to_ex_vat():
    sql = re.sub(r"\s+", " ", dca._cleanup_sql(dca._FILL_COST_COLUMNS_SQL))
    assert "'inclusive' THEN 1.18 ELSE 1 END" in sql and "'exclusive'" in sql
    assert "ROUND((c.local_min * 1.18)::numeric, 2)" in sql            # ex-VAT x1.18 = consumer incl. VAT, once


def test_one_implementation_of_the_refresh():
    import inspect, catalog_scraper
    assert "refresh_min_max_for_parts" in inspect.getsource(catalog_scraper)
