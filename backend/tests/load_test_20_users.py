"""
AutoSpareFinder — 20-User Concurrent Load Test
================================================
Simulates 20 real users doing a full purchase cycle across 3 platforms:
  - Web (REST API)
  - WhatsApp (webhook simulation)
  - Telegram (webhook simulation)

Each user: search → view part → check suppliers → add to cart → checkout → payment
Progress is logged per-user and a final report shows what needs improvement.

Usage:
    docker exec autospare_backend python3 /app/tests/load_test_20_users.py

    # With custom base URL:
    BASE_URL=https://autosparefinder.co.il docker exec autospare_backend python3 /app/tests/load_test_20_users.py
"""

import asyncio
import json
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
TEST_USER_EMAIL = os.getenv("TEST_USER_EMAIL", "test@autosparefinder.com")
TEST_USER_PASS = os.getenv("TEST_USER_PASS", "TestUser2024!")
TELEGRAM_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
TIMEOUT = 15.0  # seconds per request

# Simulate different user IPs to bypass per-IP rate limiting (realistic — real users have different IPs)
def fake_ip(user_id: int) -> str:
    return f"10.{(user_id // 256) % 256}.{user_id % 256}.1"

# ── Search queries covering different part types & languages ──────────────────
SEARCH_QUERIES = [
    "brake pads",         "oil filter",         "air filter",
    "spark plugs",        "timing belt",        "water pump",
    "alternator",         "starter motor",      "shock absorber",
    "wheel bearing",      "clutch kit",         "radiator",
    "fuel pump",          "thermostat",         "cv joint",
    "פילטר שמן",          "רפידות בלמים",       "מסנן אוויר",
    "גל מנוע",            "משאבת מים",
]

PLATFORMS = ["web", "web", "web", "web", "web",   # 10 web users
             "web", "web", "web", "web", "web",
             "whatsapp", "whatsapp", "whatsapp",    # 5 WhatsApp users
             "whatsapp", "whatsapp",
             "telegram", "telegram", "telegram",    # 5 Telegram users
             "telegram", "telegram"]

# Fake WhatsApp phone numbers for test users
WA_PHONES = [f"+9725{random.randint(10000000,99999999)}" for _ in range(5)]
TG_CHAT_IDS = [str(random.randint(100000000, 999999999)) for _ in range(5)]


# ── Per-user event log ────────────────────────────────────────────────────────
@dataclass
class StepResult:
    step: str
    ok: bool
    duration_ms: float
    status_code: Optional[int] = None
    detail: str = ""


@dataclass
class UserSession:
    user_id: int
    platform: str
    query: str
    steps: List[StepResult] = field(default_factory=list)
    token: Optional[str] = None
    part_id: Optional[str] = None
    supplier_part_id: Optional[str] = None
    cart_item_id: Optional[str] = None
    order_id: Optional[str] = None
    total_duration_ms: float = 0.0
    completed: bool = False

    def log(self, step: str, ok: bool, ms: float, code: int = None, detail: str = ""):
        self.steps.append(StepResult(step, ok, ms, code, detail))
        status = "✅" if ok else "❌"
        print(f"  [User {self.user_id:02d}|{self.platform:<9}] {status} {step:<30} {ms:>7.0f}ms"
              + (f"  {detail}" if detail else ""))


# ── HTTP helpers ──────────────────────────────────────────────────────────────
async def timed_request(client: httpx.AsyncClient, method: str, url: str,
                        **kwargs) -> tuple[Any, float, int]:
    t0 = time.perf_counter()
    try:
        r = await client.request(method, url, timeout=TIMEOUT, **kwargs)
        ms = (time.perf_counter() - t0) * 1000
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text[:200]}
        return data, ms, r.status_code
    except httpx.TimeoutException:
        ms = (time.perf_counter() - t0) * 1000
        return {"error": "timeout"}, ms, 0
    except Exception as e:
        ms = (time.perf_counter() - t0) * 1000
        return {"error": str(e)[:100]}, ms, 0


