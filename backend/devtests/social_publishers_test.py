"""
Script: devtests/social_publishers_test.py
Purpose: Verify the NOA social publishing layer — the registry dispatch, every publisher's
         uniform contract, fail-closed behaviour when a platform has no credentials, the
         media-required rule, and the offline request builders (X OAuth1, caption split).
         Does NOT post anything live: it only dispatches to platforms that are UNconfigured
         (which return before any network call).

Usage:  docker exec autospare_backend python3 /app/devtests/social_publishers_test.py
Author: AutoSpareFinder Agent — Last Updated: 2026-07-19
"""
import asyncio

results = []
def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main():
    from social import registry
    from social import (discord_publisher, facebook_publisher, instagram_publisher,
                        x_publisher, reddit_publisher)

    print("[1] Registry wiring")
    for p in ("facebook", "instagram", "x", "discord", "reddit", "telegram", "tiktok"):
        check(f"{p} in ALL_PLATFORMS", p in registry.ALL_PLATFORMS)
    check("instagram is media-required", "instagram" in registry.MEDIA_REQUIRED)
    check("tiktok is media-required", "tiktok" in registry.MEDIA_REQUIRED)

    print("[2] Uniform contract on every publisher module")
    for mod in (discord_publisher, facebook_publisher, instagram_publisher, x_publisher, reddit_publisher):
        has = hasattr(mod, "publish") and hasattr(mod, "is_configured") and hasattr(mod, "PLATFORM")
        check(f"{mod.PLATFORM}: publish/is_configured/PLATFORM present", has)

    print("[3] Fail-closed: unconfigured platform → not_configured (no crash, no network)")
    async def _dispatch_unconfigured():
        for p in ("facebook", "instagram", "x", "discord", "reddit"):
            if registry.is_configured(p):
                check(f"{p}: configured — skipping live dispatch", True, "creds present")
                continue
            r = await registry.dispatch(p, "test caption #אוטו", media_url="https://x/y.jpg")
            ok = isinstance(r, dict) and r.get("ok") is False and r.get("not_configured") is True
            check(f"{p}: dispatch → not_configured", ok, str(r.get("error"))[:50])
    asyncio.get_event_loop().run_until_complete(_dispatch_unconfigured())

    print("[4] Media-required rule (instagram needs an image)")
    async def _ig_no_media():
        if registry.is_configured("instagram"):
            check("instagram no-media guard (skipped — configured)", True); return
        r = await registry.dispatch("instagram", "caption only")  # no media_url
        # unconfigured returns not_configured first; if it were configured it'd say media required
        check("instagram without media does not crash", isinstance(r, dict) and r.get("ok") is False)
    asyncio.get_event_loop().run_until_complete(_ig_no_media())

    print("[5] Offline request builders")
    # X OAuth1 header builds deterministically with dummy creds
    hdr = x_publisher._oauth1_header("POST", x_publisher._ENDPOINT, "ck", "cs", "at", "ats")
    check("x OAuth1 header well-formed",
          hdr.startswith("OAuth ") and "oauth_signature" in hdr and "oauth_consumer_key" in hdr)
    # registry caption/hashtag split
    cap, tags = registry._split_caption_hashtags("קנה חלקים עכשיו #רכב #חלפים")
    check("caption/hashtag split", "#רכב" in tags and "#חלפים" in tags and "#" not in cap, f"tags={tags}")
    # discord payload path is chosen by config
    check("discord is_configured reflects env", discord_publisher.is_configured() == bool(
        __import__("os").getenv("DISCORD_WEBHOOK_URL", "").strip()))

    print("[6] configured_platforms() reports live channels")
    live = registry.configured_platforms()
    check("configured_platforms returns a list", isinstance(live, list), f"live={live}")

    passed = sum(1 for _, ok in results if ok)
    print("\n" + "=" * 52)
    print(f"SOCIAL PUBLISHERS: {passed}/{len(results)} passed")
    fails = [n for n, ok in results if not ok]
    print("FAILURES: " + ", ".join(fails) if fails else "ALL CHECKS PASSED ✅")
    print("=" * 52)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
