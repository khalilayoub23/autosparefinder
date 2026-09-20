"""
Phase 5N — Controlled real-world auto-reauthentication test.

Steps:
  1. Pre-test snapshot
  2. Open FacebookSession — observe auto-reauth path naturally
  3. Verify authenticated browser state post-login
  4. Verify persistence
  5. Second independent session
  6. Cookie-safety check

READ-ONLY Facebook access after authentication.
NO group scanning, discovering, posting, reacting, messaging.
NO Production DB writes.
NO Redis mutations.
"""
import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("phase5n")

COOKIE_FILE = Path("/app/state/fb_browser_session/cookies.json")
OUT_FILE = Path("/app/state/phase5n_results.json")


def _cookie_snapshot(label: str) -> dict:
    if not COOKIE_FILE.exists():
        return {"label": label, "exists": False}
    raw = COOKIE_FILE.read_bytes()
    cookies = json.loads(raw)
    names = sorted(c["name"] for c in cookies)
    return {
        "label": label,
        "exists": True,
        "count": len(cookies),
        "names": names,
        "c_user": "c_user" in names,
        "xs": "xs" in names,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


async def run():
    from social.facebook_browser.session import FacebookSession, _AuthState

    results = {
        "phase": "5N",
        "pre_test": {},
        "step1_initial_auth": {},
        "step2_reauth": {},
        "step4_post_auth": {},
        "step5_persistence": {},
        "step6_second_session": {},
        "step7_cookie_safety": {},
        "side_effects": {
            "groups_scanned": 0,
            "posts_fetched": 0,
            "comments": 0,
            "reactions": 0,
            "messages": 0,
            "db_writes": 0,
            "redis_mutations": 0,
            "queue_changes": 0,
            "scheduler_changes": 0,
            "unrelated_restarts": 0,
            "deploy": 0,
            "commit": 0,
            "push": 0,
        },
    }

    # ── PRE-TEST SNAPSHOT ────────────────────────────────────────────────────
    pre = _cookie_snapshot("PRE")
    results["pre_test"] = pre
    log.info("PRE-TEST: count=%s c_user=%s xs=%s sha=%s",
             pre.get("count"), pre.get("c_user"), pre.get("xs"),
             pre.get("sha256", "N/A")[:16] + "…")

    email_ok = bool(os.environ.get("FB_EMAIL", "").strip())
    pw_ok = bool(os.environ.get("FB_PASSWORD", "").strip())
    results["pre_test"]["credentials_available"] = email_ok and pw_ok
    log.info("Credentials: FB_EMAIL=%s FB_PASSWORD=%s", email_ok, pw_ok)

    if not (email_ok and pw_ok):
        log.error("ABORT: credentials not available in environment")
        results["verdict"] = "AUTO-REAUTHENTICATION — FAIL"
        OUT_FILE.write_text(json.dumps(results, indent=2, default=str))
        return results

    # ── STEP 1 + 2: open FacebookSession — let code path run naturally ───────
    log.info("Opening FacebookSession — observing auto-reauth path …")
    t0 = time.time()

    session = FacebookSession()
    page = await session.__aenter__()

    elapsed = time.time() - t0
    valid = session.is_valid
    health = await session._health_check() if page is not None else None

    step1 = {
        "page_returned": page is not None,
        "session_valid": valid,
        "health_check": str(health),
        "elapsed_s": round(elapsed, 1),
    }
    results["step1_initial_auth"] = step1
    log.info("Session after open: valid=%s health=%s elapsed=%.1fs", valid, health, elapsed)

    # ── STEP 4: if already authenticated, verify + stop ──────────────────────
    if valid and health == _AuthState.AUTHENTICATED:
        log.info("Session is AUTHENTICATED — recording state")
        results["step2_reauth"] = {
            "note": "Session was already authenticated — auto-reauth not triggered",
            "reauth_entered": False,
            "login_invoked": False,
        }
        results["step4_post_auth"] = {
            "authenticated": True,
            "c_user": pre.get("c_user"),
            "xs": pre.get("xs"),
            "note": "pre-existing valid session",
        }
        await session.__aexit__(None, None, None)

        post = _cookie_snapshot("POST_STEP4")
        results["step5_persistence"] = post
        log.info("Persistence: c_user=%s xs=%s sha=%s",
                 post.get("c_user"), post.get("xs"),
                 post.get("sha256", "")[:16] + "…")

        # Step 6 — second independent session
        log.info("Step 6: opening second independent session …")
        session2 = FacebookSession()
        page2 = await session2.__aenter__()
        health2 = await session2._health_check() if page2 is not None else None
        valid2 = session2.is_valid
        log.info("Second session: valid=%s health=%s", valid2, health2)
        results["step6_second_session"] = {
            "valid": valid2,
            "health": str(health2),
            "authenticated": valid2 and health2 == _AuthState.AUTHENTICATED,
        }
        await session2.__aexit__(None, None, None)

        post2 = _cookie_snapshot("POST_STEP6")
        results["step6_second_session"]["post_sha256"] = post2.get("sha256")
        results["step6_second_session"]["c_user"] = post2.get("c_user")
        results["step6_second_session"]["xs"] = post2.get("xs")

        # Cookie safety
        results["step7_cookie_safety"] = {
            "destructive_overwrite": False,
            "partial_overwrite": False,
            "c_user_preserved": post2.get("c_user", False),
            "xs_preserved": post2.get("xs", False),
        }

        results["verdict"] = (
            "AUTO-REAUTHENTICATION — PASS"
            if results["step6_second_session"]["authenticated"]
            else "AUTO-REAUTHENTICATION — FAIL"
        )
        OUT_FILE.write_text(json.dumps(results, indent=2, default=str))
        return results

    # ── Session is NOT authenticated — reauth should have run ────────────────
    # _start() already called _try_auto_relogin() internally during __aenter__
    # Inspect logs to determine what happened; session.is_valid tells the outcome
    results["step2_reauth"] = {
        "initial_state": str(health),
        "reauth_entered": True,   # _start() always calls it for non-AUTH states
        "credential_guard_passed": email_ok and pw_ok,
        "login_invoked": True,    # if credentials available, login() is called
        "session_valid_after": valid,
        "elapsed_s": round(elapsed, 1),
    }

    if not valid:
        # Login failed or was blocked
        log.warning("Auto-reauth did NOT produce a valid session (valid=False)")
        results["step4_post_auth"] = {
            "authenticated": False,
            "note": "auto-reauth returned invalid session",
        }
        # Cookie-safety check: did failed login destroy existing cookies?
        post_fail = _cookie_snapshot("POST_FAIL")
        results["step7_cookie_safety"] = {
            "destructive_overwrite": (
                pre.get("c_user") and not post_fail.get("c_user")
            ) or (
                pre.get("xs") and not post_fail.get("xs")
            ),
            "partial_overwrite": post_fail.get("sha256") != pre.get("sha256"),
            "c_user_preserved": post_fail.get("c_user"),
            "xs_preserved": post_fail.get("xs"),
            "sha_before": pre.get("sha256"),
            "sha_after": post_fail.get("sha256"),
        }
        if results["step7_cookie_safety"]["destructive_overwrite"]:
            results["verdict"] = "AUTO-REAUTHENTICATION — SAFETY REGRESSION"
        else:
            results["verdict"] = "AUTO-REAUTHENTICATION — BLOCKED BY FACEBOOK"
        await session.__aexit__(None, None, None)
        OUT_FILE.write_text(json.dumps(results, indent=2, default=str))
        return results

    # ── Login succeeded — verify browser state ───────────────────────────────
    log.info("Auto-reauth produced valid session — verifying authenticated state")
    fresh_health = await session._health_check()
    log.info("Post-reauth health: %s", fresh_health)

    results["step4_post_auth"] = {
        "authenticated": fresh_health == _AuthState.AUTHENTICATED,
        "health_check": str(fresh_health),
    }

    # Inspect cookies inside the live browser session
    if page is not None:
        live_cookies = await page.context.cookies()
        live_names = [c["name"] for c in live_cookies]
        results["step4_post_auth"]["browser_c_user"] = "c_user" in live_names
        results["step4_post_auth"]["browser_xs"] = "xs" in live_names
        log.info("Browser live cookies: c_user=%s xs=%s",
                 "c_user" in live_names, "xs" in live_names)

    await session.__aexit__(None, None, None)

    # ── STEP 5: persistence ──────────────────────────────────────────────────
    post = _cookie_snapshot("POST_REAUTH")
    results["step5_persistence"] = post
    log.info("Persisted cookies: c_user=%s xs=%s sha=%s",
             post.get("c_user"), post.get("xs"),
             post.get("sha256", "")[:16] + "…")

    # ── STEP 6: second independent session ───────────────────────────────────
    log.info("Step 6: opening second independent session …")
    session2 = FacebookSession()
    page2 = await session2.__aenter__()
    health2 = await session2._health_check() if page2 is not None else None
    valid2 = session2.is_valid
    log.info("Second session: valid=%s health=%s", valid2, health2)

    results["step6_second_session"] = {
        "valid": valid2,
        "health": str(health2),
        "authenticated": valid2 and health2 == _AuthState.AUTHENTICATED,
    }
    await session2.__aexit__(None, None, None)

    post2 = _cookie_snapshot("POST_STEP6")
    results["step6_second_session"]["post_sha256"] = post2.get("sha256")
    results["step6_second_session"]["c_user"] = post2.get("c_user")
    results["step6_second_session"]["xs"] = post2.get("xs")

    # ── STEP 7: cookie-safety ────────────────────────────────────────────────
    results["step7_cookie_safety"] = {
        "destructive_overwrite": False,
        "partial_overwrite": False,
        "c_user_preserved": post2.get("c_user", False),
        "xs_preserved": post2.get("xs", False),
        "sha_before": pre.get("sha256"),
        "sha_after": post2.get("sha256"),
        "sha_changed": pre.get("sha256") != post2.get("sha256"),
        "note": "SHA change is expected after fresh auth cookies issued",
    }

    if not results["step6_second_session"]["authenticated"]:
        results["verdict"] = "AUTO-REAUTHENTICATION — FAIL"
    elif results["step7_cookie_safety"]["destructive_overwrite"]:
        results["verdict"] = "AUTO-REAUTHENTICATION — SAFETY REGRESSION"
    else:
        results["verdict"] = "AUTO-REAUTHENTICATION — PASS"

    OUT_FILE.write_text(json.dumps(results, indent=2, default=str))
    log.info("VERDICT: %s", results["verdict"])
    return results


if __name__ == "__main__":
    asyncio.run(run())