# ── WEB USER JOURNEY ──────────────────────────────────────────────────────────
async def web_user_journey(session: UserSession, client: httpx.AsyncClient,
                           shared_token: str = None):
    """Full web purchase cycle."""

    # Step 1: Login — use shared pre-auth token if available (avoids 2FA + rate limit)
    if shared_token:
        session.token = shared_token
        session.log("login", True, 1.0, 200, "pre-auth token reused")
    else:
        data, ms, code = await timed_request(
            client, "POST", f"{BASE_URL}/api/v1/auth/login",
            json={"email": TEST_USER_EMAIL, "password": TEST_USER_PASS}
        )
        # Handle 202 = 2FA required (test env may require OTP)
        if code == 202 and data.get("requires_2fa"):
            session.log("login", False, ms, code, "2FA required — use pre-auth token in env")
            return
        ok = code == 200 and "access_token" in data
        session.log("login", ok, ms, code,
                    data.get("detail","") if not ok else "")
        if not ok:
            return
        session.token = data["access_token"]
    auth = {"Authorization": f"Bearer {session.token}"}

    # Step 2: Search for a part
    data, ms, code = await timed_request(
        client, "GET", f"{BASE_URL}/api/v1/parts/search",
        params={"q": session.query, "per_type": 5}, headers=auth
    )
    ok = code == 200
    results_count = 0
    if ok:
        for ptype in ["original", "oem", "aftermarket"]:
            grp = data.get(ptype, {})
            if grp and grp.get("part"):
                results_count += 1
                if not session.part_id:
                    session.part_id = grp["part"].get("id")
                    session.supplier_part_id = (grp.get("suppliers") or [{}])[0].get("supplier_part_id")
        opts = data.get("aftermarket_options", []) + data.get("oem_options", [])
        for o in opts:
            if not session.part_id and o.get("part"):
                session.part_id = o["part"].get("id")
    session.log("search", ok and bool(session.part_id), ms, code,
                f"found part_id={session.part_id[:8] if session.part_id else 'none'}")
    if not session.part_id:
        return

    # Step 3: View part detail
    data, ms, code = await timed_request(
        client, "GET", f"{BASE_URL}/api/v1/parts/{session.part_id}", headers=auth
    )
    session.log("part_detail", code == 200, ms, code)

    # Step 4: Get all suppliers (comparison)
    data, ms, code = await timed_request(
        client, "GET", f"{BASE_URL}/api/v1/parts/{session.part_id}/suppliers",
        headers=auth
    )
    sup_count = data.get("supplier_count", 0) if code == 200 else 0
    session.log("supplier_compare", code == 200, ms, code, f"{sup_count} suppliers")
    if code == 200 and data.get("suppliers"):
        session.supplier_part_id = data["suppliers"][0].get("supplier_part_id")

    if not session.supplier_part_id:
        session.log("add_to_cart", False, 0, 0, "no supplier_part_id available")
        return

    # Step 5: Add to cart
    data, ms, code = await timed_request(
        client, "POST", f"{BASE_URL}/api/v1/customers/cart/items",
        json={"supplier_part_id": session.supplier_part_id, "quantity": 1},
        headers=auth
    )
    ok = code in (200, 201)
    session.log("add_to_cart", ok, ms, code)
    if ok and isinstance(data, list) and data:
        session.cart_item_id = data[0].get("id")
    elif ok and isinstance(data, dict):
        session.cart_item_id = data.get("id")

    # Step 6: View cart
    data, ms, code = await timed_request(
        client, "GET", f"{BASE_URL}/api/v1/customers/cart", headers=auth
    )
    cart_items = len(data) if isinstance(data, list) else 0
    session.log("view_cart", code == 200, ms, code, f"{cart_items} items")

    # Step 7: Checkout
    checkout_payload = {
        "shipping_address": {
            "full_name": f"Test User {session.user_id}",
            "street": "123 Test Street",
            "city": "Tel Aviv",
            "country": "IL",
            "postal_code": "6100001",
            "phone": "+972501234567"
        },
        "notes": f"Load test order - user {session.user_id}"
    }
    data, ms, code = await timed_request(
        client, "POST", f"{BASE_URL}/api/v1/customers/checkout",
        json=checkout_payload, headers=auth
    )
    ok = code in (200, 201)
    if ok:
        session.order_id = data.get("order_id") or data.get("id")
    session.log("checkout", ok, ms, code,
                f"order_id={str(session.order_id)[:8] if session.order_id else 'none'}")

    # Step 8: Create payment session
    if session.order_id:
        data, ms, code = await timed_request(
            client, "POST", f"{BASE_URL}/api/v1/payments/create-checkout",
            json={"order_id": str(session.order_id), "success_url": f"{BASE_URL}/success",
                  "cancel_url": f"{BASE_URL}/cancel"},
            headers=auth
        )
        ok = code in (200, 201)
        checkout_url = data.get("checkout_url") or data.get("url", "")
        session.log("create_payment", ok, ms, code,
                    f"url={'yes' if checkout_url else 'no'}")
    else:
        session.log("create_payment", False, 0, 0, "skipped — no order_id")

    # Step 9: Order history
    data, ms, code = await timed_request(
        client, "GET", f"{BASE_URL}/api/v1/orders", headers=auth
    )
    orders = data.get("orders", data) if isinstance(data, dict) else data
    session.log("order_history", code == 200, ms, code,
                f"{len(orders) if isinstance(orders, list) else '?'} orders")

    session.completed = True


