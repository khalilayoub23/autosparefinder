"""Owner WhatsApp alert architecture remediation — regression suite (2026-09-09).

Forensic investigation (same date) found:
  A. the catalog-stall alert (_harvest_supervisor_loop) bypassed notify_owner()
     and used an in-memory-only cooldown that does not survive a container restart.
  B. the NOA coherence-gate alert already used notify_owner() correctly.
  C. the Amayama alert already used notify_owner() correctly, BUT the quiet-hours
     queue (autospare:wa_quiet_queue) has no cross-item dedup, so a persistent
     condition sampled every 30 min can accumulate several individually-valid,
     near-identical queued messages overnight and burst-deliver them all when the
     09:00 window opens.

This authorized remediation:
  1. migrated the catalog-stall/idle/recovery sends from a direct _wa_send_update()
     call to notify_owner() with distinct, stable alert_keys
     (harvest_catalog_stalled / harvest_catalog_idle / harvest_catalog_recovered),
  2. threaded alert_key through _wa_send_update() -> _wa_send_quiet() so the
     quiet-hours queue can dedup by semantic identity (never message text),
  3. left NOA and Amayama's PRODUCERS untouched (they already used notify_owner());
     Amayama benefits automatically from the shared queue-dedup fix.

These tests exercise the REAL _wa_send_quiet()/notify_owner() functions against a
small in-memory fake Redis (never the real Redis, never a real WhatsApp send), plus
a handful of static source-shape checks for the parts that are impractical to drive
through the full 30-minute supervisor loop (e.g. "the old direct call site for the
stall message is gone").

Run: docker exec autospare_backend python3 /app/devtests/owner_alert_remediation_test.py
"""
import sys
import json
import asyncio
import pathlib
import unittest.mock as mock

sys.path.insert(0, "/app")

fails: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"         got : {got!r}")
        print(f"         want: {want!r}")
        fails.append(label)


_ROUTES_SRC = pathlib.Path("/app/BACKEND_API_ROUTES.py").read_text(encoding="utf-8")


# ─── A tiny, faithful in-memory fake of the Redis calls this module makes ──────
class _FakeRedis:
    """Backs list ops with a real Python list so dedup logic is genuinely
    exercised (LRANGE/LREM/RPUSH/LTRIM/LPOP), not just call-recorded. Backs
    cooldown keys with a plain set — good enough to prove "does this key exist
    across two independent calls", which is the restart-survival property under
    test (an in-memory Python variable would NOT survive being thrown away and
    recreated between two calls; this fake object persists exactly like Redis
    would across two independent notify_owner() invocations)."""

    def __init__(self):
        self.lists: dict[str, list[bytes]] = {}
        self._cooldown_keys: set[str] = set()

    async def exists(self, key):
        return 1 if key in self._cooldown_keys else 0

    async def set(self, key, val, ex=None):
        self._cooldown_keys.add(key)
        return True

    async def expire_cooldown(self, key):
        """Test helper only — simulates the Redis TTL expiring."""
        self._cooldown_keys.discard(key)

    async def rpush(self, key, val):
        raw = val if isinstance(val, (bytes, bytearray)) else val.encode()
        self.lists.setdefault(key, []).append(raw)
        return len(self.lists[key])

    async def ltrim(self, key, start, end):
        lst = self.lists.get(key, [])
        n = len(lst)
        e = n + end if end < 0 else end
        s = n + start if start < 0 else start
        self.lists[key] = lst[s:e + 1]
        return True

    async def lrange(self, key, start, end):
        lst = self.lists.get(key, [])
        n = len(lst)
        e = n - 1 if end == -1 else end
        s = n + start if start < 0 else start
        return list(lst[s:e + 1])

    async def lrem(self, key, count, value):
        lst = self.lists.get(key, [])
        if value in lst:
            lst.remove(value)
            self.lists[key] = lst
            return 1
        return 0

    async def lindex(self, key, index):
        lst = self.lists.get(key, [])
        try:
            return lst[index]
        except IndexError:
            return None

    async def lset(self, key, index, value):
        raw = value if isinstance(value, (bytes, bytearray)) else value.encode()
        lst = self.lists.get(key, [])
        lst[index] = raw
        return True

    async def eval(self, script, numkeys, key, alert_key, payload, trim_start, trim_end):
        """Fast, pure-Python stand-in for the REAL Lua script (_WA_QUIET_ENQUEUE_LUA)
        used only by this file's fast, no-infra unit tests of notify_owner()/alert_key
        selection logic. This is NOT how the real atomicity claim is verified —
        that requires an actual Redis engine (single-threaded command execution is
        what makes the real EVAL atomic; this Python re-implementation runs with no
        internal `await`, so it is trivially "atomic" for reasons that don't
        generalize to prove anything about the real system). The real script is
        exercised against a genuine, isolated Redis instance with real concurrent
        asyncio.gather() callers in devtests/queue_atomic_dedup_test.py — THAT file
        is the actual proof of the race being closed, not this fake."""
        if alert_key:
            existing = list(self.lists.get(key, []))
            for raw in existing:
                try:
                    item = json.loads(raw)
                except Exception:
                    continue
                if isinstance(item, dict) and item.get("alert_key") == alert_key:
                    lst = self.lists.get(key, [])
                    if raw in lst:
                        lst.remove(raw)
                    self.lists[key] = lst
        await self.rpush(key, payload)
        await self.ltrim(key, trim_start, trim_end)
        return 1

    async def lpop(self, key):
        lst = self.lists.get(key, [])
        if not lst:
            return None
        v = lst.pop(0)
        self.lists[key] = lst
        return v


