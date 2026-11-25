"""Mission 5: a digest preview built from REAL production state, replayed through the repo's digest aggregator on an
in-memory Redis (run in sandbox_backend). Nothing is read from or written to production Redis; nothing is sent.
Real inputs (read-only, 2026-11-25): real_facts.json = DB counts; the 6 items delivered one by one at 09:00 IL come from
autospare:wa_quiet_queue:audit (alert_keys: noa_eod_no_activity, email_handoff_stale, email_da02e210-..., + 3 with no key).
The TEXT of delivered messages is not persisted, so wording below is each producer's own code wording, NOT a copy of what the
owner read; the 3 key-less items cannot be reconstructed and are excluded (stated in the report).
Usage: docker exec -i -w /app sandbox_backend env FACTS=<json> python3 - < digest_real_replay.py"""
import asyncio, json, os, sys
from datetime import datetime
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/tests")
import owner_digest as od
from fake_async_redis import FakeAsyncRedis

F = json.loads(os.environ["FACTS"])
NOW = datetime(2026, 11, 26, 9, 40, tzinfo=od.TZ)


async def main():
    r = FakeAsyncRedis(); st = od.Store(r); st.now = lambda: NOW.timestamp()
    raw = 0
    async def rec(**k):
        nonlocal raw; raw += 1
        return await st.record(**k)
    # the real items delivered individually at 09:00 IL (+ their repeats over the last days: cooldown keys prove daily re-reports)
    for _ in range(3):   # noa_eod_no_activity reports daily with the same reason (real: alert_key seen, fingerprint = reason)
        await rec(category="social", title="NOA — אין פעילות פייסבוק ב-24 השעות האחרונות", body=f"לא זוהו פוסטים.\nממתינים לאישור (כל הפלטפורמות): {F['pending_posts']['n']}", severity="info", alert_key="noa_eod_no_activity", fingerprint="same-reason")
    await rec(category="email", title="מיילים שהועברו לסוכנים ולא טופלו", body="• BOAZ: 14 משימות לא התחילו מעל 24 שעות\nסיבה: מבצע המשימות של BOAZ כבוי (BOAZ_TASKS_ENABLED)", severity="warning", alert_key="email_handoff_stale", state="action", fingerprint="h1", actions=["משימות מייל"])
    await rec(category="email", title="מייל חדש — ספק", body="מאת: ספק (מזהה da02e210)", alert_key="email_da02e210", event_keys=["mail:da02e210"])
    await rec(category="supplier", title="BOAZ צריך החלטה", body="אותו מייל", alert_key="boaz_da02e210", event_keys=["mail:da02e210"])
    await rec(category="email", title="סיכום מיילים יומי", body="• ספק: 1", severity="info", alert_key="email_digest_2026-11-25")
    # standing conditions measured in production now (real counts)
    n = F["pending_posts"]["n"]
    await rec(category="social", title="פוסטים של NOA ממתינים לאישור", body=f"{n} פוסטים ממתינים; הישן ביותר מ-{F['pending_posts']['oldest'][:10]}", severity="warning", alert_key="noa_pending_posts", state="action", actions=["פוסטים"])
    for nm in F["pending_suppliers"]:
        await rec(category="supplier", title=f"ספק חדש ממתין לאישורך: {nm}", body="נמצא על ידי NIR", alert_key=f"sup_{nm}", ref=nm[:3], state="action", actions=[f"אשרספק {nm[:3]}", f"דחהספק {nm[:3]}"])
    for nm in F["pending_credentials"]:
        await rec(category="supplier", title=f"{nm}: ממתין לפרטי גישה", body="נדרש חשבון/טוקן מהבעלים", severity="warning", alert_key=f"cred_{nm}")
    await rec(category="harvest", title="כללי קטגוריה חדשים ממתינים לאישור", body=f"{F['kw_pending_tokens']} מילים מוצעות; הצג עם *מילים*", severity="info", alert_key="kw_pending", state="action", actions=["מילים"])
    pend = await st.pending()
    parts = od.render_digest("PREVIEW", pend, now_local=NOW)
    stats = await st.status()
    print("RAW_RECORD_CALLS", raw, "DISTINCT_EVENTS", len(pend))
    print("STATS", json.dumps(stats.get("stats", stats), ensure_ascii=False, default=str)[:600])
    print("ACTION_ITEMS", sum(1 for e in pend if e.get("actions") or e.get("ref") or e.get("state") == "action"))
    for p in parts: print("-" * 30); print(p)
asyncio.run(main())
