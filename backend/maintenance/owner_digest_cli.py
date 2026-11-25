"""
Script:  owner_digest_cli.py
Purpose: Read-only inspection (and explicit replay) of the owner morning-digest store. Never sends a WhatsApp message.
Process: status -> pending count, frozen (undelivered) digest, last checkpoint, dead-list size, counters;
         pending -> the events waiting for the next digest; dead -> events expired after DIGEST_EVENT_MAX_AGE_S;
         preview -> the exact text(s) the next digest would send (does NOT claim or modify anything);
         replay-dead -> moves the dead events back to pending (the only write; needs --yes).
Data Imported/Modified: Redis keys `autospare:digest:*` (replay-dead only).
Data Sources: Redis (REDIS_URL via BACKEND_AUTH_SECURITY.get_redis).
Missing Data Delegation: n/a.
Last Updated: 2026-11-25
Run: docker exec -w /app autospare_backend python3 /app/maintenance/owner_digest_cli.py status
"""
import asyncio
import json
import sys
import time
from datetime import datetime

sys.path.insert(0, "/app")


async def main(argv: list[str]) -> int:
    import owner_digest as od
    from BACKEND_AUTH_SECURITY import get_redis
    cmd = argv[0] if argv else "status"
    store = od.Store(await get_redis())
    if cmd == "status":
        print(json.dumps(await store.status(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "pending":
        for e in await store.pending():
            print(f"{datetime.fromtimestamp(e['first_ts'], od.TZ):%d/%m %H:%M} [{e['sev']}/{e['state']}] {e['id']} x{e.get('repeats', 1)} {e['title']}")
    elif cmd == "dead":
        for raw in await store.r.lrange(od.K_DEAD, 0, 499):
            e = json.loads(od._s(raw))
            print(f"{e.get('dead_reason')} {e['id']} {e['title']}")
    elif cmd == "preview":
        evs = await store.pending()
        for part in od.render_digest("PREVIEW", evs, now_local=datetime.now(od.TZ)) or ["(אין אירועים - לא יישלח דיגסט)"]:
            print(part, "\n" + "-" * 40)
    elif cmd == "replay-dead":
        if "--yes" not in argv:
            print("refusing without --yes")
            return 2
        print("replayed", await store.replay_dead())
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