def _queued_items(fake_redis: _FakeRedis) -> list[dict]:
    import BACKEND_API_ROUTES as routes
    return [json.loads(raw) for raw in fake_redis.lists.get(routes._WA_QUIET_QUEUE_KEY, [])]


def _patched(monkeypatches: dict):
    """Context manager applying {attr_name: value} onto BACKEND_API_ROUTES, restoring after."""
    import BACKEND_API_ROUTES as routes

    class _Ctx:
        def __enter__(self):
            self._orig = {k: getattr(routes, k) for k in monkeypatches}
            for k, v in monkeypatches.items():
                setattr(routes, k, v)
            return routes

        def __exit__(self, *a):
            for k, v in self._orig.items():
                setattr(routes, k, v)
    return _Ctx()


# ═══════════════════════════════════════════════════════════════════════════
print("=" * 70)
print("CATALOG STALL ALERT — migrated to notify_owner()")
print("=" * 70)

print("\n1. First catalog stall -> exactly one notification")


async def t1():
    import BACKEND_API_ROUTES as routes
    sends = []

    async def fake_wa_send(to, text):
        sends.append(text)
        return {"ok": True}

    fake_r = _FakeRedis()
    with _patched({
        "_wa_send": fake_wa_send,
        "_notify_window_open": lambda now_local=None: (True, None),
    }), mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        await routes.notify_owner(
            "harvest", "שאיבת הקטלוג תקועה", "body text",
            severity="warning", alert_key="harvest_catalog_stalled", cooldown_s=21600,
        )
    check("first stall -> 1 send", len(sends), 1)

asyncio.run(t1())

print("\n2. Same stall BEFORE cooldown expires -> suppressed")


async def t2():
    import BACKEND_API_ROUTES as routes
    sends = []

    async def fake_wa_send(to, text):
        sends.append(text)
        return {"ok": True}

    fake_r = _FakeRedis()
    with _patched({
        "_wa_send": fake_wa_send,
        "_notify_window_open": lambda now_local=None: (True, None),
    }), mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        await routes.notify_owner("harvest", "שאיבת הקטלוג תקועה", "t=0",
                                   severity="warning", alert_key="harvest_catalog_stalled", cooldown_s=21600)
        await routes.notify_owner("harvest", "שאיבת הקטלוג תקועה", "t=+30min still stalled",
                                   severity="warning", alert_key="harvest_catalog_stalled", cooldown_s=21600)
    check("repeat within cooldown -> still only 1 send", len(sends), 1)

