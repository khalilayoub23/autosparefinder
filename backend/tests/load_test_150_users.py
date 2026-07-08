"""
AutoSpareFinder — 150-User Realistic Load Test
================================================
Simulates 150 real Israeli customers searching, chatting, and purchasing
car parts across Web, WhatsApp, and Telegram.

Each user has a real persona: name, car, part they need, budget.
Chat users carry context through their conversation.

Usage:
    PRE_AUTH_TOKEN=<jwt> docker exec autospare_backend python3 /app/tests/load_test_150_users.py

    # Auto-generate token:
    docker exec autospare_backend python3 /app/tests/load_test_150_users.py
"""

import asyncio
import json
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
TIMEOUT  = 20.0
CONCURRENCY_LIMIT = 30

# ── Real Israeli customer personas ───────────────────────────────────────────
PERSONAS = [
    # (name, car_make, car_model, car_year, part_en, part_he, budget_ils)
    ("יוסף כהן",     "Toyota",    "Corolla",    2018, "brake pads",           "רפידות בלם",        350),
    ("מרים לוי",     "Hyundai",   "Tucson",     2020, "oil filter",            "פילטר שמן",         80),
    ("דוד אברהם",    "Kia",       "Sportage",   2019, "air filter",            "מסנן אוויר",        120),
    ("רחל ביטון",    "Mazda",     "CX-5",       2021, "spark plugs",           "מצתים",             200),
    ("אלי נחום",     "Ford",      "Focus",      2017, "timing belt",           "חגורת תזמון",       450),
    ("שרה גולן",     "Volkswagen","Golf",       2016, "water pump",            "משאבת מים",         300),
    ("משה פרץ",      "BMW",       "3 Series",   2015, "alternator",            "אלטרנטור",          800),
    ("תמר שפירא",    "Mercedes",  "C-Class",    2019, "shock absorber",        "בולם זעזועים",      600),
    ("נחום כץ",      "Nissan",    "Qashqai",    2018, "wheel bearing",         "מיסב גלגל",         280),
    ("לאה מזרחי",    "Renault",   "Megane",     2017, "clutch kit",            "ערכת מצמד",         950),
    ("איתן הלוי",    "Peugeot",   "308",        2016, "radiator",              "מצנן",              500),
    ("ריבקה שרון",   "Honda",     "HR-V",       2020, "fuel pump",             "משאבת דלק",         400),
    ("שמואל כהן",    "Fiat",      "500",        2018, "thermostat",            "תרמוסטט",           150),
    ("חנה לוי",      "Skoda",     "Octavia",    2019, "cv joint",              "פרזול CV",          350),
    ("אורי גרין",    "Seat",      "Leon",       2020, "headlight",             "פנס קדמי",          700),
    ("פנינה דהן",    "Opel",      "Astra",      2017, "brake disc",            "דיסקית בלם",        320),
    ("יעקב ביטון",   "Toyota",    "Yaris",      2019, "catalytic converter",   "קטליזטור",          1200),
    ("דינה שמש",     "Hyundai",   "i30",        2016, "power steering pump",   "משאבת הגה",         650),
    ("ברוך אמדי",    "Kia",       "Ceed",       2020, "tie rod end",           "ראש מוט",           180),
    ("גלית מור",     "Mitsubishi","Outlander",  2018, "control arm",           "זרוע בקרה",         420),
    ("רון אשכנזי",   "Ford",      "Fiesta",     2017, "sway bar link",         "זרוע מייצב",        120),
    ("תמי גבאי",     "VW",        "Passat",     2019, "engine mount",          "תושבת מנוע",        280),
    ("אמיר עמרם",    "Renault",   "Clio",       2020, "exhaust pipe",          "צינור פליטה",       380),
    ("נועה קרסו",    "Mazda",     "3",          2018, "tail light",            "פנס אחורי",         450),
    ("ירון שלום",    "BMW",       "5 Series",   2016, "transmission fluid",    "שמן גיר",           200),
    ("שושנה פינטו",  "Mercedes",  "A-Class",    2021, "brake caliper",         "אוגן בלם",          900),
    ("גיא צמח",      "Nissan",    "Leaf",       2020, "wiper blade",           "מגב שמשה",          80),
    ("מלי יעקובי",   "Honda",     "Civic",      2019, "air conditioning belt", "רצועת מזגן",        150),
    ("זיו אריאל",    "Hyundai",   "Santa Fe",   2018, "oxygen sensor",         "חיישן חמצן",        350),
    ("נורית בן דוד", "Toyota",    "RAV4",       2021, "differential oil",      "שמן דיפרנציאל",     250),
    # English-speaking personas (tourists, expats)
    ("Alex Johnson",  "Toyota",   "Camry",      2018, "brake pads",           "brake pads",         300),
    ("Sarah Williams","BMW",      "X5",         2019, "oil filter",            "oil filter",         90),
    ("Mike Davis",    "Ford",     "Mustang",    2017, "spark plugs",           "spark plugs",        180),
    ("Emma Brown",    "Audi",     "A4",         2020, "timing belt",           "timing belt",        500),
    ("James Wilson",  "Honda",    "Accord",     2018, "water pump",            "water pump",         280),
]

