"""Email Agent handoff follow-through (2026-11-13): a routed mail whose task nobody started is reported, once."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from email_agent import owner_flow as of

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def _t(i, status="not_started", age_h=30, agent="BOAZ"):
    return {"id": f"{i:08x}-0000-0000-0000-000000000000", "agent": agent, "status": status,
            "created_at": NOW - timedelta(hours=age_h)}


def test_only_old_unstarted_tasks_are_stale():
    tasks = [_t(1), _t(2, age_h=2), _t(3, status="in_progress"), _t(4, status="completed")]
    assert [t["id"][:8] for t in of.stale_handoffs(tasks, NOW)] == ["00000001"]


def test_naive_created_at_is_treated_as_utc():
    t = _t(1)
    t["created_at"] = t["created_at"].replace(tzinfo=None)
    assert len(of.stale_handoffs([t], NOW)) == 1


class _Todos:
    def __init__(self, tasks):
        self.tasks = tasks

    async def open_email_tasks(self, limit=15):
        return self.tasks


def test_stale_handoffs_alert_once_with_fingerprint_and_real_cause(monkeypatch):
    calls = []

    async def notify(cat, title, body, **kw):
        calls.append((cat, title, body, kw))
        return {"ok": True}

    monkeypatch.delenv("BOAZ_TASKS_ENABLED", raising=False)
    n = asyncio.run(of.check_handoffs(_Todos([_t(1), _t(2)]), notify, NOW))
    assert n == 2 and len(calls) == 1
    cat, title, body, kw = calls[0]
    assert kw["alert_key"] == "email_handoff_stale" and kw["fingerprint"] == "00000001,00000002"
    assert "BOAZ: 2" in body and "BOAZ_TASKS_ENABLED" in body


def test_executor_on_does_not_claim_it_is_off(monkeypatch):
    calls = []

    async def notify(cat, title, body, **kw):
        calls.append(body)
        return {"ok": True}

    monkeypatch.setenv("BOAZ_TASKS_ENABLED", "1")
    asyncio.run(of.check_handoffs(_Todos([_t(1)]), notify, NOW))
    assert "כבוי" not in calls[0]