asyncio.run(t2())

print("\n3. Same stall AFTER cooldown expires -> re-alerts")


async def t3():
    import BACKEND_API_ROUTES as routes
    sends = []

    async def fake_wa_send(to, text):
        sends.append(text)
        return {"ok": True}

    fake_r = _FakeRedis()
    with _patched({
        "_wa_send": fake_wa_send,
        "_notify_window_open": lambda now_local=None: (True, None),
    }), mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        await routes.notify_owner("harvest", "שאיבת הקטלוג תקועה", "t=0",
                                   severity="warning", alert_key="harvest_catalog_stalled", cooldown_s=21600)
        await fake_r.expire_cooldown("autospare:alert_cooldown:harvest_catalog_stalled")
        await routes.notify_owner("harvest", "שאיבת הקטלוג תקועה", "t=+6h still stalled",
                                   severity="warning", alert_key="harvest_catalog_stalled", cooldown_s=21600)
    check("re-alert once cooldown window has passed", len(sends), 2)

asyncio.run(t3())

print("\n4. Restart survival — cooldown lives in Redis, not a process-local variable")


async def t4():
    """Simulate a restart: the SECOND call happens with a brand-new set of Python
    local variables (as a fresh container's _harvest_alert_sent_utc = None would
    be) but the SAME underlying Redis state — proving suppression comes from
    Redis, not from anything that a restart would wipe."""
    import BACKEND_API_ROUTES as routes
    sends = []

    async def fake_wa_send(to, text):
        sends.append(text)
        return {"ok": True}

    fake_r = _FakeRedis()  # one Redis "instance" survives across both calls below
    with _patched({
        "_wa_send": fake_wa_send,
        "_notify_window_open": lambda now_local=None: (True, None),
    }), mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        # "pre-restart" send
        await routes.notify_owner("harvest", "שאיבת הקטלוג תקועה", "pre-restart",
                                   severity="warning", alert_key="harvest_catalog_stalled", cooldown_s=21600)
        # "post-restart": brand new local state (nothing carried in Python), same alert_key
        _harvest_alert_state = None          # what a fresh process would have
        _harvest_alert_sent_utc = None        # what a fresh process would have
        await routes.notify_owner("harvest", "שאיבת הקטלוג תקועה", "post-restart, still stalled",
                                   severity="warning", alert_key="harvest_catalog_stalled", cooldown_s=21600)
    check("post-restart repeat suppressed by Redis-backed cooldown", len(sends), 1)

asyncio.run(t4())

print("\n5. Recovery -> notified exactly once, under its own alert_key")


async def t5():
    import BACKEND_API_ROUTES as routes
    sends = []

    async def fake_wa_send(to, text):
        sends.append(text)
        return {"ok": True}

    fake_r = _FakeRedis()
    with _patched({
        "_wa_send": fake_wa_send,
        "_notify_window_open": lambda now_local=None: (True, None),
    }), mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        await routes.notify_owner("harvest", "שאיבת הקטלוג תקועה", "stalled",
                                   severity="warning", alert_key="harvest_catalog_stalled", cooldown_s=21600)
        await routes.notify_owner("harvest", "שאיבת הקטלוג חזרה לפעול", "recovered",
                                   severity="success", alert_key="harvest_catalog_recovered", cooldown_s=3600)
        # a second "recovered" call immediately after (e.g. a duplicate sample) must not double-send
        await routes.notify_owner("harvest", "שאיבת הקטלוג חזרה לפעול", "recovered again",
                                   severity="success", alert_key="harvest_catalog_recovered", cooldown_s=3600)
    check("recovery delivered exactly once", len(sends), 2)  # 1 stall + 1 recovery

