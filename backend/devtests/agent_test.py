import asyncio, httpx, json, uuid, time

BASE = "http://localhost:8000"
TEST_USER = "test@autosparefinder.com"
TEST_PASS = "TestUser2024!"

async def get_token():
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{BASE}/api/v1/auth/login",
            json={"email": TEST_USER, "password": TEST_PASS})
        if r.status_code == 200:
            return r.json().get("access_token")
        print(f"Login failed: {r.status_code} {r.text[:200]}")
        return None

async def chat(token, msg, conv_id=None):
    async with httpx.AsyncClient(timeout=45) as c:
        payload = {"message": msg, "conversation_id": conv_id}
        r = await c.post(f"{BASE}/api/v1/chat/message",
            json=payload,
            headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            d = r.json()
            return d
        return {"error": r.status_code, "detail": r.text[:200]}

async def main():
    token = await get_token()
    if not token:
        print("CANNOT LOGIN")
        return

    tests = [
        ("AVI routing: Hebrew part search", "אני צריך מסנן שמן לטויוטה קורולה 2018"),
        ("AVI routing: Arabic greeting", "مرحبا، أحتاج قطعة غيار"),
        ("AVI routing: order status", "מה הסטטוס של ההזמנה שלי?"),
        ("AVI routing: payment question", "איך אני יכול לשלם?"),
        ("AVI routing: warranty / complaint", "יש לי תלונה על חלק שקיבלתי"),
    ]

    conv_id = None
    results = []
    for label, msg in tests:
        t0 = time.time()
        resp = await chat(token, msg, conv_id)
        elapsed = round(time.time() - t0, 1)
        agent = resp.get("agent_name") or resp.get("agent") or resp.get("current_agent") or "?"
        reply = (resp.get("message") or resp.get("response") or resp.get("content") or str(resp))[:200]
        conv_id = resp.get("conversation_id")
        results.append((label, agent, elapsed, reply))
        print(f"\n{'='*60}")
        print(f"TEST: {label}")
        print(f"INPUT: {msg}")
        print(f"AGENT: {agent} | TIME: {elapsed}s")
        print(f"REPLY: {reply}")
        await asyncio.sleep(1)

    print(f"\n{'='*60}")
    print("SUMMARY:")
    for label, agent, elapsed, reply in results:
        print(f"  {label[:45]:<45} → {agent:<25} ({elapsed}s)")

asyncio.run(main())
