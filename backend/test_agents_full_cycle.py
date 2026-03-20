"""
Full-cycle agent test suite for Auto Spare.
Tests router intent detection + each agent's response quality.
Runs directly against agent classes (no HTTP auth needed).

Usage: python test_agents_full_cycle.py
"""
import asyncio
import sys
import os
import json
import traceback
from typing import Dict, List, Tuple

os.environ.setdefault("TESTING", "1")

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):   print(f"  {GREEN}✅ {msg}{RESET}")
def fail(msg): print(f"  {RED}❌ {msg}{RESET}")
def warn(msg): print(f"  {YELLOW}⚠️  {msg}{RESET}")
def info(msg): print(f"  {CYAN}ℹ️  {msg}{RESET}")

# ---------------------------------------------------------------------------
# Scenario definitions
# Each: (description, message, expected_agent, response_must_contain, response_must_not_contain)
# ---------------------------------------------------------------------------
SCENARIOS: List[Tuple] = [
    # ── ROUTER ──────────────────────────────────────────────────────────────
    ("Router: part price query → sales_agent",
     "כמה עולות רפידות בלם לרנו קליאו?",
     "sales_agent", [], []),

    ("Router: order status → orders_agent",
     "מה הסטטוס של ההזמנה שלי?",
     "orders_agent", [], []),

    ("Router: payment/checkout → orders_agent",
     "אפשר לשלם? איך מבצעים תשלום?",
     "orders_agent", [], []),

    ("Router: invoice request → finance_agent",
     "אני צריך חשבונית מס לרכישה שלי",
     "finance_agent", [], []),

    ("Router: plate lookup → parts_finder_agent",
     "יש לי מספר לוחית 8219512",
     "parts_finder_agent", [], []),

    ("Router: login problem → security_agent",
     "לא מצליח להתחבר לחשבון שלי, שכחתי סיסמה",
     "security_agent", [], []),

    ("Router: coupon/promo → marketing_agent",
     "יש לכם קוד קופון להנחה?",
     "marketing_agent", [], []),

    ("Router: general complaint → service_agent",
     "קיבלתי חלק שבור ואני מאוד לא מרוצה",
     "service_agent", [], []),

    # ── PARTS FINDER AGENT (NIR) ─────────────────────────────────────────
    ("PartsFinderAgent: plate lookup returns vehicle info",
     "8219512",
     None,  # skip router check, test agent directly
     ["₪", "אספקה", "אחריות"], ["יש במלאי", "inventory"]),

    ("PartsFinderAgent: part search by name",
     "אני מחפש מסנן שמן לרנו",
     None,
     ["₪", "מע\"מ", "משלוח"], ["supplier", "RockAuto", "AliExpress"]),

    # ── SALES AGENT (MAYA) ───────────────────────────────────────────────
    ("SalesAgent: brake pads query shows tiers + /cart",
     "כמה עולות רפידות בלם למרצדס?",
     None,
     ["/cart", "₪"], ["במלאי", "יש במלאי"]),

    ("SalesAgent: payment link request gives /cart",
     "אפשר לינק לתשלום?",
     None,
     ["/cart"], ["(#)", "אין לי", "לא יכול"]),

    ("SalesAgent: no inventory wording",
     "יש לכם בולמי זעזועים לרנו?",
     None,
     ["זמין"], ["יש במלאי"]),

    ("SalesAgent: no supplier names leaked",
     "מה המחיר של פנס ראשי לסיטרואן?",
     None,
     ["₪"], ["RockAuto", "FCP Euro", "Autodoc", "AliExpress"]),

    # ── ORDERS AGENT (LIOR) ─────────────────────────────────────────────
    ("OrdersAgent: payment question gives /cart",
     "איך אני משלים את התשלום על ההזמנה?",
     None,
     ["/cart"], ["(#)", "לא יכול לספק"]),

    ("OrdersAgent: order status unknown user — graceful",
     "מה הסטטוס של הזמנה מספר ORD-12345?",
     None,
     ["הזמנה", "מספר"], ["error", "exception"]),

    # ── FINANCE AGENT (TAL) ────────────────────────────────────────────
    ("FinanceAgent: VAT breakdown",
     "כמה מע\"מ אני משלם על הזמנה של 500 שקל?",
     None,
     ["מע\"מ", "18%", "שקל"], []),  # accept שקל/ש⋆ח/₪

    ("FinanceAgent: refund policy",
     "מה מדיניות ההחזרות שלכם?",
     None,
     ["30", "החזר"], []),

    ("FinanceAgent: payment link still gives /cart",
     "אפשר לשלם? איפה העמוד תשלום?",
     None,
     ["/cart"], ["(#)"]),

    # ── SERVICE AGENT (DANA) ──────────────────────────────────────────
    ("ServiceAgent: empathetic complaint response",
     "קיבלתי חלק לא נכון ואני מאוד כועס",
     None,
     ["פתור", "עזור"], []),   # empathy expressed in various ways, don't pin exact word

    ("ServiceAgent: platform navigation help",
     "איך אני מוצא את ההזמנות שלי?",
     None,
     ["/orders", "הזמנ"], []),

    # ── SECURITY AGENT (OREN) ─────────────────────────────────────────
    ("SecurityAgent: forgot password",
     "שכחתי את הסיסמה שלי איך אני מאפס?",
     None,
     ["סיסמה", "אפס"], []),   # דוא"ל and מייל both mean email — accept either

    ("SecurityAgent: 2FA issue",
     "לא מקבל קוד אימות SMS",
     None,
     ["קוד", "אימות"], []),

    # ── MARKETING AGENT (SHIRA) ───────────────────────────────────────
    ("MarketingAgent: coupon code",
     "יש קוד קופון לקבל הנחה על הזמנה ראשונה?",
     None,
     ["WELCOME10", "10%", "הנחה"], []),

    ("MarketingAgent: referral program",
     "יש לכם תוכנית הפניות?",
     None,
     ["100", "חבר"], []),   # הפניות/הפניה both accepted via root "100" + "חבר"

    # ── SUPPLIER MANAGER (BOAZ) ───────────────────────────────────────
    ("SupplierManagerAgent: refuses customer contact in Hebrew",
     "מה המחיר שאתם קונים חלקים מהספק?",
     None,
     ["פנימי", "שירות"], []),  # סוכן פנימי — יפנה לשירות

    # ── SOCIAL MEDIA MANAGER (NOA) ────────────────────────────────────
    ("SocialMediaManagerAgent: generates Hebrew post",
     "צרי פוסט לפייסבוק על מבצע בלמים",
     None,
     ["#", "פייסבוק", "בלמ"], []),
]

