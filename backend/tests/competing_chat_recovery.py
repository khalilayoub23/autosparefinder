"""#103 helper (not a test): stands in for the ENVIRONMENT'S OWN backend — runs the real chat recovery pass
(routes.webhooks.recover_agent_requests, which the backend runs every CHAT_REQ_RECOVERY_INTERVAL_S) on the shared
Redis, every 0.2 s for <seconds>, and prints how many requests it took over. Isolated environments only.

Usage: python3 tests/competing_chat_recovery.py <seconds>
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
assert (os.environ.get("ENVIRONMENT") or "").lower() in ("sandbox", "test"), "REFUSED: not an isolated environment"
assert "sandbox" in os.environ.get("REDIS_URL", ""), "REFUSED: not the sandbox Redis"

import routes.webhooks as wh  # noqa: E402


async def main(seconds):
    end, taken, passes = time.time() + seconds, 0, 0
    print("READY", flush=True)
    while time.time() < end:
        taken += (await wh.recover_agent_requests()).get("taken", 0)
        passes += 1
        await asyncio.sleep(0.2)
    print(f"TAKEN {taken} PASSES {passes}", flush=True)


asyncio.run(main(float(sys.argv[1])))
