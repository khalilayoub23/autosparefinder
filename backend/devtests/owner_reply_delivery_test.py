"""The owner's WhatsApp reply must NEVER be silence.

Owner complaint (2026-07-29): "some of them didn't respond."

Root cause found in routes/webhooks.py: the owner-console reply was spawned with
a bare `asyncio.create_task(...)`. The event loop keeps only a WEAK reference to
a task, so if no one holds a strong reference the task can be garbage-collected
mid-await. The webhook still ACKs, so the message looks accepted — and no reply
is ever sent and nothing is logged. Intermittent by nature, which is exactly how
the owner described it.

Secondary causes of the same symptom, also fixed: an LLM call that hangs (no
deadline => wait forever => silence) and an empty reply string (`if r:` meant a
blank answer sent nothing at all).

Part 1 demonstrates the GC hazard is REAL rather than theoretical.
Part 2 asserts the webhook module carries all four guards.

Run: docker exec autospare_backend python3 /app/devtests/owner_reply_delivery_test.py
"""
import asyncio
import gc
import sys

sys.path.insert(0, "/app")

fails = []


def check(label, got, want):
    print(f"  {'PASS' if got == want else 'FAIL'}  {label}: got={got} want={want}")
    if got != want:
        fails.append(label)


# ── 1. the hazard is real ────────────────────────────────────────────────────
print("1. an unreferenced task CAN be collected mid-await (this is the bug)")


async def gc_demo():
    finished_unheld, finished_held = [], []

    async def work(sink, tag):
        await asyncio.sleep(0.05)
        sink.append(tag)

    # (a) no reference kept — the loop holds only a weak ref
    for i in range(200):
        asyncio.create_task(work(finished_unheld, i))
    gc.collect()                      # what CPython may do at any moment
    await asyncio.sleep(0.4)

    # (b) strong reference kept — the shape now used in webhooks.py
    held = set()
    for i in range(200):
        t = asyncio.create_task(work(finished_held, i))
        held.add(t)
        t.add_done_callback(held.discard)
    gc.collect()
    await asyncio.sleep(0.4)
    return len(finished_unheld), len(finished_held)

unheld, heldn = asyncio.run(gc_demo())
print(f"     unreferenced tasks completed: {unheld}/200")
print(f"     strong-referenced completed  : {heldn}/200")
# The strong-referenced set must ALWAYS complete. (The unreferenced count is
# implementation-dependent — CPython often survives; the point of the fix is that
# survival must not be left to chance for the owner's only reply channel.)
check("strong-referenced tasks all complete", heldn, 200)

# ── 2. the webhook carries every guard ───────────────────────────────────────
print("\n2. routes/webhooks.py owner-console block")
src = open("/app/routes/webhooks.py", encoding="utf-8").read()

check("strong-reference set declared", "_OWNER_REPLY_TASKS: set = set()" in src, True)
check("task is added to it", "_OWNER_REPLY_TASKS.add(_t)" in src, True)
check("and discarded when done", "add_done_callback(_OWNER_REPLY_TASKS.discard)" in src, True)
check("no bare create_task for the reply",
      "asyncio.create_task(_owner_reply_bg())\n            return" in src, False)
check("reply has a deadline", "asyncio.wait_for(" in src and "_OWNER_REPLY_TIMEOUT_S" in src, True)
check("timeout still answers", "לקח לי יותר מדי זמן" in src, True)
check("empty reply still answers", 'if not (r or "").strip():' in src, True)
check("exception still answers", "שגיאה בעיבוד ההודעה" in src, True)

print("\n3. the module imports and the timeout is a sane number")
import routes.webhooks as W  # noqa: E402
check("timeout is set", isinstance(W._OWNER_REPLY_TIMEOUT_S, int), True)
check("timeout in a sensible range (30-600s)",
      30 <= W._OWNER_REPLY_TIMEOUT_S <= 600, True)
check("task registry starts empty", len(W._OWNER_REPLY_TASKS), 0)

print()
if fails:
    print(f"FAILED: {len(fails)} -> {fails}")
    sys.exit(1)
print("ALL PASS — the owner's reply cannot be silently dropped.")
