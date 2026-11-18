"""task_heal_importer_price must (a) only select healable rows and (b) match the partial index predicate."""
import inspect, re
import db_cleanup_agent as d


def _norm(s):
    return re.sub(r"\s+", " ", s)


def test_gap_query_only_selects_healable_rows():
    src = _norm(inspect.getsource(d.task_heal_importer_price))
    assert "AND EXISTS (SELECT 1 FROM supplier_parts sp0" in src
    assert "s0.country = 'Israel'" in src


def test_gap_query_matches_partial_index_predicate():
    src = _norm(inspect.getsource(d.task_heal_importer_price))
    mig = _norm(open("/app/alembic/versions/0063_importer_gap_partial_index.py").read())
    for frag in ("is_active", "max_price_ils > 0", "importer_price_ils IS NULL OR importer_price_ils = 0",
                 "online_price_ils IS NULL OR online_price_ils = 0"):
        assert frag in src and frag in mig
