"""Atomic quiet-hours-queue dedup + safe delivery — regression suite (2026-09-09).

The pre-production safety audit (same date) found two real issues in the first
remediation pass:

  1. The quiet-hours queue's alert_key dedup was LRANGE -> inspect -> LREM ->
     RPUSH: 3-4 separate Redis round trips, each a real `await` suspension point
     under a single event loop running ~39 concurrent background tasks. Two
     concurrent calls with the SAME alert_key could both read the queue before
     either wrote to it, so both would survive — a TOCTOU race, reintroducing
     the exact duplicate-burst bug the dedup exists to prevent.

  2. `_flush_wa_quiet_queue()` did `LPOP` (destructive) BEFORE attempting
     delivery. An exception during `_wa_send()`, or a soft `{"ok": False}`
     result (which was not even inspected), silently discarded an
     already-dequeued message.

This suite proves both fixes against a REAL, ISOLATED, NON-PRODUCTION Redis
instance — never `autospare_redis` (Production's Redis) — because:
  (a) Redis's atomicity guarantee for EVAL only means something when tested
      against a real Redis engine, not a pure-Python stand-in with no internal
      `await` (which is trivially "atomic" for reasons that prove nothing about
      the real system — see the FakeRedis docstring in
      owner_alert_remediation_test.py for why that file's fast unit tests are a
      DIFFERENT, faster, but non-authoritative check);
  (b) proving/disproving a race requires real concurrent network I/O, which
      only a real server can provide.

Setup: `docker run -d --name test_atomic_dedup_redis --network
autosparefinder_internal redis:7-alpine` (ephemeral, own empty dataset, torn
down after this run — see the operator instructions printed at the end of this
file's execution). This container is NOT Production's Redis (`autospare_redis`)
and holds no Production data.

Run: docker exec autospare_backend python3 /app/devtests/queue_atomic_dedup_test.py
"""
import sys
import json
import asyncio
import unittest.mock as mock

sys.path.insert(0, "/app")

import redis.asyncio as aioredis  # noqa: E402

fails: list[str] = []
TEST_REDIS_HOST = "test_atomic_dedup_redis"
TEST_REDIS_PORT = 6379


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"         got : {got!r}")
        print(f"         want: {want!r}")
        fails.append(label)


def _patched(monkeypatches: dict):
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


async def _fresh_client():
    r = aioredis.Redis(host=TEST_REDIS_HOST, port=TEST_REDIS_PORT, decode_responses=True)
    await r.ping()
    return r


async def _queue_items(r, key):
    raw_items = await r.lrange(key, 0, -1)
    return [json.loads(x) for x in raw_items]