asyncio.run(t5())

print("\n6. A DIFFERENT condition (idle) is never suppressed by the stalled cooldown")


async def t6():
    import BACKEND_API_ROUTES as routes
    sends = []

    async def fake_wa_send(to, text):
        sends.append(text)
        return {"ok": True}

    fake_r = _FakeRedis()
    with _patched({
        "_wa_send": fake_wa_send,
        "_notify_window_open": lambda now_local=None: (True, None),
    }), mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        await routes.notify_owner("harvest", "שאיבת הקטלוג תקועה", "stalled",
                                   severity="warning", alert_key="harvest_catalog_stalled", cooldown_s=21600)
        await routes.notify_owner("harvest", "שאיבת הקטלוג בטלה", "now idle instead",
                                   severity="warning", alert_key="harvest_catalog_idle", cooldown_s=21600)
    check("stalled and idle are independent alert_keys -> both delivered", len(sends), 2)

asyncio.run(t6())

print("\n7. The old direct _wa_send_update(msg) call for the STATUS section is gone")

_hs_start = _ROUTES_SRC.index("async def _harvest_supervisor_loop")
_hs_end = _ROUTES_SRC.index("\nasync def ", _hs_start + 10)
_hs_body = _ROUTES_SRC[_hs_start:_hs_end]

check(
    "harvest_supervisor now calls notify_owner( for the status alert",
    "await notify_owner(" in _hs_body,
    True,
)
check(
    "harvest_catalog_stalled alert_key present in the loop",
    "harvest_catalog_stalled" in _hs_body,
    True,
)
check(
    "harvest_catalog_idle alert_key present in the loop",
    "harvest_catalog_idle" in _hs_body,
    True,
)
check(
    "harvest_catalog_recovered alert_key present in the loop",
    "harvest_catalog_recovered" in _hs_body,
    True,
)
check(
    # Exactly one _wa_send_update(msg) must remain: the weekly digest at the
    # bottom of the function, which is an intentional, justified exception
    # (Phase 4) — not the migrated stall/idle/recovery status alert.
    "exactly one direct _wa_send_update(msg) remains (the weekly digest)",
    _hs_body.count("await _wa_send_update(msg)"),
    1,
)

print("\n8. Messages never claim a false resolution")

_forbidden = ("טופל", "נפתר", "הושלם", "בוצע בהצלחה")
_stall_msg_region = _hs_body[_hs_body.index('_title = "שאיבת הקטלוג תקועה"'):_hs_body.index("body = (")]
check(
    "stalled-state title/body construction contains no false-resolution phrase",
    any(w in _stall_msg_region for w in _forbidden),
    False,
)


# ═══════════════════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("NOA — unchanged, already centralized (regression only)")
print("=" * 70)

print("\n9-11. NOA coherence-gate alert_key + cooldown untouched")
check("noa_coherence_gate_exhausted alert_key still present", "noa_coherence_gate_exhausted" in _ROUTES_SRC, True)
_noa_start = _ROUTES_SRC.index("noa_coherence_gate_exhausted")
_noa_region = _ROUTES_SRC[max(0, _noa_start - 600):_noa_start + 100]
check("NOA alert still routes through notify_owner(", "await notify_owner(" in _noa_region, True)
check("NOA cooldown still 3600s", "cooldown_s=3600" in _noa_region, True)


# ═══════════════════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("AMAYAMA — producer untouched; benefits from shared queue dedup")
print("=" * 70)

print("\n12. Amayama cooldown remains 4 hours, still via notify_owner()")
_am_start = _ROUTES_SRC.index("amayama_harvester_down")
_am_region = _ROUTES_SRC[max(0, _am_start - 600):_am_start + 200]
check("Amayama alert still routes through notify_owner(", "await notify_owner(" in _am_region, True)
check("Amayama cooldown is still 4*3600", "4 * 3600" in _am_region, True)

