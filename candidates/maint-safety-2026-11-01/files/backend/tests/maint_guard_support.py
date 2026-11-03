"""Shared fixtures helpers for the maintenance-guard tests (isolated sandbox only).

The application keeps ONE module-global Redis client bound to the event loop it was created in; pytest-asyncio gives
every test its own loop, so the tests hand the guard a fresh client per test (`fresh_redis`) and neutral probes, so a
busy CI host can never flake a test that is not about load."""
import os

import redis.asyncio as aioredis

import maintenance_guard as mg


async def fresh_redis():
    url = os.environ["REDIS_URL"]
    if "sandbox" not in url:
        raise RuntimeError(f"refusing to use a non-sandbox Redis in a test: {url.split('@')[-1]}")
    return aioredis.from_url(url)


class Notices(list):
    """Recorder standing in for the owner notification (never a real WhatsApp message in a test)."""

    async def __call__(self, title, body):
        self.append((title, body))


def neutral_probes(monkeypatch):
    """Load / CPU never trip unless a test sets them on purpose."""
    monkeypatch.setattr(mg, "default_load_probe", lambda: 0.0)
    monkeypatch.setattr(mg, "default_cpu_probe", _zero_cpu)
    monkeypatch.setattr(mg, "_default_redis", fresh_redis)
    notices = Notices()
    monkeypatch.setattr(mg, "default_notify", notices)
    return notices


async def _zero_cpu():
    return 0.0
