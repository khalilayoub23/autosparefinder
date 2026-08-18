"""
Direct internal agent test — calls process_user_message without HTTP auth.
Shows agent routing, delegation, response quality and tone.
"""
import asyncio, sys, time, os
sys.path.insert(0, '/app')
os.chdir('/app')

from BACKEND_DATABASE_MODELS import get_pii_db
from BACKEND_AI_AGENTS import process_user_message, get_agent

TESTS = [
    ("1. HE parts search → NIR",  "אני צריך פד בלמים קדמיים לטויוטה קורולה 2019"),
    ("2. AR parts search → NIR",  "أحتاج فلتر زيت لسيارة كيا بيكانتو 2020"),
    ("3. EN parts search → NIR",  "I need brake pads for BMW 3 Series 2021"),
    ("4. Order status → LIOR",    "מה הסטטוס של ההזמנה שלי?"),
    ("5. Payment question → LIOR","אני רוצה לשלם עכשיו"),
    ("6. Complaint → DANA",       "קיבלתי חלק פגום, אני מאוכזב מאוד"),
    ("7. Login issue → OREN",     "אני לא מצליח להתחבר לחשבון שלי"),
    ("8. Promotion → SHIRA",      "יש לכם קופון הנחה?"),
]

FAKE_USER_ID = "00000000-0000-0000-0000-000000000001"

async def run():
    results = []
    async for db in get_pii_db():
        for label, msg in TESTS:
            t0 = time.time()
            try:
                resp = await process_user_message(
                    user_id=FAKE_USER_ID,
                    message=msg,
                    conversation_id=None,
                    db=db,
                    source="whatsapp",
                )
                elapsed = round(time.time()-t0, 1)
                agent = resp.get("agent_name") or resp.get("agent") or "?"
                reply = (resp.get("message") or resp.get("response") or resp.get("content") or "")
                results.append((label, agent, elapsed, reply, None))
            except Exception as e:
                elapsed = round(time.time()-t0, 1)
                results.append((label, "ERROR", elapsed, "", str(e)[:200]))

            print(f"\n{'─'*70}")
            r = results[-1]
            print(f"TEST:  {r[0]}")
            print(f"INPUT: {msg}")
            print(f"AGENT: {r[1]}  ({r[2]}s)")
            if r[4]:
                print(f"ERROR: {r[4]}")
            else:
                print(f"REPLY:\n{r[3][:350]}")
            await asyncio.sleep(0.5)
        break  # only need one db session

    print(f"\n{'='*70}")
    print("DELEGATION SUMMARY:")
    for label, agent, elapsed, reply, err in results:
        ok = "✅" if not err and agent not in ("?","ERROR","service_agent") else "⚠️"
        print(f"  {ok} {label:<40} → {agent:<25} {elapsed}s")

asyncio.run(run())