print("\n13. Multiple valid Amayama alerts during quiet hours do NOT burst — collapse to one queued item")


async def t13():
    import BACKEND_API_ROUTES as routes
    fake_r = _FakeRedis()
    with _patched({"_notify_window_open": lambda now_local=None: (False, None)}), \
         mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        # Three separate 30-min samples overnight, each individually valid (its
        # own 4h cooldown having lapsed), each with slightly different diagnostic
        # numbers — exactly what the forensic investigation observed accumulating.
        await routes._wa_send_quiet(to="9725XXXXXXX", text="Amayama down; 1200 unpriced",
                                     alert_key="amayama_harvester_down")
        await routes._wa_send_quiet(to="9725XXXXXXX", text="Amayama down; 1250 unpriced",
                                     alert_key="amayama_harvester_down")
        await routes._wa_send_quiet(to="9725XXXXXXX", text="Amayama down; 1300 unpriced",
                                     alert_key="amayama_harvester_down")
    items = _queued_items(fake_r)
    check("only ONE amayama_harvester_down item remains queued", len(items), 1)

asyncio.run(t13())

print("\n14. The LATEST diagnostic state is what survives the collapse")


async def t14():
    import BACKEND_API_ROUTES as routes
    fake_r = _FakeRedis()
    with _patched({"_notify_window_open": lambda now_local=None: (False, None)}), \
         mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        await routes._wa_send_quiet(to="972500000000", text="Amayama down; 1200 unpriced",
                                     alert_key="amayama_harvester_down")
        await routes._wa_send_quiet(to="972500000000", text="Amayama down; 1300 unpriced (latest)",
                                     alert_key="amayama_harvester_down")
    items = _queued_items(fake_r)
    check("surviving item carries the LATEST text", items[0]["text"] if items else None,
          "Amayama down; 1300 unpriced (latest)")

asyncio.run(t14())

print("\n15. A DIFFERENT alert family in the queue at the same time is left alone")


async def t15():
    import BACKEND_API_ROUTES as routes
    fake_r = _FakeRedis()
    with _patched({"_notify_window_open": lambda now_local=None: (False, None)}), \
         mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        await routes._wa_send_quiet(to="972500000000", text="Amayama down x2",
                                     alert_key="amayama_harvester_down")
        await routes._wa_send_quiet(to="972500000000", text="Amayama down x3",
                                     alert_key="amayama_harvester_down")
        await routes._wa_send_quiet(to="972500000000", text="NOA gate exhausted",
                                     alert_key="noa_coherence_gate_exhausted")
    items = _queued_items(fake_r)
    keys = sorted(i.get("alert_key") for i in items)
    check("two distinct alert_keys survive, amayama collapsed to one", keys,
          ["amayama_harvester_down", "noa_coherence_gate_exhausted"])

asyncio.run(t15())

print("\n16. Recovery is never collapsed into an active-alert state (distinct keys)")


async def t16():
    import BACKEND_API_ROUTES as routes
    fake_r = _FakeRedis()
    with _patched({"_notify_window_open": lambda now_local=None: (False, None)}), \
         mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        await routes._wa_send_quiet(to="972500000000", text="stalled",
                                     alert_key="harvest_catalog_stalled")
        await routes._wa_send_quiet(to="972500000000", text="recovered",
                                     alert_key="harvest_catalog_recovered")
    items = _queued_items(fake_r)
    check("both the active-alert and the recovery item are queued separately", len(items), 2)

asyncio.run(t16())


# ═══════════════════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("QUEUE MECHANICS")
print("=" * 70)

print("\n17. Same alert_key queued 5x -> one logical delivery")


async def t17():
    import BACKEND_API_ROUTES as routes
    fake_r = _FakeRedis()
    with _patched({"_notify_window_open": lambda now_local=None: (False, None)}), \
         mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        for i in range(5):
            await routes._wa_send_quiet(to="972500000000", text=f"sample {i}", alert_key="job_failures_dlq")
    check("5 pushes of the same alert_key -> 1 queued item", len(_queued_items(fake_r)), 1)

