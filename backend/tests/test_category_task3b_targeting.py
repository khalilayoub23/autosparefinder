"""task3b (LLM keyword teaching) must target the most frequent UNKNOWN token of the stuck parts — read
from the DB — and skip tokens already waiting for the owner (FIXES_TRACKER #71).

Live evidence: task3 pops stuck ids out of _uncategorized_index, so task3b's ranking always saw empty
text and fell back to arbitrary parts: 0 "targeting" log lines in 356 LLM batches (2026-10-18..20),
while 'vest' (already awaiting the owner) kept collecting votes (128,965).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db_cleanup_agent as dca  # noqa: E402


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Db:
    calls = []

    def __init__(self, blobs):
        self.blobs = blobs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        _Db.calls.append(sql)
        if "id = ANY(CAST(:ids AS uuid[]))" in sql:          # the targeting read (#71)
            return _Rows([(pid, b) for pid, b in self.blobs.items() if pid in params["ids"]])
        return _Rows([])                                       # the batch fetch: stop before the LLM


def test_targets_the_frequent_unknown_token_not_one_awaiting_the_owner(monkeypatch, capsys):
    import category_learning as cl
    blobs = {"p1": "vest clamp", "p2": "vest clamp hose", "p3": "vest strap", "p4": "clamp bracket"}
    monkeypatch.setattr(dca, "_unclassifiable", set(blobs))
    monkeypatch.setattr(dca, "_uncategorized_index", {})          # popped by task3, as in production
    monkeypatch.setattr(dca, "_llm_fallback_cursor", [])
    monkeypatch.setattr(dca, "_llm_budget_check", lambda: (True, ""))
    monkeypatch.setattr(dca, "guess_category_by_text", lambda w: None)
    monkeypatch.setattr(dca, "scraper_session_factory", lambda: _Db(blobs))

    async def _awaiting(db, limit=15):
        return [{"token": "vest", "category": "engine"}]
    monkeypatch.setattr(cl, "pending_for_owner", _awaiting)
    monkeypatch.setattr(cl, "is_blocked", lambda w: False)
    asyncio.run(dca.task3b_llm_category_fallback(batch_size=25))
    out = capsys.readouterr().out
    # 'vest' is in 3 parts but already waits for the owner; 'clamp' (3 parts) is the token to teach
    assert "targeting unknown token 'clamp' (3 parts in the stuck sample)" in out
    assert any("id = ANY(CAST(:ids AS uuid[]))" in c for c in _Db.calls)