async def main() -> None:
    import BACKEND_API_ROUTES as routes

    real_redis = await _fresh_client()
    QK = routes._WA_QUIET_QUEUE_KEY

    async def reset():
        await real_redis.delete(QK)

    def patched_env(window_open=False):
        return _patched({
            "get_redis": (lambda: asyncio.sleep(0, result=real_redis)),
            "_notify_window_open": lambda now_local=None: (window_open, None),
        })

    # ═══════════════════════════════════════════════════════════════════════
    print("=" * 70)
    print("CONCURRENCY — real asyncio.gather() against a real Redis engine")
    print("=" * 70)

    print("\n1. Two SIMULTANEOUS same-key enqueue operations -> exactly one item")
    await reset()
    with patched_env(window_open=False):
        await asyncio.gather(
            routes._wa_send_quiet(to="972500000000", text="v1", alert_key="race_key"),
            routes._wa_send_quiet(to="972500000000", text="v2", alert_key="race_key"),
        )
    items = await _queue_items(real_redis, QK)
    check("exactly one item survives 2 concurrent same-key enqueues", len(items), 1)

    print("\n2. MANY (25) simultaneous same-key enqueue operations -> exactly one item, repeated 20x")
    worst_len = 0
    for trial in range(20):
        await reset()
        with patched_env(window_open=False):
            await asyncio.gather(*[
                routes._wa_send_quiet(to="972500000000", text=f"sample {i}", alert_key="race_key_2")
                for i in range(25)
            ])
        items = await _queue_items(real_redis, QK)
        worst_len = max(worst_len, len(items))
    check("worst-case survivors across 20 trials x 25 concurrent same-key enqueues", worst_len, 1)

    print("\n3. Simultaneous DIFFERENT keys -> all survive")
    await reset()
    with patched_env(window_open=False):
        await asyncio.gather(*[
            routes._wa_send_quiet(to="972500000000", text=f"payload {i}", alert_key=f"distinct_key_{i}")
            for i in range(10)
        ])
    items = await _queue_items(real_redis, QK)
    check("10 concurrent DIFFERENT alert_keys -> all 10 survive", len(items), 10)

    print("\n4. Concurrent ACTIVE + RECOVERY alerts -> both survive (never conflated)")
    await reset()
    with patched_env(window_open=False):
        await asyncio.gather(
            routes._wa_send_quiet(to="972500000000", text="stalled", alert_key="harvest_catalog_stalled"),
            routes._wa_send_quiet(to="972500000000", text="recovered", alert_key="harvest_catalog_recovered"),
        )
    items = await _queue_items(real_redis, QK)
    check("active + recovery both present after concurrent enqueue", sorted(i["alert_key"] for i in items),
          ["harvest_catalog_recovered", "harvest_catalog_stalled"])

    # ═══════════════════════════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("LUA SCRIPT BEHAVIOR — direct, sequential proof of each contract")
    print("=" * 70)

    print("\n5. No-match insertion: fresh key, alert_key given -> item appended")
    await reset()
    with patched_env(window_open=False):
        await routes._wa_send_quiet(to="972500000000", text="first", alert_key="k1")
    items = await _queue_items(real_redis, QK)
    check("fresh insertion with alert_key", [i["text"] for i in items], ["first"])

    print("\n6. Existing-key replacement: second call with same key replaces the first")
    with patched_env(window_open=False):
        await routes._wa_send_quiet(to="972500000000", text="second (latest)", alert_key="k1")
    items = await _queue_items(real_redis, QK)
    check("existing key replaced, one item, latest text", [i["text"] for i in items], ["second (latest)"])

    print("\n7. Multiple EXISTING duplicates (simulating leftover pre-fix data) are ALL cleaned up in one call")
    await reset()
    # Seed 3 raw duplicates directly (as if a race under the OLD code had already
    # produced them) — bypassing _wa_send_quiet entirely.
    for txt in ("dup1", "dup2", "dup3"):
        await real_redis.rpush(QK, json.dumps({"to": "972500000000", "text": txt,
                                                "queued_at": "01/01 00:00", "alert_key": "dup_key"}))
    with patched_env(window_open=False):
        await routes._wa_send_quiet(to="972500000000", text="the one that should survive", alert_key="dup_key")
    items = await _queue_items(real_redis, QK)
    check("all 3 pre-existing duplicates collapsed into the new single item", len(items), 1)
    check("surviving item is the new one", items[0]["text"], "the one that should survive")

    print("\n8. Different keys in the same queue are untouched by a dedup on another key")
    await reset()
    with patched_env(window_open=False):
        await routes._wa_send_quiet(to="972500000000", text="keep me", alert_key="other_key")
        await routes._wa_send_quiet(to="972500000000", text="a v1", alert_key="target_key")
        await routes._wa_send_quiet(to="972500000000", text="a v2", alert_key="target_key")
    items = {i["alert_key"]: i["text"] for i in await _queue_items(real_redis, QK)}
    check("unrelated key untouched", items.get("other_key"), "keep me")
    check("target key collapsed to latest", items.get("target_key"), "a v2")

    print("\n9. Malformed/legacy entries are never matched or corrupted by the script")
    await reset()
    await real_redis.rpush(QK, "{not valid json")
    await real_redis.rpush(QK, json.dumps({"to": "972500000000", "text": "legacy, no alert_key",
                                            "queued_at": "01/01 00:00"}))
    with patched_env(window_open=False):
        await routes._wa_send_quiet(to="972500000000", text="new keyed alert", alert_key="k9")
    raw_items = await real_redis.lrange(QK, 0, -1)
    check("malformed + legacy + new keyed item all present (3 total)", len(raw_items), 3)
    check("malformed entry unchanged", raw_items[0], "{not valid json")

    print("\n10. Queue trimming (LTRIM cap) still applies inside the atomic script")
    await reset()
    with patched_env(window_open=False):
        for i in range(60):
            await routes._wa_send_quiet(to="972500000000", text=f"msg{i}", alert_key="")  # no dedup, just fill
    items = await _queue_items(real_redis, QK)
    check("queue capped at 50 after 60 pushes", len(items), 50)
    check("oldest 10 were trimmed (FIFO), newest retained", items[0]["text"], "msg10")
    check("newest item retained", items[-1]["text"], "msg59")

    # ═══════════════════════════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("DELIVERY SAFETY — _flush_wa_quiet_queue()")
    print("=" * 70)

    print("\n11. Successful send -> item removed exactly once")
    await reset()
    await real_redis.rpush(QK, json.dumps({"to": "972500000000", "text": "ok msg", "queued_at": "01/01 00:00"}))
    sent_log = []

    async def wa_send_ok(to, text):
        sent_log.append(text)
        return {"ok": True}

    with _patched({"get_redis": (lambda: asyncio.sleep(0, result=real_redis)), "_wa_send": wa_send_ok}):
        n = await routes._flush_wa_quiet_queue()
    remaining = await real_redis.llen(QK)
    check("flush reports 1 sent", n, 1)
    check("queue empty after successful delivery", remaining, 0)

    print("\n12. send returns ok=False -> item remains queued (recoverable), NOT counted as sent")
    await reset()
    await real_redis.rpush(QK, json.dumps({"to": "972500000000", "text": "will fail", "queued_at": "01/01 00:00"}))

    async def wa_send_soft_fail(to, text):
        return {"ok": False, "error": "bridge unreachable"}

    with _patched({"get_redis": (lambda: asyncio.sleep(0, result=real_redis)), "_wa_send": wa_send_soft_fail}):
        n = await routes._flush_wa_quiet_queue()
    remaining_items = await _queue_items(real_redis, QK)
    check("ok=False -> 0 counted as sent", n, 0)
    check("item still in queue after soft failure", len(remaining_items), 1)
    check("attempts counter incremented", remaining_items[0].get("attempts"), 1)

    print("\n13. send RAISES an exception -> item remains queued (recoverable), NOT lost")
    await reset()
    await real_redis.rpush(QK, json.dumps({"to": "972500000000", "text": "will raise", "queued_at": "01/01 00:00"}))

    async def wa_send_raises(to, text):
        raise RuntimeError("network timeout")

    with _patched({"get_redis": (lambda: asyncio.sleep(0, result=real_redis)), "_wa_send": wa_send_raises}):
        n = await routes._flush_wa_quiet_queue()
    remaining_items = await _queue_items(real_redis, QK)
    check("exception during send -> 0 counted as sent", n, 0)
    check("item survives an exception during delivery", len(remaining_items), 1)
    check("attempts counter incremented after exception too", remaining_items[0].get("attempts"), 1)

    print("\n14. Retry eventually succeeds -> exactly ONE successful delivery, no duplicate queue entries")
    await reset()
    await real_redis.rpush(QK, json.dumps({"to": "972500000000", "text": "flaky", "queued_at": "01/01 00:00"}))
    attempts_made = {"n": 0}

    async def wa_send_flaky(to, text):
        attempts_made["n"] += 1
        if attempts_made["n"] < 3:
            return {"ok": False, "error": "transient"}
        return {"ok": True}

    with _patched({"get_redis": (lambda: asyncio.sleep(0, result=real_redis)), "_wa_send": wa_send_flaky}):
        # Each flush pass processes the head once and stops on failure (by
        # design — see the function's docstring), so simulate 3 scheduled
        # passes exactly like 3 real 5-min health-monitor cycles would.
        n1 = await routes._flush_wa_quiet_queue()
        n2 = await routes._flush_wa_quiet_queue()
        n3 = await routes._flush_wa_quiet_queue()
    check("pass 1 fails (0 sent)", n1, 0)
    check("pass 2 fails (0 sent)", n2, 0)
    check("pass 3 succeeds (1 sent)", n3, 1)
    check("exactly 3 delivery attempts were made", attempts_made["n"], 3)
    check("queue empty after eventual success — no duplicate entries left behind",
          await real_redis.llen(QK), 0)

    print("\n15. A permanently-failing item is dropped after MAX_ATTEMPTS, never blocking forever")
    await reset()
    await real_redis.rpush(QK, json.dumps({"to": "972500000000", "text": "poison", "queued_at": "01/01 00:00"}))

    async def wa_send_always_fails(to, text):
        return {"ok": False, "error": "permanently broken"}

    with _patched({"get_redis": (lambda: asyncio.sleep(0, result=real_redis)), "_wa_send": wa_send_always_fails}):
        total_sent = 0
        for _ in range(routes._WA_QUIET_MAX_ATTEMPTS + 2):
            total_sent += await routes._flush_wa_quiet_queue()
    check("poison item never counted as sent", total_sent, 0)
    check("poison item eventually dropped (queue empty)", await real_redis.llen(QK), 0)

    print("\n16. Unrelated queued entries remain intact while the head item fails")
    await reset()
    await real_redis.rpush(QK, json.dumps({"to": "972500000000", "text": "head - will fail",
                                            "queued_at": "01/01 00:00"}))
    await real_redis.rpush(QK, json.dumps({"to": "972500000000", "text": "behind it - untouched",
                                            "queued_at": "01/01 00:00"}))
    with _patched({"get_redis": (lambda: asyncio.sleep(0, result=real_redis)), "_wa_send": wa_send_soft_fail}):
        n = await routes._flush_wa_quiet_queue()
    remaining_items = await _queue_items(real_redis, QK)
    check("0 delivered this pass (head is stuck)", n, 0)
    check("both items still present, FIFO order preserved (no reordering, no loss)",
          [i["text"] for i in remaining_items], ["head - will fail", "behind it - untouched"])

    # ═══════════════════════════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("LEGACY COMPATIBILITY")
    print("=" * 70)

    print("\n17. A legacy item (no alert_key) already in the queue is never touched by a new keyed enqueue")
    await reset()
    await real_redis.rpush(QK, json.dumps({"to": "972500000000", "text": "legacy item",
                                            "queued_at": "01/01 00:00"}))
    with patched_env(window_open=False):
        await routes._wa_send_quiet(to="972500000000", text="new keyed", alert_key="k17")
    items = await _queue_items(real_redis, QK)
    check("legacy item + new keyed item both present", len(items), 2)
    check("legacy item content unchanged", items[0]["text"], "legacy item")

    print("\n18. Mixed legacy + keyed queue: keyed dedup still works, legacy entries pile up independently")
    await reset()
    with patched_env(window_open=False):
        await routes._wa_send_quiet(to="972500000000", text="legacy A", alert_key="")
        await routes._wa_send_quiet(to="972500000000", text="legacy B", alert_key="")
        await routes._wa_send_quiet(to="972500000000", text="keyed v1", alert_key="k18")
        await routes._wa_send_quiet(to="972500000000", text="keyed v2", alert_key="k18")
    items = await _queue_items(real_redis, QK)
    texts = [i["text"] for i in items]
    check("both legacy entries present (no dedup applied to them)",
          "legacy A" in texts and "legacy B" in texts, True)
    check("keyed entries collapsed to the latest only", texts.count("keyed v1") + texts.count("keyed v2"), 1)
    check("surviving keyed entry is the latest", "keyed v2" in texts, True)

    # ═══════════════════════════════════════════════════════════════════════
    print()
    print("=" * 70)
    print("REGRESSION — real alert families, real Redis")
    print("=" * 70)

    print("\n19. Catalog stall (harvest_catalog_stalled) still dedups correctly against real Redis")
    await reset()
    with patched_env(window_open=False):
        for i in range(4):
            await routes._wa_send_quiet(to="972500000000", text=f"stall sample {i}",
                                         alert_key="harvest_catalog_stalled")
    items = await _queue_items(real_redis, QK)
    check("4 stall samples collapse to 1 queued item", len(items), 1)

    print("\n20. NOA (noa_coherence_gate_exhausted) still dedups correctly, independent of catalog")
    await reset()
    with patched_env(window_open=False):
        await routes._wa_send_quiet(to="972500000000", text="stall", alert_key="harvest_catalog_stalled")
        await routes._wa_send_quiet(to="972500000000", text="noa gate 1", alert_key="noa_coherence_gate_exhausted")
        await routes._wa_send_quiet(to="972500000000", text="noa gate 2", alert_key="noa_coherence_gate_exhausted")
    items = {i["alert_key"]: i["text"] for i in await _queue_items(real_redis, QK)}
    check("catalog and NOA remain independent", set(items.keys()),
          {"harvest_catalog_stalled", "noa_coherence_gate_exhausted"})
    check("NOA collapsed to its latest sample", items["noa_coherence_gate_exhausted"], "noa gate 2")

    print("\n21. Amayama (amayama_harvester_down) burst-collapse still holds against real Redis")
    await reset()
    with patched_env(window_open=False):
        for i in range(6):
            await routes._wa_send_quiet(to="972500000000", text=f"amayama sample {i}",
                                         alert_key="amayama_harvester_down")
    items = await _queue_items(real_redis, QK)
    check("6 overnight Amayama samples collapse to 1 queued item", len(items), 1)
    check("surviving item is the latest sample", items[0]["text"], "amayama sample 5")

    await real_redis.aclose()

    print()
    if fails:
        print(f"FAILED: {len(fails)} test(s):")
        for f in fails:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL QUEUE ATOMIC-DEDUP + DELIVERY-SAFETY TESTS PASS")
    print()
    print("NOTE: this suite used an EPHEMERAL, isolated Redis container")
    print(f"({TEST_REDIS_HOST}), never Production's Redis (autospare_redis).")
    print(f"Operator cleanup: docker rm -f {TEST_REDIS_HOST}")


asyncio.run(main())