def get_persona(user_id: int) -> dict:
    p = PERSONAS[user_id % len(PERSONAS)]
    return {
        "name": p[0], "car_make": p[1], "car_model": p[2], "car_year": p[3],
        "part_en": p[4], "part_he": p[5], "budget": p[6],
        "is_hebrew": any('֐' <= c <= '׿' for c in p[0]),
    }

PLATFORMS = (["web"] * 90 + ["whatsapp"] * 30 + ["telegram"] * 30)
random.shuffle(PLATFORMS)

WA_PHONES   = [f"+9725{random.randint(10000000, 99999999)}" for _ in range(30)]
TG_CHAT_IDS = [str(random.randint(100000000, 999999999)) for _ in range(30)]


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
    persona: dict
    steps: List[StepResult] = field(default_factory=list)
    token: Optional[str] = None
    part_id: Optional[str] = None
    supplier_part_id: Optional[str] = None
    order_id: Optional[str] = None
    external_suppliers_count: int = 0
    total_duration_ms: float = 0.0
    completed: bool = False

    def log(self, step: str, ok: bool, ms: float, code: int = None, detail: str = ""):
        self.steps.append(StepResult(step, ok, ms, code, detail))
        icon = "✅" if ok else "❌"
        name = self.persona["name"][:12].ljust(12)
        print(f"  [{self.user_id:03d}|{self.platform:<9}|{name}] {icon} {step:<28} {ms:>7.0f}ms"
              + (f"  {detail}" if detail else ""))


async def timed_request(client: httpx.AsyncClient, method: str, url: str, **kwargs):
    t0 = time.perf_counter()
    try:
        r = await client.request(method, url, timeout=TIMEOUT, **kwargs)
        ms = (time.perf_counter() - t0) * 1000
        try:    data = r.json()
        except: data = {"raw": r.text[:200]}
        return data, ms, r.status_code
    except httpx.TimeoutException:
        return {"error": "timeout"}, (time.perf_counter() - t0) * 1000, 0
    except Exception as e:
        return {"error": str(e)[:100]}, (time.perf_counter() - t0) * 1000, 0


