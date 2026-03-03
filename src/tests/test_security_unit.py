import sys
import pathlib
import asyncio
import re
import time
import pytest

# ensure repository root is on sys.path for imports when running tests in CI
repo_root = pathlib.Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.append(str(repo_root))

from src.auth import security


class FakeRedis:
    def __init__(self):
        self._data = {}

    async def get(self, key):
        v = self._data.get(key)
        if not v:
            return None
        value, expires_at = v
        if expires_at and time.time() > expires_at:
            del self._data[key]
            return None
        return value

    async def setex(self, key, ttl, value):
        self._data[key] = (str(value), time.time() + ttl)

    async def incr(self, key):
        cur = await self.get(key)
        if cur is None:
            await self.setex(key, 60, "1")
            return 1
        new = int(cur) + 1
        # preserve ttl
        _, expires_at = self._data[key]
        self._data[key] = (str(new), expires_at)
        return new

    async def ttl(self, key):
        v = self._data.get(key)
        if not v:
            return -2
        _, expires_at = v
        return int(expires_at - time.time())

    async def close(self):
        self._data.clear()


def test_password_hash_and_verify():
    pw = "Str0ngPass!"
    h = security.hash_password(pw)
    assert security.verify_password(pw, h)
    assert not security.verify_password("wrong", h)


def test_password_strength():
    ok, _ = security.validate_password_strength("GoodPass1")
    assert ok
    bad, msg = security.validate_password_strength("short")
    assert not bad and "at least 8" in msg


def test_jwt_create_and_decode():
    data = {"user_id": "123"}
    at = security.create_access_token(data)
    rt = security.create_refresh_token(data)
    payload = security.decode_token(at, token_type="access")
    assert payload["user_id"] == "123"
    payload_r = security.decode_token(rt, token_type="refresh")
    assert payload_r["user_id"] == "123"


def test_jwt_type_mismatch_raises():
    data = {"user_id": "1"}
    at = security.create_access_token(data)
    try:
        security.decode_token(at, token_type="refresh")
        assert False, "should have raised"
    except Exception:
        pass


def test_generate_device_fingerprint_consistent():
    class Req:
        client = type("C", (), {"host": "1.2.3.4"})
        headers = {"user-agent": "ua", "accept-language": "he"}

    r = Req()
    f1 = security.generate_device_fingerprint(r)
    f2 = security.generate_device_fingerprint(r)
    assert f1 == f2
    assert re.fullmatch(r"[0-9a-f]{64}", f1)


def test_generate_2fa_code_format():
    c = security.generate_2fa_code()
    assert c.isdigit() and len(c) == 6


def test_rate_limit_allows_then_blocks():
    fr = FakeRedis()
    key = "rl:test"
    allowed, rem = asyncio.run(security.check_rate_limit(key, 3, 2, fr))
    assert allowed and rem == 2
    allowed, rem = asyncio.run(security.check_rate_limit(key, 3, 2, fr))
    assert allowed and rem == 1
    allowed, rem = asyncio.run(security.check_rate_limit(key, 3, 2, fr))
    assert allowed and rem == 0
    allowed, rem = asyncio.run(security.check_rate_limit(key, 3, 2, fr))
    assert not allowed


def test_is_ip_block_and_block_ip():
    fr = FakeRedis()
    ip = "10.0.0.1"
    blocked, _ = asyncio.run(security.is_ip_blocked(ip, fr))
    assert not blocked
    asyncio.run(security.block_ip(ip, fr, minutes=1))
    blocked, ttl = asyncio.run(security.is_ip_blocked(ip, fr))
    assert blocked and (ttl is None or ttl > 0)
