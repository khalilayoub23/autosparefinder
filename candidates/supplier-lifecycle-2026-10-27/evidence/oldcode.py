# Deployed (0041) Email Agent store operations against whatever schema the database has.
import asyncio, sys
sys.path.insert(0, "/app")
from email_agent.store import PgStore
async def main():
    st = PgStore()
    rows = await st.owner_pending(10); dig = await st.digest_pending(10); op = await st.open_items(10)
    claim = await st.claim("rehearsal-" + str(len(rows) + len(dig)), "t9", 3)
    pending = await st.owner_pending(10)
    by = await st.by_ref(pending[0]["id"][:8]) if pending else []
    if pending:
        await st.mark_owner_notified(pending[0]["id"])
    print(f"OLD CODE OK: owner_pending={len(rows)} digest_pending={len(dig)} open={len(op)} claim={claim} by_ref={len(by)} mark_notified=ok")
asyncio.run(main())