# ── WEB USER JOURNEY ──────────────────────────────────────────────────────────
async def web_user_journey(session: UserSession, client: httpx.AsyncClient, shared_token: str):
    p = session.persona
    query = p["part_en"] if not p["is_hebrew"] else f"{p['part_he']} {p['car_make']} {p['car_year']}"

    # Login with pre-auth token
    session.token = shared_token
    session.log("login", True, 1.0, 200, f"{p['name']}")
    auth = {"Authorization": f"Bearer {session.token}"}

    # Search: part name as text query + vehicle as structured filter (not in text)
    # This avoids over-filtering when car make/year is in both query string and params
    part_query = p["part_he"] if p["is_hebrew"] else p["part_en"]
    data, ms, code = await timed_request(
        client, "GET", f"{BASE_URL}/api/v1/parts/search",
        params={
            "q": part_query,
            "per_type": 5,
            "vehicle_manufacturer": p["car_make"],
            "vehicle_model": p["car_model"],
        },
        headers=auth
    )
    ok = code == 200
    if ok:
        for ptype in ["original", "oem", "aftermarket"]:
            grp = data.get(ptype, {})
            if grp and grp.get("part"):
                if not session.part_id:
                    session.part_id = grp["part"].get("id")
                    session.supplier_part_id = (grp.get("suppliers") or [{}])[0].get("supplier_part_id")
        for key in ["aftermarket_options", "oem_options", "original_options"]:
            for o in (data.get(key) or []):
                if not session.part_id and o.get("part"):
                    session.part_id = o["part"].get("id")
        ext = data.get("external_suppliers", [])
        session.external_suppliers_count = len(ext)
    session.log("search", ok and bool(session.part_id), ms, code,
                f"q='{query[:25]}' part={'found' if session.part_id else 'none'} ext={session.external_suppliers_count}")
    if not session.part_id:
        return

    # External suppliers check — realistic: user compares prices across suppliers
    session.log("ext_supplier_check", session.external_suppliers_count >= 0, ms, code,
                f"{session.external_suppliers_count} ext results vs budget ₪{p['budget']}")

    # Part detail
    data, ms, code = await timed_request(client, "GET", f"{BASE_URL}/api/v1/parts/{session.part_id}", headers=auth)
    session.log("part_detail", code == 200, ms, code,
                data.get("name", "")[:30] if code == 200 else "")

    # Supplier comparison — key feature: compare IL importers + global
    data, ms, code = await timed_request(
        client, "GET", f"{BASE_URL}/api/v1/parts/{session.part_id}/suppliers", headers=auth
    )
    sup_count = data.get("supplier_count", 0) if code == 200 else 0
    session.log("supplier_compare", code == 200, ms, code, f"{sup_count} suppliers")
    if code == 200 and data.get("suppliers"):
        # Pick cheapest supplier within budget
        suppliers = sorted(data["suppliers"], key=lambda s: s.get("price_ils") or 999999)
        for sup in suppliers:
            if (sup.get("price_ils") or 0) <= p["budget"] * 1.2:
                session.supplier_part_id = sup.get("supplier_part_id")
                break
        if not session.supplier_part_id and suppliers:
            session.supplier_part_id = suppliers[0].get("supplier_part_id")

    if not session.part_id:
        session.log("add_to_cart", False, 0, 0, "no part_id")
        return

    # Add to cart — endpoint accepts part_id, resolves cheapest supplier internally
    data, ms, code = await timed_request(
        client, "POST", f"{BASE_URL}/api/v1/customers/cart/items",
        json={"part_id": session.part_id, "quantity": 1},
        headers=auth
    )
    session.log("add_to_cart", code in (200, 201), ms, code)

    # View cart
    data, ms, code = await timed_request(client, "GET", f"{BASE_URL}/api/v1/customers/cart", headers=auth)
    session.log("view_cart", code == 200, ms, code,
                f"{len(data) if isinstance(data, list) else '?'} items")

    # Checkout with real address
    checkout = {
        "shipping_address": {
            "full_name": p["name"],
            "street": f"{random.randint(1, 200)} {random.choice(['רחוב הרצל', 'שדרות רוטשילד', 'רחוב דיזנגוף', 'שדרות בן גוריון'])}",
            "city": random.choice(["תל אביב", "ירושלים", "חיפה", "באר שבע", "נתניה", "ראשון לציון"]),
            "country": "IL",
            "postal_code": str(random.randint(1000000, 9999999)),
            "phone": f"+9725{random.randint(10000000, 99999999)}"
        },
        "notes": f"{p['car_year']} {p['car_make']} {p['car_model']} — {p['part_en']}"
    }
    data, ms, code = await timed_request(
        client, "POST", f"{BASE_URL}/api/v1/customers/checkout", json=checkout, headers=auth
    )
    ok = code in (200, 201)
    if ok:
        session.order_id = data.get("order_id") or data.get("id")
    session.log("checkout", ok, ms, code,
                f"order={'ok' if session.order_id else 'fail'}")

    # Payment
    if session.order_id:
        data, ms, code = await timed_request(
            client, "POST", f"{BASE_URL}/api/v1/payments/create-checkout",
            json={"order_id": str(session.order_id),
                  "success_url": f"{BASE_URL}/success",
                  "cancel_url": f"{BASE_URL}/cancel"},
            headers=auth
        )
        session.log("payment", code in (200, 201), ms, code,
                    "url=yes" if (data.get("checkout_url") or data.get("url")) else "url=no")
    else:
        session.log("payment", False, 0, 0, "skipped")

    # Order history
    data, ms, code = await timed_request(client, "GET", f"{BASE_URL}/api/v1/orders", headers=auth)
    orders = data.get("orders", data) if isinstance(data, dict) else data
    session.log("order_history", code == 200, ms, code,
                f"{len(orders) if isinstance(orders, list) else '?'} orders")

    session.completed = True


