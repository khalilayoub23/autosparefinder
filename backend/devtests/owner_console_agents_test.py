"""Owner-console agent behaviour: do NOA / AVI / @עוזר act like agents?

Owner complaint (2026-07-29): "i tried to contact the agents connected to
whatsapp and some of them didn't respond and some got mistaken responses — i
want noa and avi and the helper to do what i ask them and act like agents not
like bots."

A live probe found all three DO reply, so "didn't respond" was not a routing
bug. The real defects were in NOA's answers:

  1. She invented `https://autosparefinder.co.il/oil-filters-corolla` — a route
     that does not exist — and an unnamed "special discount" that does not
     exist. The owner-console kept a PRIVATE, minimal copy of NOA's policy that
     forbade invented prices but said nothing about invented links or
     promotions. (Same class as the repeated "one policy, many private copies"
     entries in docs/POSTMORTEMS.md.)
  2. Told "from now on always put a real price in every post", she replied by
     asking the OWNER for a price. She answered the topic instead of the intent
     — which is precisely what reads as a bot rather than an agent.

Part A tests the deterministic guards (no network). Part B optionally drives the
real LLM path.

Run:  docker exec autospare_backend python3 /app/devtests/owner_console_agents_test.py
      (add --live to also call the LLM)
"""
import asyncio
import re
import sys

sys.path.insert(0, "/app")
from agents.owner_console import (  # noqa: E402
    _clean_wa_reply, _pick_agent, _SAVE_INTENT, _AGENT_TAG, _OWNER_SYSTEM,
)

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}\n         got={got!r}\n        want={want!r}"
          if not ok else f"  PASS  {label}")
    if not ok:
        fails.append(label)


print("A1. routing — every advertised handle reaches its agent")
for msg, want in [
    ("@נועה תכיני פוסט", "social_media_manager_agent"),
    ("@noa make a post", "social_media_manager_agent"),
    ("נועה, תכיני פוסט", "social_media_manager_agent"),
    ("@avi status", "router_agent"),
    ("@אבי מה קורה", "router_agent"),
    ("@עוזר תסביר לי", "assistant_agent"),
    ("@claude explain", "assistant_agent"),
    ("כמה חלקים יש?", "router_agent"),          # bare -> AVI orchestrator
]:
    check(f"route {msg[:22]!r}", _pick_agent(msg)[0], want)

print("\nA2. every routable agent has a system prompt and a tag (a missing branch")
print("    does not error — it silently answers in the WRONG persona)")
for key in {"social_media_manager_agent", "router_agent", "assistant_agent"}:
    check(f"prompt exists {key}", key in _OWNER_SYSTEM, True)
check("AVI tag defaults", _AGENT_TAG.get("router_agent", "AVI"), "AVI")

print("\nA3. INVENTED LINKS are collapsed to the site root")
got = _clean_wa_reply("קנו כאן: https://autosparefinder.co.il/oil-filters-corolla")
check("invented deep path", got, "קנו כאן: https://autosparefinder.co.il")
check("invented path (www)",
      _clean_wa_reply("https://www.autosparefinder.co.il/brakes/toyota"),
      "https://www.autosparefinder.co.il")

print("\nA4. REAL routes must survive (over-zealous stripping would break checkout)")
for url in [
    "https://autosparefinder.co.il/pay/UwX2QDM",
    "https://autosparefinder.co.il/api/v1/go?src=qr_instagram_w30",
    "https://autosparefinder.co.il/admin",
    "https://autosparefinder.co.il",
]:
    check(f"keeps {url[-24:]}", _clean_wa_reply(url), url)

print("\nA5. punctuation after a link is not swallowed")
check("trailing period",
      _clean_wa_reply("ראה https://autosparefinder.co.il/foo-bar."),
      "ראה https://autosparefinder.co.il.")