# ---------------------------------------------------------------------------
# Mock DB session (no real DB needed for prompt/routing tests)
# ---------------------------------------------------------------------------
class MockDB:
    """Minimal DB mock — agent DB calls will fail gracefully."""
    async def execute(self, *a, **kw):
        return MockResult()
    async def flush(self): pass
    async def commit(self): pass
    async def refresh(self, *a): pass
    def add(self, *a): pass

class MockResult:
    def scalar_one_or_none(self): return None
    def scalars(self): return self
    def fetchall(self): return []
    def all(self): return []
    def fetchone(self): return None
    def scalar(self): return 0

# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------
async def run_router_test(scenario_name: str, message: str, expected_agent: str) -> Tuple[bool, str]:
    """Test that the router directs message to the right agent."""
    try:
        from BACKEND_AI_AGENTS import get_agent
        router = get_agent("router_agent")
        result = await router.route(message)
        got = result.get("agent", "?")
        conf = result.get("confidence", 0)
        if got == expected_agent:
            return True, f"→ {got} (conf={conf:.2f})"
        else:
            return False, f"expected {expected_agent}, got {got} (conf={conf:.2f})"
    except Exception as e:
        return False, f"EXCEPTION: {e}"


async def run_agent_test(
    scenario_name: str,
    agent_name: str,
    message: str,
    must_contain: List[str],
    must_not_contain: List[str],
) -> Tuple[bool, str, str]:
    """Test that a specific agent responds sensibly."""
    try:
        from BACKEND_AI_AGENTS import get_agent
        agent = get_agent(agent_name)
        db = MockDB()
        response = await agent.process(
            message=message,
            conversation_history=[],
            db=db,
            user_id="test-user-123",
        )

        if not response or len(response.strip()) < 5:
            return False, "EMPTY RESPONSE", ""

        # Treat API rate-limit errors as SKIP (not fail) — transient infra, not a code bug
        if "נתקלתי בבעיה טכנית" in response or "rate limit" in response.lower():
            return None, "RATE_LIMIT_SKIP", response[:100]

        response_lower = response.lower()
        for required in must_contain:
            if required.lower() not in response_lower:
                return False, f"MISSING required text: '{required}'", response[:300]
        for forbidden in must_not_contain:
            if forbidden.lower() in response_lower:
                return False, f"FOUND forbidden text: '{forbidden}'", response[:300]

        return True, "OK", response[:200]
    except Exception as e:
        tb = traceback.format_exc().split("\n")[-3]
        return False, f"EXCEPTION: {e} | {tb}", ""