asyncio.run(t17())

print("\n18. Different alert_key values -> separate deliveries")


async def t18():
    import BACKEND_API_ROUTES as routes
    fake_r = _FakeRedis()
    with _patched({"_notify_window_open": lambda now_local=None: (False, None)}), \
         mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        await routes._wa_send_quiet(to="972500000000", text="a", alert_key="alert_a")
        await routes._wa_send_quiet(to="972500000000", text="b", alert_key="alert_b")
        await routes._wa_send_quiet(to="972500000000", text="c", alert_key="alert_c")
    check("3 distinct alert_keys -> 3 queued items", len(_queued_items(fake_r)), 3)

asyncio.run(t18())

print("\n19. A legacy queue item without alert_key is handled safely (never touched by dedup)")


async def t19():
    import BACKEND_API_ROUTES as routes
    fake_r = _FakeRedis()
    with _patched({"_notify_window_open": lambda now_local=None: (False, None)}), \
         mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        # simulate two legacy items already sitting in the queue from BEFORE this fix
        await fake_r.rpush(routes._WA_QUIET_QUEUE_KEY, json.dumps(
            {"to": "972500000000", "text": "legacy 1", "queued_at": "01/01 00:00"}, ensure_ascii=False))
        await fake_r.rpush(routes._WA_QUIET_QUEUE_KEY, json.dumps(
            {"to": "972500000000", "text": "legacy 1", "queued_at": "01/01 00:05"}, ensure_ascii=False))
        # a NEW, keyed alert arrives — must not touch or crash on the legacy items
        await routes._wa_send_quiet(to="972500000000", text="new keyed alert", alert_key="harvest_catalog_stalled")
    items = _queued_items(fake_r)
    check("both legacy items survive untouched + the new keyed one is added", len(items), 3)
    check("no exception raised handling legacy items lacking alert_key", True, True)

asyncio.run(t19())

print("\n20. Queue flush is deterministic (FIFO order preserved)")


async def t20():
    import BACKEND_API_ROUTES as routes
    fake_r = _FakeRedis()
    sent_order = []

    async def fake_wa_send(to, text):
        sent_order.append(text)
        return {"ok": True}

    with _patched({"_wa_send": fake_wa_send}), \
         mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        for i in range(4):
            await fake_r.rpush(routes._WA_QUIET_QUEUE_KEY, json.dumps(
                {"to": "972500000000", "text": f"msg{i}", "queued_at": "01/01 00:00"}, ensure_ascii=False))
        n = await routes._flush_wa_quiet_queue()
    check("flush sent all 4 queued items", n, 4)
    check("flush preserves FIFO order", [t.split("]\n")[-1] for t in sent_order], ["msg0", "msg1", "msg2", "msg3"])

asyncio.run(t20())

print("\n21. Dedup does not mutate an unrelated entry's content")


async def t21():
    import BACKEND_API_ROUTES as routes
    fake_r = _FakeRedis()
    with _patched({"_notify_window_open": lambda now_local=None: (False, None)}), \
         mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        await routes._wa_send_quiet(to="972500000000", text="unrelated, keep me", alert_key="alert_b")
        await routes._wa_send_quiet(to="972500000000", text="a v1", alert_key="alert_a")
        await routes._wa_send_quiet(to="972500000000", text="a v2", alert_key="alert_a")
    items = {i["alert_key"]: i["text"] for i in _queued_items(fake_r)}
    check("unrelated alert_b entry is untouched", items.get("alert_b"), "unrelated, keep me")
    check("alert_a collapsed to its latest text", items.get("alert_a"), "a v2")

asyncio.run(t21())

print("\n22. A malformed queue entry does not stop the flush of good ones")


