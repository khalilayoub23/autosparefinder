"""
Phase 5I — Controlled 31-group observational Facebook scan.
READ-ONLY. No writes to DB, Redis, queues, cookies, or Facebook.
Uses existing GroupAgent.scan_groups() with Phase 5H telemetry.
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/app")

from social.facebook_browser.group_agent import GroupAgent

# ── Phase 5G baseline (group_url -> discovery count) ─────────────────────────
PHASE_5G_DISCOVERIES = {
    "https://www.facebook.com/groups/1734670403411595": 0,
    "https://www.facebook.com/groups/500278511749059": 1,
    "https://www.facebook.com/groups/2045632139280757": 0,
    "https://www.facebook.com/groups/onlinejunkyard": 0,
    "https://www.facebook.com/groups/683435885693736": 0,
    "https://www.facebook.com/groups/3092848697605236": 0,
    "https://www.facebook.com/groups/738815752855315": 1,
    "https://www.facebook.com/groups/415289008904788": 2,
    "https://www.facebook.com/groups/221104989701139": 0,
    "https://www.facebook.com/groups/547021220796969": 0,
    "https://www.facebook.com/groups/3259211947503788": 0,
    "https://www.facebook.com/groups/857521891104969": 0,
    "https://www.facebook.com/groups/463271374808740": 0,
    "https://www.facebook.com/groups/1517822041794535": 2,
    "https://www.facebook.com/groups/427707415157511": 1,
    "https://www.facebook.com/groups/pishpeshuk.cars": 0,
    "https://www.facebook.com/groups/byd.israel": 0,
    "https://www.facebook.com/groups/2851888278209565": 0,
    "https://www.facebook.com/groups/658585624935670": 0,
    "https://www.facebook.com/groups/639284953398609": 0,
    "https://www.facebook.com/groups/288648403192523": 1,
    "https://www.facebook.com/groups/548760968496679": 0,
    "https://www.facebook.com/groups/612814396148052": 0,
    "https://www.facebook.com/groups/363922487409243": 0,
    "https://www.facebook.com/groups/1268040767255702": 0,
    "https://www.facebook.com/groups/1993530644119698": 0,
    "https://www.facebook.com/groups/463353552674379": 0,
    "https://www.facebook.com/groups/1444337075948231": 0,
    "https://www.facebook.com/groups/622505987804263": 1,
    "https://www.facebook.com/groups/musahnikim": 1,
}

def _5g_lookup(url: str) -> int:
    """Normalize url for Phase 5G lookup."""
    u = url.rstrip("/")
    return PHASE_5G_DISCOVERIES.get(u, PHASE_5G_DISCOVERIES.get(u + "/", -1))


# ── Exact 30 unique groups (deduplicated from 31 DB rows) ────────────────────
GROUPS = [
    {"id": "1734670403411595", "group_name": "Chevrolet Silverado שברולט סילברדו",            "group_url": "https://www.facebook.com/groups/1734670403411595/"},
    {"id": "500278511749059",  "group_name": "JEEP WK2 Grand Cherokee OWNERS ISRAEL",         "group_url": "https://www.facebook.com/groups/500278511749059/"},
    {"id": "2045632139280757", "group_name": "ONLINE JUNKYARD iL",                            "group_url": "https://www.facebook.com/groups/2045632139280757/"},
    {"id": "onlinejunkyard",   "group_name": "Online Junkyard Israel",                        "group_url": "https://www.facebook.com/groups/onlinejunkyard/"},
    {"id": "683435885693736",  "group_name": "SKODA Premium Friends Club סקודה",              "group_url": "https://www.facebook.com/groups/683435885693736/"},
    {"id": "3092848697605236", "group_name": "אדם קונים מכוניות לפירוק",                     "group_url": "https://www.facebook.com/groups/3092848697605236/"},
    {"id": "738815752855315",  "group_name": "חלפים ואביזרים לרכב",                          "group_url": "https://www.facebook.com/groups/738815752855315/"},
    {"id": "415289008904788",  "group_name": "חלפים לרכב",                                    "group_url": "https://www.facebook.com/groups/415289008904788/"},
    {"id": "221104989701139",  "group_name": "חלפים לרכב מחיר זול",                          "group_url": "https://www.facebook.com/groups/221104989701139/"},
    {"id": "547021220796969",  "group_name": "טיפול ברכב",                                    "group_url": "https://www.facebook.com/groups/547021220796969/"},
    {"id": "3259211947503788", "group_name": "כלי רכב ורכיבים",                               "group_url": "https://www.facebook.com/groups/3259211947503788/"},
    {"id": "857521891104969",  "group_name": "לוח רכבים הגדול במרכז 100k",                   "group_url": "https://www.facebook.com/groups/857521891104969/"},
    {"id": "463271374808740",  "group_name": "מוסכניקים ישראל",                               "group_url": "https://www.facebook.com/groups/463271374808740/"},
    {"id": "1517822041794535", "group_name": "פיג׳ו סיטרואן רנו מכירה קנייה והחלפת חלקים",  "group_url": "https://www.facebook.com/groups/1517822041794535/"},
    {"id": "427707415157511",  "group_name": "פנסים לכל סוגי הרכבים",                        "group_url": "https://www.facebook.com/groups/427707415157511/"},
    {"id": "pishpeshuk.cars",  "group_name": "פשפשוק - רכב",                                  "group_url": "https://www.facebook.com/groups/pishpeshuk.cars/"},
    {"id": "byd.israel",       "group_name": "קהילת BYD ישראל",                               "group_url": "https://www.facebook.com/groups/byd.israel/"},
    {"id": "2851888278209565", "group_name": "קהילת רכב ישראל",                               "group_url": "https://www.facebook.com/groups/2851888278209565/"},
    {"id": "658585624935670",  "group_name": "קונה רכבים לפירוק 050-480-1297",               "group_url": "https://www.facebook.com/groups/658585624935670/"},
    {"id": "639284953398609",  "group_name": "קנייה ומכירה - סובארו חלקים ורכבים",           "group_url": "https://www.facebook.com/groups/639284953398609/"},
    {"id": "288648403192523",  "group_name": "קנייה מכירה חלפים לרכב",                       "group_url": "https://www.facebook.com/groups/288648403192523/"},
    {"id": "548760968496679",  "group_name": "רכבים חדשים וישנים",                            "group_url": "https://www.facebook.com/groups/548760968496679/"},
    {"id": "612814396148052",  "group_name": "רכבים יד 2",                                    "group_url": "https://www.facebook.com/groups/612814396148052/"},
    {"id": "363922487409243",  "group_name": "רכבים למכירה",                                  "group_url": "https://www.facebook.com/groups/363922487409243/"},
    {"id": "1268040767255702", "group_name": "רכבים למכירה בכל הארץ",                        "group_url": "https://www.facebook.com/groups/1268040767255702/"},
    {"id": "1993530644119698", "group_name": "רכבים למכירה כל הארץ",                         "group_url": "https://www.facebook.com/groups/1993530644119698/"},
    {"id": "463353552674379",  "group_name": "רכבים למכירה פרסום חופשי",                     "group_url": "https://www.facebook.com/groups/463353552674379/"},
    {"id": "1444337075948231", "group_name": "שברולט סוואנה",                                  "group_url": "https://www.facebook.com/groups/1444337075948231/"},
    {"id": "622505987804263",  "group_name": "שיפורים לרכב",                                   "group_url": "https://www.facebook.com/groups/622505987804263/"},
    {"id": "musahnikim",       "group_name": "תשאל מוסכניק",                                   "group_url": "https://www.facebook.com/groups/musahnikim/"},
]


async def run_scan():
    print("=" * 70)
    print("PHASE 5I — 31-GROUP OBSERVATIONAL SCAN")
    print("READ-ONLY. NO DB/REDIS/FACEBOOK WRITES.")
    print(f"Groups: {len(GROUPS)}")
    print("=" * 70)

    start_time = time.time()

    # Pre-scan session check
    cookie_path = "/app/state/fb_browser_session/cookies.json"
    with open(cookie_path) as f:
        cookies_before = json.load(f)
    pre_count = len(cookies_before)
    pre_c_user = any(c.get("name") == "c_user" for c in cookies_before)
    pre_xs = any(c.get("name") == "xs" for c in cookies_before)
    print(f"\nPRE-SCAN SESSION: count={pre_count} c_user={pre_c_user} xs={pre_xs}")

    agent = GroupAgent()
    result = await agent.scan_groups(GROUPS, posts_per_group=10)

    total_elapsed = time.time() - start_time

    # Post-scan session check
    with open(cookie_path) as f:
        cookies_after = json.load(f)
    post_count = len(cookies_after)
    post_c_user = any(c.get("name") == "c_user" for c in cookies_after)
    post_xs = any(c.get("name") == "xs" for c in cookies_after)

    tel = result.get("telemetry", {})
    per_grp = result.get("per_group_telemetry", [])

    print("\n" + "=" * 70)
    print("SCAN COMPLETE")
    print("=" * 70)
    print(f"Groups selected : {result.get('groups_selected')}")
    print(f"Groups attempted: {result.get('groups_attempted')}")
    print(f"Groups fetched  : {result.get('groups_fetched')}")
    print(f"Session failed  : {result.get('session_failed')}")
    print(f"Total elapsed   : {total_elapsed:.1f}s")
    print(f"\nAggregate telemetry:")
    print(f"  dom_candidates : {tel.get('dom_candidates',0)}")
    print(f"  text_valid     : {tel.get('text_valid',0)}")
    print(f"  scored         : {tel.get('scored',0)}")
    print(f"  discoveries    : {tel.get('discoveries',0)}")
    print(f"  near_misses    : {tel.get('near_misses',0)}")
    print(f"  zero_score     : {tel.get('zero_score',0)}")
    sc = tel.get('scored', 0)
    di = tel.get('discoveries', 0)
    zs = tel.get('zero_score', 0)
    if sc > 0:
        print(f"  disc/scored    : {di/sc:.3f} (sampled discovery rate)")
        print(f"  zero/scored    : {zs/sc:.3f} (sampled zero-score rate)")

    print(f"\nPost-scan session: count={post_count} c_user={post_c_user} xs={post_xs}")
    print(f"Session drift   : {post_count - pre_count} cookies")

    print("\n" + "=" * 70)
    print("PER-GROUP DETAIL")
    print("=" * 70)
    print(f"{'#':>2} | {'Group':<45} | 5G | DOM | TV | SC | DI | NM | ZS | Class")
    print("-" * 120)

    for i, g in enumerate(per_grp, 1):
        name = g.get("group_name", "")[:44]
        url = g.get("group_url", "")
        dom = g.get("dom_candidates", 0)
        tv = g.get("text_valid", 0)
        sc_g = g.get("scored", 0)
        di_g = g.get("discoveries", 0)
        nm_g = g.get("near_misses", 0)
        zs_g = g.get("zero_score", 0)
        cls = g.get("classification", "?")[:30]
        inv = "✓" if g.get("invariant_ok", True) else "✗INVAR"
        g5 = _5g_lookup(url)

        # Verify invariant inline
        actual_inv_ok = (di_g + nm_g + zs_g == sc_g)
        if not actual_inv_ok:
            inv = f"✗INVAR({di_g+nm_g+zs_g}≠{sc_g})"

        print(f"{i:2d} | {name:<45} | {g5:>2} | {dom:>3} | {tv:>2} | {sc_g:>2} | {di_g:>2} | {nm_g:>2} | {zs_g:>2} | {cls} {inv}")

        # Print discoveries
        for d in g.get("discovery_details", []):
            kws = d.get("keywords_matched", [])
            score = d.get("score", 0)
            action = d.get("suggested_action", "?")
            print(f"   -> DISC score={score:.3f} action={action} kw={kws}")

    # Forensic classification summary
    print("\n" + "=" * 70)
    print("ZERO-DISCOVERY FORENSIC CLASSIFICATION")
    print("=" * 70)
    class_a = class_b = class_c = class_d = class_e = class_err = class_disc = 0
    for g in per_grp:
        di_g = g.get("discoveries", 0)
        dom = g.get("dom_candidates", 0)
        tv = g.get("text_valid", 0)
        sc_g = g.get("scored", 0)
        zs_g = g.get("zero_score", 0)
        cls = g.get("classification", "")

        if di_g > 0:
            class_disc += 1
        elif "ERROR" in cls:
            class_err += 1
        elif dom == 0:
            class_a += 1
        elif tv == 0:
            class_b += 1
        elif sc_g > 0 and zs_g == sc_g:
            class_c += 1
        elif sc_g > 0 and sc_g < tv:
            class_e += 1
        else:
            class_d += 1

    print(f"  DISCOVERY (di>0)                       : {class_disc}")
    print(f"  Class A: DOM=0 (extraction inconclusive): {class_a}")
    print(f"  Class B: DOM>0, text_valid=0            : {class_b}")
    print(f"  Class C: scored>0, all zero-score       : {class_c}")
    print(f"  Class D: mixed (investigate)            : {class_d}")
    print(f"  Class E: max_posts cap reached           : {class_e}")
    print(f"  ERROR                                   : {class_err}")

    # Save results
    output = {
        "scan_result": result,
        "per_group_5g_comparison": [
            {**g, "5g_discoveries": _5g_lookup(g.get("group_url", ""))}
            for g in per_grp
        ],
        "pre_scan_session": {"count": pre_count, "c_user": pre_c_user, "xs": pre_xs},
        "post_scan_session": {"count": post_count, "c_user": post_c_user, "xs": post_xs},
        "total_elapsed_s": round(total_elapsed, 1),
    }

    out_path = "/app/state/phase5i_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nResults saved to: {out_path}")

    return output


if __name__ == "__main__":
    asyncio.run(run_scan())