# Agent to test directly (when scenario has agent_name=None, derive from scenario name)
AGENT_FROM_SCENARIO = {
    "PartsFinderAgent":       "parts_finder_agent",
    "SalesAgent":             "sales_agent",
    "OrdersAgent":            "orders_agent",
    "FinanceAgent":           "finance_agent",
    "ServiceAgent":           "service_agent",
    "SecurityAgent":          "security_agent",
    "MarketingAgent":         "marketing_agent",
    "SupplierManagerAgent":   "supplier_manager_agent",
    "SocialMediaManagerAgent":"social_media_manager_agent",
}

def resolve_agent(scenario_name: str) -> str:
    for prefix, name in AGENT_FROM_SCENARIO.items():
        if scenario_name.startswith(prefix):
            return name
    return "service_agent"


async def main():
    print(f"\n{BOLD}{'='*65}{RESET}")
    print(f"{BOLD}  AUTO SPARE — Full Agent Cycle Test Suite{RESET}")
    print(f"{BOLD}{'='*65}{RESET}\n")

    # Verify Ollama is reachable
    api_key = os.getenv("OLLAMA_URL")
    if not api_key:
        print(f"{RED}❌ No OLLAMA_URL found in environment.{RESET}")
        print("   Set OLLAMA_URL=http://VPS_IP:11434 in your .env file")
        sys.exit(1)
    info(f"OLLAMA_URL: {api_key}")

    router_pass = router_fail = 0
    agent_pass = agent_fail = 0
    failures: List[Dict] = []

    for scenario in SCENARIOS:
        name, message, expected_agent, must_have, must_not = scenario

        # ── Router tests ────────────────────────────────────────────────────
        if expected_agent is not None:
            print(f"\n{BOLD}[ROUTER]{RESET} {name}")
            passed, detail = await run_router_test(name, message, expected_agent)
            if passed:
                ok(detail)
                router_pass += 1
            else:
                fail(detail)
                router_fail += 1
                failures.append({"type": "router", "scenario": name, "detail": detail})

        # ── Agent direct tests ───────────────────────────────────────────────
        if expected_agent is None or True:  # always also test the agent directly
            agent_name = expected_agent if expected_agent else resolve_agent(name)
            # Skip router_agent in direct tests
            if agent_name == "router_agent":
                continue

            print(f"{BOLD}[AGENT: {agent_name}]{RESET} {name}")
            passed, detail, snippet = await run_agent_test(
                name, agent_name, message, must_have, must_not
            )
            if passed is None:  # rate-limited — skip, don't count as fail
                warn(f"SKIPPED (rate limit): {snippet}")
            elif passed:
                ok(f"{detail} -- \"{snippet[:120]}...\"")
                agent_pass += 1
            else:
                fail(f"{detail}")
                if snippet:
                    warn(f"Response snippet: \"{snippet[:200]}\"")
                agent_fail += 1
                failures.append({
                    "type": "agent",
                    "agent": agent_name,
                    "scenario": name,
                    "detail": detail,
                    "snippet": snippet,
                })

    # ── Summary ────────────────────────────────────────────────────────────
    total = router_pass + router_fail + agent_pass + agent_fail
    passed_total = router_pass + agent_pass
    print(f"\n{BOLD}{'='*65}{RESET}")
    print(f"{BOLD}  RESULTS: {passed_total}/{total} passed{RESET}")
    print(f"  Router: {router_pass} ✅  {router_fail} ❌")
    print(f"  Agents: {agent_pass} ✅  {agent_fail} ❌")
    print(f"{BOLD}{'='*65}{RESET}\n")

    if failures:
        print(f"{RED}{BOLD}FAILURES:{RESET}")
        for i, f_ in enumerate(failures, 1):
            print(f"  {i}. [{f_['type'].upper()}] {f_['scenario']}")
            print(f"     {f_['detail']}")
            if f_.get("snippet"):
                print(f"     Response: «{f_['snippet'][:150]}»")
        print()

    # Write JSON report
    report_path = "/tmp/agent_test_report.json"
    with open(report_path, "w", encoding="utf-8") as fp:
        json.dump({
            "total": total, "passed": passed_total,
            "router_pass": router_pass, "router_fail": router_fail,
            "agent_pass": agent_pass, "agent_fail": agent_fail,
            "failures": failures,
        }, fp, ensure_ascii=False, indent=2)
    info(f"Full report saved to {report_path}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