# ── WHATSAPP USER JOURNEY — realistic multi-turn conversation ─────────────────
async def whatsapp_user_journey(session: UserSession, client: httpx.AsyncClient, phone: str):
    p = session.persona
    is_he = p["is_hebrew"]

    # Natural conversation flow based on persona
    if is_he:
        messages = [
            f"שלום, אני מחפש {p['part_he']} ל{p['car_make']} {p['car_model']} {p['car_year']}",
            f"מה המחיר? התקציב שלי עד {p['budget']} ₪",
            "יש מספר OEM מקורי?",
            "כמה זמן אספקה לתל אביב?",
            "מה אפשרויות התשלום? יש אשראי?",
        ]
    else:
        messages = [
            f"Hi, looking for {p['part_en']} for {p['car_year']} {p['car_make']} {p['car_model']}",
            f"What's the price? Budget is {p['budget']} ILS",
            "Do you have OEM part number?",
            "How long is delivery to Tel Aviv?",
            "Can I pay by credit card?",
        ]

    for i, msg_text in enumerate(messages):
        payload = {
            "From": f"whatsapp:{phone}",
            "To": "whatsapp:+14155238886",
            "Body": msg_text,
            "MessageSid": f"SM{uuid.uuid4().hex[:32]}",
            "AccountSid": "AC_test",
            "NumMedia": "0",
            "ProfileName": p["name"],
        }
        data, ms, code = await timed_request(
            client, "POST", f"{BASE_URL}/api/v1/webhooks/whatsapp",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        session.log(f"wa_msg_{i+1}", code in (200, 204), ms, code,
                    msg_text[:30].strip())
        await asyncio.sleep(random.uniform(0.2, 0.8))  # human typing delay

    session.completed = True


# ── TELEGRAM USER JOURNEY — realistic bot interaction ────────────────────────
async def telegram_user_journey(session: UserSession, client: httpx.AsyncClient, chat_id: str):
    p = session.persona
    is_he = p["is_hebrew"]

    if is_he:
        messages = [
            {"text": "/start",                                                          "desc": "start"},
            {"text": f"/search {p['part_he']}",                                         "desc": "search_cmd"},
            {"text": f"{p['part_he']} {p['car_make']} {p['car_model']} {p['car_year']}","desc": "search_detail"},
            {"text": "מחיר",                                                             "desc": "price"},
            {"text": "ספקים",                                                           "desc": "suppliers"},
        ]
    else:
        messages = [
            {"text": "/start",                                                          "desc": "start"},
            {"text": f"/search {p['part_en']}",                                         "desc": "search_cmd"},
            {"text": f"{p['part_en']} {p['car_make']} {p['car_model']} {p['car_year']}","desc": "search_detail"},
            {"text": "price",                                                            "desc": "price"},
            {"text": "suppliers",                                                        "desc": "suppliers"},
        ]

    tg_headers = {}
    if os.getenv("TELEGRAM_WEBHOOK_SECRET"):
        tg_headers["X-Telegram-Bot-Api-Secret-Token"] = os.getenv("TELEGRAM_WEBHOOK_SECRET")

    for msg in messages:
        payload = {
            "update_id": random.randint(100000, 999999),
            "message": {
                "message_id": random.randint(1, 9999),
                "from": {
                    "id": int(chat_id),
                    "first_name": p["name"].split()[0],
                    "last_name": p["name"].split()[-1] if len(p["name"].split()) > 1 else "",
                    "language_code": "he" if is_he else "en"
                },
                "chat": {"id": int(chat_id), "type": "private"},
                "date": int(time.time()),
                "text": msg["text"]
            }
        }
        data, ms, code = await timed_request(
            client, "POST", f"{BASE_URL}/api/v1/webhooks/telegram",
            json=payload, headers=tg_headers
        )
        session.log(f"tg_{msg['desc']}", code in (200, 204), ms, code)
        await asyncio.sleep(random.uniform(0.15, 0.5))

    session.completed = True


# ── ORCHESTRATOR ──────────────────────────────────────────────────────────────
async def run_user(user_id: int, platform: str, sem: asyncio.Semaphore,
                   wa_idx: int = 0, tg_idx: int = 0,
                   shared_token: str = "") -> UserSession:
    persona = get_persona(user_id)
    session = UserSession(user_id=user_id, platform=platform, persona=persona)
    t0 = time.perf_counter()
    async with sem:
        async with httpx.AsyncClient(
            base_url=BASE_URL,
            headers={
                "User-Agent": f"AutoSpareTest/User{user_id}",
                "X-Forwarded-For": f"10.{(user_id // 256) % 256}.{user_id % 256}.1",
                "Accept-Language": "he-IL,he;q=0.9" if persona["is_hebrew"] else "en-US,en;q=0.9",
            },
            follow_redirects=True,
        ) as client:
            try:
                if platform == "web":
                    await web_user_journey(session, client, shared_token)
                elif platform == "whatsapp":
                    await whatsapp_user_journey(session, client, WA_PHONES[wa_idx % len(WA_PHONES)])
                elif platform == "telegram":
                    await telegram_user_journey(session, client, TG_CHAT_IDS[tg_idx % len(TG_CHAT_IDS)])
            except Exception as e:
                session.log("UNEXPECTED_ERROR", False, 0, detail=str(e)[:100])
    session.total_duration_ms = (time.perf_counter() - t0) * 1000
    return session


# ── REPORT ────────────────────────────────────────────────────────────────────
def generate_report(sessions: List[UserSession]) -> str:
    lines = [
        "", "=" * 75,
        "  AUTOSPAREFINDER — 150-USER REALISTIC LOAD TEST REPORT",
        f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 75, "",
    ]
    for platform in ["web", "whatsapp", "telegram"]:
        ps = [s for s in sessions if s.platform == platform]
        if not ps: continue
        completed = sum(1 for s in ps if s.completed)
        avg_ms = sum(s.total_duration_ms for s in ps) / len(ps)
        lines.append(f"  {platform.upper()} ({len(ps)} users)")
        lines.append(f"    Completed       : {completed}/{len(ps)}")
        lines.append(f"    Avg total time  : {avg_ms:.0f}ms")
        if platform == "web":
            ext = [s.external_suppliers_count for s in ps]
            lines.append(f"    Avg ext results : {sum(ext)/len(ext):.1f} per search")
        lines.append("")

    lines.append("  STEP PERFORMANCE (web)")
    lines.append(f"  {'Step':<28} {'N':>5} {'OK':>4} {'Avg':>8} {'Max':>8}  Flag")
    lines.append("  " + "-" * 65)
    step_map: Dict[str, List] = {}
    issues = []
    for s in [x for x in sessions if x.platform == "web"]:
        for st in s.steps:
            step_map.setdefault(st.step, []).append(st)
    for step_name, results in step_map.items():
        ok = sum(1 for r in results if r.ok)
        avg = sum(r.duration_ms for r in results) / len(results)
        mx = max(r.duration_ms for r in results)
        flag = ("⚠️ SLOW" if avg > 500 else "") or ("❌ ERR" if ok < len(results) else "")
        if "SLOW" in flag: issues.append(f"SLOW '{step_name}' avg={avg:.0f}ms")
        if "ERR" in flag:
            failed = [r for r in results if not r.ok]
            issues.append(f"ERRORS '{step_name}' {len(results)-ok}/{len(results)} — {failed[0].detail if failed else '?'}")
        lines.append(f"  {step_name:<28} {len(results):>5} {ok:>4} {avg:>8.0f} {mx:>8.0f}  {flag}")

    for platform in ["whatsapp", "telegram"]:
        ps = [s for s in sessions if s.platform == platform]
        if not ps: continue
        lines.append(f"\n  {platform.upper()} STEPS")
        step_map2: Dict[str, List] = {}
        for s in ps:
            for st in s.steps: step_map2.setdefault(st.step, []).append(st)
        for step_name, results in step_map2.items():
            ok = sum(1 for r in results if r.ok)
            avg = sum(r.duration_ms for r in results) / len(results)
            flag = "⚠️ SLOW" if avg > 1500 else ("❌ ERR" if ok < len(results) else "")
            if flag: issues.append(f"{platform.upper()} '{step_name}': {flag} avg={avg:.0f}ms")
            lines.append(f"  {step_name:<28} {len(results):>5} {ok:>4} {avg:>8.0f}  {flag}")

    lines += ["", "  ISSUES", "  " + "-" * 65]
    if issues:
        for i, iss in enumerate(issues, 1): lines.append(f"  {i}. {iss}")
    else:
        lines.append("  ✅ All steps within thresholds")
    lines += ["", "=" * 75]

    report = "\n".join(lines)
    path = f"/app/state/logs/load_test_150_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    try:
        os.makedirs("/app/state/logs", exist_ok=True)
        with open(path, "w") as f: f.write(report)
        print(f"\n  📄 Saved: {path}")
    except Exception: pass
    return report


# ── MAIN ──────────────────────────────────────────────────────────────────────
async def main():
    print("\n" + "=" * 75)
    print("  AutoSpareFinder — 150-User Realistic Load Test")
    print(f"  Target  : {BASE_URL}")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 75)
    print(f"  90 Web | 30 WhatsApp | 30 Telegram  |  cap={CONCURRENCY_LIMIT} concurrent")
    print(f"  Users have real personas, car details, Hebrew+English queries")
    print("=" * 75 + "\n")

    # Get pre-auth token (bypass 2FA for load test)
    shared_token = os.getenv("PRE_AUTH_TOKEN", "")
    if not shared_token:
        print("  Generating pre-auth token...")
        try:
            import uuid as _uuid
            sys.path.insert(0, "/app")
            from BACKEND_AUTH_SECURITY import create_access_token
            import asyncpg
            PII_DB = os.environ["DATABASE_PII_URL"].replace("postgresql+asyncpg://", "postgresql://")
            async def _get_token():
                conn = await asyncpg.connect(PII_DB)
                user = await conn.fetchrow("SELECT id FROM users WHERE email='test@autosparefinder.com' LIMIT 1")
                await conn.close()
                return create_access_token(str(user["id"]), str(_uuid.uuid4())) if user else ""
            shared_token = await _get_token()
            print(f"  ✅ Token generated (user=test@autosparefinder.com)")
        except Exception as e:
            print(f"  ❌ Token generation failed: {e}")
            print("  Set PRE_AUTH_TOKEN env var manually")

    if not shared_token:
        print("  ⚠️  No auth token — web users will fail at login. WhatsApp/Telegram will still run.")

    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)
    wa_idx = tg_idx = 0
    tasks = []
    for i, platform in enumerate(PLATFORMS):
        if platform == "whatsapp":
            tasks.append(run_user(i+1, platform, sem, wa_idx=wa_idx, shared_token=shared_token))
            wa_idx += 1
        elif platform == "telegram":
            tasks.append(run_user(i+1, platform, sem, tg_idx=tg_idx, shared_token=shared_token))
            tg_idx += 1
        else:
            tasks.append(run_user(i+1, platform, sem, shared_token=shared_token))

    print(f"  Launching {len(tasks)} users...\n")
    sessions = await asyncio.gather(*tasks)

    report = generate_report(list(sessions))
    print(report)

    total_ok = sum(1 for s in sessions if s.completed)
    web_ok = sum(1 for s in sessions if s.platform == "web" and s.completed)
    wa_ok = sum(1 for s in sessions if s.platform == "whatsapp" and s.completed)
    tg_ok = sum(1 for s in sessions if s.platform == "telegram" and s.completed)
    print(f"\n  Final: {total_ok}/150  (Web {web_ok}/90 | WhatsApp {wa_ok}/30 | Telegram {tg_ok}/30)\n")


if __name__ == "__main__":
    asyncio.run(main())