print("\nA6. DIRECTIVE detection — an instruction must be recognised as one")
for msg, want in [
    ("מעכשיו תמיד תוסיפי מחיר אמיתי לכל פוסט", True),
    ("נועה, תשמרי נוהל: לא לפרסם בלי מחיר", True),
    ("from now on always include the price", True),
    ("תכיני לי פוסט על מסנני שמן", False),
    ("כמה חלקים יש בקטלוג?", False),
]:
    check(f"directive? {msg[:30]!r}", bool(_SAVE_INTENT.search(msg)), want)

print("\nA7. the WhatsApp reply rules actually carry the new prohibitions")
noa = _OWNER_SYSTEM["social_media_manager_agent"]
check("forbids invented links", "אסור להמציא קישורים" in noa, True)
check("forbids invented discounts", "אסור להמציא מבצעים" in noa, True)
check("directive != post", "אל תכתבי פוסט" in noa, True)

print("\nA8. chain-of-thought still stripped (regression guard)")
out = _clean_wa_reply("1. Analyze the Request\n2. Drafting the Post\n"
                      "Final Output: *מסנן שמן לקורולה* — חפשו לפי מספר רישוי.")
check("no CoT leak", bool(re.search(r"Analyze|Drafting|Final Output", out)), False)
check("keeps the answer", "מסנן שמן לקורולה" in out, True)

print("\nA9. campaign command routes to SHIRA's real delegate_to_social_campaign()")
print("    (2026-08-15d merge audit — the owner console's ONLY campaign-creation path,")
print("    previously zero; must not be confused with the deterministic post approve/")
print("    reject commands or accidentally caught by an unrelated pattern)")
import agents.owner_console as _oc  # noqa: E402
_CAMP_RE = re.compile(r"^(campaign|קמפיין)\b\s*(.*)$", re.I | re.S)
for msg, expect_topic in [
    ("קמפיין רפידות בלם לחורף", "רפידות בלם לחורף"),
    ("campaign winter brake pads", "winter brake pads"),
    ("קמפיין", ""),  # no topic given — handler must ask for one, not crash
]:
    m = _CAMP_RE.match(msg)
    check(f"campaign regex matches {msg[:26]!r}", bool(m), True)
    if m:
        check(f"campaign topic parsed from {msg[:26]!r}", m.group(2).strip(), expect_topic)
# must not collide with the existing approve/reject/post patterns
for msg in ["אשר abc123", "approve abc123", "דחה abc123", "פוסטים", "תגובות"]:
    check(f"campaign regex does NOT match {msg!r}", bool(_CAMP_RE.match(msg)), False)
check("_create_campaign_via_shira exists", hasattr(_oc, "_create_campaign_via_shira"), True)
check("help text documents the command", "*קמפיין [נושא]*" in _oc._HELP, True)

if "--live" in sys.argv:
    print("\nB. LIVE — driving the real LLM path")
    from agents.owner_console import process_owner_message as P

    async def live():
        cases = [
            ("directive->ack", "נועה, מעכשיו תמיד תוסיפי מחיר אמיתי לכל פוסט"),
            ("post request", "נועה, תכיני פוסט על מסנני שמן לקורולה"),
            ("avi status", "@avi מה מצב הייבוא?"),
            ("helper", "@עוזר תסביר בקצרה מה זה OEM"),
        ]
        for tag, msg in cases:
            r = await P(msg) or ""
            bad_link = re.search(
                r"autosparefinder\.co\.il/(?!pay/|admin|api/v1/go|search)\S", r)
            promo = re.search(r"הנחה מיוחדת|קופון", r)
            print(f"\n  --- {tag} ---\n  {r[:340]}")
            check(f"live[{tag}] no invented link", bool(bad_link), False)
            check(f"live[{tag}] no invented promo", bool(promo), False)
            check(f"live[{tag}] non-empty", len(r.strip()) > 10, True)

    asyncio.run(live())

print()
if fails:
    print(f"FAILED: {len(fails)} -> {fails}")
    sys.exit(1)
print("ALL PASS")