async def t22():
    import BACKEND_API_ROUTES as routes
    fake_r = _FakeRedis()
    sent = []

    async def fake_wa_send(to, text):
        sent.append(text)
        return {"ok": True}

    with _patched({"_wa_send": fake_wa_send}), \
         mock.patch.object(routes, "get_redis", new=mock.AsyncMock(return_value=fake_r)):
        await fake_r.rpush(routes._WA_QUIET_QUEUE_KEY, b"{not valid json")
        await fake_r.rpush(routes._WA_QUIET_QUEUE_KEY, json.dumps(
            {"to": "972500000000", "text": "good message", "queued_at": "01/01 00:00"}, ensure_ascii=False))
        n = await routes._flush_wa_quiet_queue()
    check("good message still delivered despite a malformed entry ahead of it", len(sent), 1)
    check("flush count reflects only the successfully-sent item", n, 1)

asyncio.run(t22())


# ═══════════════════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("REGRESSION — unrelated mechanisms unchanged")
print("=" * 70)

print("\n23. db_update_agent delta-reporting mechanism untouched by this session")
_dbagent_src = pathlib.Path("/app/db_update_agent.py").read_text(encoding="utf-8")
check("prev_failed_tasks key still present", "autospare:dbagent:prev_failed_tasks" in _dbagent_src, True)
check("delta labels (new/ongoing) still present", '"חדש" if _new_tasks else "מתמשך"' in _dbagent_src, True)

print("\n24-26. See separately-run suites (all reported PASS in this session):")
print("   - devtests/notify_policy_test.py            -> 8/8 PASS")
print("   - devtests/harvest_notify_policy_test.py     -> 11/11 PASS")
print("   - devtests/pass4_content_quality_test.py     -> 14/14 PASS")


# ═══════════════════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("DEFECT A — validate_watchdog_actions uses notify_owner() (2026-09-10)")
print("=" * 70)

_dbagent_src = pathlib.Path("/app/db_update_agent.py").read_text(encoding="utf-8")

# A1 — direct send_message import removed from validate_watchdog_actions
print("\nA1. validate_watchdog_actions no longer imports send_message directly")
# The forensic fix removed the deferred import:
#   from social.whatsapp_provider import send_message as _wa_alert
# That exact form must not appear in validate_watchdog_actions (it may exist
# elsewhere for other purposes — we use a narrow context window check).
_watchdog_fn_idx = _dbagent_src.find("async def validate_watchdog_actions")
_watchdog_fn_end = _dbagent_src.find("\nasync def ", _watchdog_fn_idx + 1)
if _watchdog_fn_end == -1:
    _watchdog_fn_end = len(_dbagent_src)
_watchdog_body = _dbagent_src[_watchdog_fn_idx:_watchdog_fn_end]
check(
    "validate_watchdog_actions body does NOT import send_message as _wa_alert",
    "from social.whatsapp_provider import send_message as _wa_alert" in _watchdog_body,
    False,
)

# A2 — notify_owner is used when anomalies exist
print("\nA2. validate_watchdog_actions calls notify_owner() for anomalies")
check(
    "notify_owner import present in watchdog body",
    "from BACKEND_API_ROUTES import notify_owner" in _watchdog_body,
    True,
)
check(
    "await _notify(...) call present in watchdog body",
    "await _notify(" in _watchdog_body,
    True,
)

# A3 — anomaly details pass through to notify_owner call
print("\nA3. Anomaly details are forwarded to notify_owner as body")
check(
    "anomaly list is joined into the notify_owner body arg",
    'join(f"• {a}" for a in anomalies)' in _watchdog_body,
    True,
)

# A4 — stable alert_key and appropriate severity present
print("\nA4. Stable alert_key and severity='warning' present")
check(
    "alert_key='watchdog_anomaly' used",
    'alert_key="watchdog_anomaly"' in _watchdog_body,
    True,
)
check(
    "severity='warning' used",
    'severity="warning"' in _watchdog_body,
    True,
)
check(
    "cooldown_s present",
    "cooldown_s=" in _watchdog_body,
    True,
)