# ── WHATSAPP USER JOURNEY ─────────────────────────────────────────────────────
async def whatsapp_user_journey(session: UserSession, client: httpx.AsyncClient, phone: str):
    """Simulate WhatsApp user searching and ordering via chat."""

    messages = [
        session.query,
        "הראה לי מחירים",     # Show me prices
        "אני רוצה להזמין",     # I want to order
        "מה אפשרויות המשלוח",  # What are shipping options
    ]

    for i, msg_text in enumerate(messages):
        # Simulate Twilio WhatsApp webhook payload
        payload = {
            "From": f"whatsapp:{phone}",
            "To": "whatsapp:+14155238886",
            "Body": msg_text,
            "MessageSid": f"SM{uuid.uuid4().hex[:32]}",
            "AccountSid": "AC_test",
            "NumMedia": "0",
        }
        data, ms, code = await timed_request(
            client, "POST", f"{BASE_URL}/api/v1/webhooks/whatsapp",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        ok = code in (200, 204)
        step = f"wa_msg_{i+1}_{msg_text[:15].strip()}"
        session.log(step, ok, ms, code, f"bot_replied={'yes' if ok else 'no'}")
        await asyncio.sleep(0.3)  # simulate human typing delay

    session.completed = True


# ── TELEGRAM USER JOURNEY ─────────────────────────────────────────────────────
async def telegram_user_journey(session: UserSession, client: httpx.AsyncClient, chat_id: str):
    """Simulate Telegram user searching via bot."""

    messages = [
        {"text": "/start", "desc": "start"},
        {"text": session.query, "desc": "search"},
        {"text": "מחיר", "desc": "price_check"},
        {"text": "פרטים נוספים", "desc": "more_info"},
    ]

    for msg in messages:
        # Simulate Telegram Update payload
        payload = {
            "update_id": random.randint(100000, 999999),
            "message": {
                "message_id": random.randint(1, 9999),
                "from": {
                    "id": int(chat_id),
                    "first_name": f"TestUser{session.user_id}",
                    "language_code": "he"
                },
                "chat": {"id": int(chat_id), "type": "private"},
                "date": int(time.time()),
                "text": msg["text"]
            }
        }
        tg_headers = {}
        if TELEGRAM_SECRET:
            tg_headers["X-Telegram-Bot-Api-Secret-Token"] = TELEGRAM_SECRET
        data, ms, code = await timed_request(
            client, "POST", f"{BASE_URL}/api/v1/webhooks/telegram",
            json=payload, headers=tg_headers
        )
        ok = code in (200, 204)
        session.log(f"tg_{msg['desc']}", ok, ms, code)
        await asyncio.sleep(0.2)

    session.completed = True


# ── ORCHESTRATOR ──────────────────────────────────────────────────────────────
async def run_user(user_id: int, platform: str, query: str,
                   wa_idx: int = 0, tg_idx: int = 0,
                   shared_token: str = None) -> UserSession:
    session = UserSession(user_id=user_id, platform=platform, query=query)
    t0 = time.perf_counter()

    async with httpx.AsyncClient(
        base_url=BASE_URL,
        headers={
            "User-Agent": f"AutoSpareTest/User{user_id}",
            "X-Forwarded-For": fake_ip(user_id),  # simulate distinct user IPs
        },
        follow_redirects=True,
    ) as client:
        try:
            if platform == "web":
                await web_user_journey(session, client, shared_token=shared_token)
            elif platform == "whatsapp":
                phone = WA_PHONES[wa_idx % len(WA_PHONES)]
                await whatsapp_user_journey(session, client, phone)
            elif platform == "telegram":
                chat_id = TG_CHAT_IDS[tg_idx % len(TG_CHAT_IDS)]
                await telegram_user_journey(session, client, chat_id)
        except Exception as e:
            session.log("UNEXPECTED_ERROR", False, 0, detail=str(e)[:100])

    session.total_duration_ms = (time.perf_counter() - t0) * 1000
    return session


# ── REPORT ────────────────────────────────────────────────────────────────────
def generate_report(sessions: List[UserSession]) -> str:
    lines = [
        "",
        "=" * 70,
        "  AUTOSPAREFINDER — 20-USER LOAD TEST REPORT",
        f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 70,
        "",
    ]

    # Per-platform summary
    for platform in ["web", "whatsapp", "telegram"]:
        psessions = [s for s in sessions if s.platform == platform]
        if not psessions:
            continue
        completed = sum(1 for s in psessions if s.completed)
        avg_ms = sum(s.total_duration_ms for s in psessions) / len(psessions)
        lines.append(f"  {platform.upper()} ({len(psessions)} users)")
        lines.append(f"    Completed full cycle: {completed}/{len(psessions)}")
        lines.append(f"    Avg total time: {avg_ms:.0f}ms")
        lines.append("")

    # Step-level performance across all web users
    lines.append("  STEP PERFORMANCE (web users)")
    lines.append(f"  {'Step':<30} {'Calls':>6} {'OK':>4} {'Avg ms':>8} {'Max ms':>8} {'Issues'}")
    lines.append("  " + "-" * 65)

    web_sessions = [s for s in sessions if s.platform == "web"]
    step_map: Dict[str, List[StepResult]] = {}
    for s in web_sessions:
        for step in s.steps:
            step_map.setdefault(step.step, []).append(step)

    issues = []
    for step_name, results in step_map.items():
        ok_count = sum(1 for r in results if r.ok)
        avg = sum(r.duration_ms for r in results) / len(results)
        mx = max(r.duration_ms for r in results)
        fail_rate = (len(results) - ok_count) / len(results) * 100

        flag = ""
        if avg > 500:
            flag = "⚠️ SLOW"
            issues.append(f"SLOW: '{step_name}' avg {avg:.0f}ms — consider caching or index optimization")
        if ok_count < len(results):
            flag = "❌ ERRORS"
            failed = [r for r in results if not r.ok]
            issues.append(f"ERRORS: '{step_name}' failed {len(results)-ok_count}/{len(results)} — "
                         + (failed[0].detail if failed else "unknown error"))
        lines.append(f"  {step_name:<30} {len(results):>6} {ok_count:>4} {avg:>8.0f} {mx:>8.0f}  {flag}")

    lines.append("")

    # WhatsApp / Telegram
    for platform in ["whatsapp", "telegram"]:
        psessions = [s for s in sessions if s.platform == platform]
        if not psessions:
            continue
        lines.append(f"  {platform.upper()} STEP PERFORMANCE")
        step_map2: Dict[str, List[StepResult]] = {}
        for s in psessions:
            for step in s.steps:
                step_map2.setdefault(step.step, []).append(step)
        for step_name, results in step_map2.items():
            ok_count = sum(1 for r in results if r.ok)
            avg = sum(r.duration_ms for r in results) / len(results)
            flag = "⚠️ SLOW" if avg > 1000 else ("❌ ERRORS" if ok_count < len(results) else "")
            if flag:
                issues.append(f"{platform.upper()} — {step_name}: {flag} avg={avg:.0f}ms ok={ok_count}/{len(results)}")
            lines.append(f"  {step_name:<30} {len(results):>6} {ok_count:>4} {avg:>8.0f}  {flag}")
        lines.append("")

    # Improvement recommendations
    lines.append("  IMPROVEMENTS NEEDED")
    lines.append("  " + "-" * 65)
    if issues:
        for i, issue in enumerate(issues, 1):
            lines.append(f"  {i}. {issue}")
    else:
        lines.append("  ✅ All steps passed within acceptable thresholds")

    lines.append("")
    lines.append("=" * 70)

    report = "\n".join(lines)

    # Save to file
    report_path = f"/app/state/logs/load_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    try:
        os.makedirs("/app/state/logs", exist_ok=True)
        with open(report_path, "w") as f:
            f.write(report)
        print(f"\n  📄 Report saved: {report_path}")
    except Exception:
        pass

    return report


# ── MAIN ──────────────────────────────────────────────────────────────────────
async def main():
    print("\n" + "=" * 70)
    print("  AutoSpareFinder — 20-User Concurrent Load Test")
    print(f"  Target: {BASE_URL}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print(f"  Platforms: 10 Web | 5 WhatsApp | 5 Telegram")
    print(f"  Each user: search → view → compare suppliers → cart → checkout → payment")
    print("=" * 70 + "\n")

    # Pre-authenticate once — share token across all web users
    # Avoids 2FA flow and rate-limit issues in concurrent test
    shared_token = None
    print("  Pre-authenticating test user...")
    async with httpx.AsyncClient() as pre_client:
        r = await pre_client.post(
            f"{BASE_URL}/api/v1/auth/login",
            json={"email": TEST_USER_EMAIL, "password": TEST_USER_PASS},
            headers={"X-Forwarded-For": "1.2.3.4"},
            timeout=15.0,
        )
        d = r.json()
        if r.status_code == 200 and "access_token" in d:
            shared_token = d["access_token"]
            print(f"  ✅ Pre-auth OK  token={shared_token[:20]}...")
        elif r.status_code == 202:
            print(f"  ⚠️  2FA required — set PRE_AUTH_TOKEN env var to skip")
            shared_token = os.getenv("PRE_AUTH_TOKEN", "")
        else:
            print(f"  ❌ Pre-auth failed: {r.status_code} {str(d)[:100]}")

    queries = random.sample(SEARCH_QUERIES, len(SEARCH_QUERIES))
    wa_idx = 0
    tg_idx = 0
    tasks = []

    for i, platform in enumerate(PLATFORMS):
        query = queries[i % len(queries)]
        if platform == "whatsapp":
            task = run_user(i + 1, platform, query, wa_idx=wa_idx)
            wa_idx += 1
        elif platform == "telegram":
            task = run_user(i + 1, platform, query, tg_idx=tg_idx)
            tg_idx += 1
        else:
            task = run_user(i + 1, platform, query, shared_token=shared_token)
        tasks.append(task)

    # Run all 20 users concurrently
    sessions = await asyncio.gather(*tasks)

    # Generate and print report
    report = generate_report(list(sessions))
    print(report)

    # Summary
    total_ok = sum(1 for s in sessions if s.completed)
    print(f"\n  Final: {total_ok}/20 users completed full cycle\n")


if __name__ == "__main__":
    asyncio.run(main())
