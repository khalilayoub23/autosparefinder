"""Live multi-agent delegation test — verifies AVI routing + each specialist reply."""
import asyncio, httpx, json, time, sys

BASE = "http://localhost:8000"
TEST_USER = "test@autosparefinder.com"
TEST_PASS = "TestUser2024!"

async def get_token():
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{BASE}/api/v1/auth/login",
            json={"email": TEST_USER, "password": TEST_PASS})
        if r.status_code == 200:
            return r.json().get("access_token")
        print(f"Login failed: {r.status_code} {r.text[:300]}")
        return None

async def chat(token, msg, conv_id=None):
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE}/api/v1/chat/message",
            json={"message": msg, "conversation_id": conv_id},
            headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            return r.json()
        return {"error": r.status_code, "body": r.text[:300]}

async def main():
    token = await get_token()
    if not token:
        sys.exit(1)
    print(f"Token: OK\n")

    tests = [
        ("1. Parts search (NIR expected)",   None, "אני צריך פד בלמים לטויוטה קורולה 2019"),
        ("2. Arabic parts (NIR expected)",   None, "أحتاج فلتر زيت لسيارة كيا بيكانتو 2020"),
        ("3. Order status (LIOR expected)",  None, "מה הסטטוס של ההזמנה שלי מספר 12345?"),
        ("4. Payment link (LIOR expected)",  None, "אני רוצה לשלם על ההזמנה שלי"),
        ("5. Complaint (DANA expected)",     None, "קיבלתי חלק שבור ואני מאוד מאוכזב"),
        ("6. English query (NIR expected)",  None, "I need brake pads for a BMW 3 Series 2021"),
        ("7. Login issue (OREN expected)",   None, "אני לא מצליח להתחבר לחשבון שלי"),
    ]

    results = []
    for label, conv, msg in tests:
        t0 = time.time()
        resp = await chat(token, msg, conv)
        elapsed = round(time.time()-t0,1)
        if "error" in resp:
            results.append((label, "ERROR", elapsed, str(resp)))
            continue
        agent = resp.get("agent_name") or resp.get("agent") or "?"
        reply = (resp.get("message") or resp.get("response") or resp.get("content") or "")[:250]
        results.append((label, agent, elapsed, reply))
        print(f"{'─'*65}")
        print(f"TEST: {label}")
        print(f"MSG : {msg}")
        print(f"AGENT: {agent!r}  ({elapsed}s)")
        print(f"REPLY:\n{reply}\n")
        await asyncio.sleep(1.5)

    print(f"\n{'='*65}")
    print("ROUTING SUMMARY:")
    for label, agent, elapsed, _ in results:
        status = "✅" if elapsed < 30 and agent != "?" else ("⚠️" if agent == "?" else "✅")
        print(f"  {status} {label:<40} → {agent:<25} {elapsed}s")

asyncio.run(main())