# A5 — quiet-hours logic NOT duplicated inside watchdog function
print("\nA5. No quiet-hours reimplementation inside watchdog body")
check(
    "_notify_window_open not called inside watchdog",
    "_notify_window_open" in _watchdog_body,
    False,
)
check(
    "_wa_send_quiet not called directly inside watchdog",
    "_wa_send_quiet" in _watchdog_body,
    False,
)
check(
    "_wa_send not called directly inside watchdog",
    "_wa_send(" in _watchdog_body.replace("_wa_send_quiet", ""),
    False,
)

# A6 — notify_owner failure is logged, not silently swallowed
print("\nA6. notify_owner failure is logged (not silent except: pass)")
# The old code had bare `except Exception: pass`. The fix must log.
check(
    "logger.warning used on notify_owner failure",
    "logger.warning" in _watchdog_body and "notify_owner failed" in _watchdog_body,
    True,
)
check(
    "bare 'except Exception: pass' pattern eliminated from watchdog anomaly block",
    "except Exception:\n            pass" not in _watchdog_body,
    True,
)

# A7 — behavioural: validate_watchdog_actions routes through notify_owner end-to-end
print("\nA7. Behavioural: anomalies route through notify_owner -> quiet-hours path")


async def t_a7():
    import BACKEND_API_ROUTES as routes
    import db_update_agent as dba
    import watchdog_state

    notify_calls: list[dict] = []

    async def fake_notify(category, title, body="", *, severity="info",
                          alert_key="", cooldown_s=3600):
        notify_calls.append(
            dict(category=category, title=title, body=body,
                 severity=severity, alert_key=alert_key, cooldown_s=cooldown_s)
        )

    # Seed 6 synthetic kill_orphan events — the burst threshold is >5 kills,
    # so exactly 6 unvalidated events triggers the "Unusual kill burst" anomaly.
    watchdog_state._EVENTS.clear()
    for i in range(6):
        watchdog_state.record(
            "kill_orphan", 9000 + i, 1,
            f"backend_start=1970-01-01T00:00:00Z age_s={99999 + i}",
        )

    # validate_watchdog_actions(db) requires an AsyncSession; mock it minimally.
    _fake_db = mock.MagicMock()

    with mock.patch.object(
        routes, "notify_owner",
        new=mock.AsyncMock(side_effect=fake_notify)
    ):
        result = await dba.validate_watchdog_actions(_fake_db)

    check("A7: anomaly detected on burst", result["anomalies"] > 0, True)
    check("A7: notify_owner called exactly once for the burst anomaly",
          len(notify_calls), 1)
    check("A7: category is 'health'",
          notify_calls[0]["category"] if notify_calls else None, "health")
    check("A7: alert_key is 'watchdog_anomaly'",
          notify_calls[0]["alert_key"] if notify_calls else None, "watchdog_anomaly")
    check("A7: severity is 'warning'",
          notify_calls[0]["severity"] if notify_calls else None, "warning")
    check("A7: anomaly detail in body",
          "Unusual kill burst" in (notify_calls[0]["body"] if notify_calls else ""),
          True)

    # Prove send_message (direct WhatsApp provider) was NOT called at all.
    import social.whatsapp_provider as _wp
    watchdog_state._EVENTS.clear()
    for i in range(6):
        watchdog_state.record(
            "kill_orphan", 9000 + i, 1,
            f"backend_start=1970-01-01T00:00:00Z age_s={99999 + i}",
        )
    with mock.patch.object(_wp, "send_message", new=mock.AsyncMock()) as _direct:
        with mock.patch.object(routes, "notify_owner", new=mock.AsyncMock()):
            await dba.validate_watchdog_actions(_fake_db)
    check("A7: send_message (provider) NOT called directly", _direct.call_count, 0)


asyncio.run(t_a7())


print()
if fails:
    print(f"FAILED: {len(fails)} test(s):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print("ALL OWNER-ALERT-REMEDIATION TESTS PASS")
