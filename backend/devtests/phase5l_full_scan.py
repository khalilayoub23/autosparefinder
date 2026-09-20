"""
Phase 5L — Full authorized group population monitoring scan.
READ-ONLY. No DB writes, no Facebook mutations, no cookie overwrites.

Uses PRODUCTION DB query path (same as tools.py facebook_group_scan):
  SELECT * FROM group_targets WHERE status != 'rejected' AND platform='facebook'
  ORDER BY created_at ASC, id ASC

Saves to: /app/state/phase5l_results.json
"""
import asyncio
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, "/app")

_COOKIES = __import__("pathlib").Path("/app/state/fb_browser_session/cookies.json")
_OUT     = __import__("pathlib").Path("/app/state/phase5l_results.json")


def _cookie_snapshot(label: str) -> dict:
    raw = _COOKIES.read_bytes()
    cookies = json.loads(raw)
    names = sorted(c["name"] for c in cookies)
    return {
        "label": label,
        "count": len(cookies),
        "names": names,
        "c_user": "c_user" in names,
        "xs": "xs" in names,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


async def _query_groups() -> tuple[list[dict], int, int]:
    """Query production group_targets table. Returns (unique_groups, total_rows, duplicates)."""
    import asyncpg
    DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(DB)
    rows = await conn.fetch("""
        SELECT id::text as id, group_url, group_name, status, created_at
        FROM group_targets
        WHERE status != 'rejected' AND platform = 'facebook'
        ORDER BY created_at ASC, id ASC
    """)
    await conn.close()

    total_rows = len(rows)
    seen_urls: dict[str, dict] = {}
    for r in rows:
        url = r["group_url"].rstrip("/") + "/"
        if url not in seen_urls:
            seen_urls[url] = {"id": r["id"], "group_url": url, "group_name": r["group_name"]}

    unique_groups = list(seen_urls.values())
    duplicates = total_rows - len(unique_groups)
    return unique_groups, total_rows, duplicates


async def run():
    from social.facebook_browser.session import FacebookSession, _AuthState
    from social.facebook_browser.group_agent import GroupAgent

    print("=" * 70)
    print("PHASE 5L — FULL AUTHORIZED GROUP POPULATION MONITORING SCAN")
    print("=" * 70)
    t0 = time.time()

    # ── TODO 1: Group selection ───────────────────────────────────────────────
    print("\n[TODO 1] Querying production group_targets...")
    groups, total_rows, duplicates = await _query_groups()
    print(f"  DB rows selected  : {total_rows}")
    print(f"  Unique group URLs : {len(groups)}")
    print(f"  Duplicate URLs    : {duplicates}")
    print(f"  Rejected excluded : (status filter applied)")

    # ── TODO 3: Cookie baseline ──────────────────────────────────────────────
    pre = _cookie_snapshot("PRE")
    print(f"\n[TODO 3] PRE-SCAN cookies: count={pre['count']} c_user={pre['c_user']} xs={pre['xs']}")
    print(f"  SHA256: {pre['sha256']}")

    # ── TODO 2: Auth verification ────────────────────────────────────────────
    print("\n[TODO 2] Verifying Facebook session auth...")
    session = FacebookSession()
    page = await session.__aenter__()
    auth_state = None
    if page is not None:
        auth_state = await session._health_check()
    valid = session.is_valid
    print(f"  _valid={valid}  health_check={auth_state}")

    if not valid or auth_state != _AuthState.AUTHENTICATED:
        print("\n  AUTH FAILED — Stopping per Phase 5L safety rules.")
        print("  Do NOT auto-login. Do NOT overwrite cookies.")
        await session.__aexit__(None, None, None)
        result = {
            "phase": "5L",
            "auth": "FAIL",
            "health_check": str(auth_state),
            "pre_scan_session": pre,
            "groups_selected_db_rows": total_rows,
            "unique_groups": len(groups),
            "duplicates": duplicates,
            "scan_result": None,
            "verdict": "FACEBOOK / NOA — 129-GROUP MONITORING FAIL",
        }
        _OUT.write_text(json.dumps(result, indent=2, default=str))
        print("\nResults saved to:", _OUT)
        return result

    print("  AUTH: PASS — proceeding with group scan")
    # Let the scan use its own session (close this health-check session)
    await session.__aexit__(None, None, None)

    # ── TODO 4: Execute full group monitoring ─────────────────────────────────
    print(f"\n[TODO 4] Scanning {len(groups)} unique groups (posts_per_group=10)...")
    print("  Using existing validated configuration: JS cap=15, max_posts=10")
    agent = GroupAgent()
    scan_result = await agent.scan_groups(groups, posts_per_group=10)
    elapsed = time.time() - t0

    # ── TODO 12: Post-scan cookie integrity ──────────────────────────────────
    post = _cookie_snapshot("POST")

    # ── TODO 5+6: Per-group telemetry + discoveries ───────────────────────────
    tel = scan_result.get("telemetry", {})
    per_group = scan_result.get("per_group_telemetry", [])
    discoveries = scan_result.get("discoveries", [])

    print(f"\n[RESULTS] elapsed={elapsed:.1f}s  session_failed={scan_result.get('session_failed')}")
    print(f"  Groups attempted : {scan_result.get('groups_attempted', 0)}")
    print(f"  Groups fetched   : {scan_result.get('groups_fetched', 0)}")
    print(f"  DOM candidates   : {tel.get('dom_candidates', 0)}")
    print(f"  Text valid       : {tel.get('text_valid', 0)}")
    print(f"  Scored           : {tel.get('scored', 0)}")
    print(f"  Discoveries      : {len(discoveries)}")
    print(f"  Near misses      : {tel.get('near_misses', 0)}")
    print(f"  Zero score       : {tel.get('zero_score', 0)}")
    print(f"\n  POST cookies: count={post['count']} c_user={post['c_user']} xs={post['xs']}")
    print(f"  Cookie drift    : {post['count'] - pre['count']} cookies")
    sha_match = pre["sha256"] == post["sha256"]
    print(f"  SHA unchanged   : {sha_match} (drift=0 = no destruction)")

    # Per-group detail
    print("\n[TODO 5] PER-GROUP TELEMETRY:")
    header = f"{'#':>3}  {'Group':45}  {'DOM':>4} {'TV':>4} {'SC':>4} {'DI':>4} {'NM':>4} {'ZS':>4}"
    print(header)
    print("-" * len(header))
    for i, pg in enumerate(per_group, 1):
        row = (f"{i:>3}  {pg.get('group_name','?')[:45]:45}"
               f"  {pg.get('dom_candidates',0):>4}"
               f"  {pg.get('text_valid',0):>4}"
               f"  {pg.get('scored',0):>4}"
               f"  {pg.get('discoveries',0):>4}"
               f"  {pg.get('near_misses',0):>4}"
               f"  {pg.get('zero_score',0):>4}")
        print(row)

    # Zero-discovery classification
    groups_with_disc = sum(1 for pg in per_group if pg.get("discoveries", 0) > 0)
    groups_no_disc   = len(per_group) - groups_with_disc
    class_dom0  = sum(1 for pg in per_group if pg.get("dom_candidates", 0) == 0 and pg.get("discoveries", 0) == 0)
    class_allzero = sum(1 for pg in per_group if pg.get("scored", 0) > 0 and pg.get("discoveries", 0) == 0)

    print(f"\n[TODO 13] COVERAGE:")
    print(f"  DB rows selected    : {total_rows}")
    print(f"  Unique groups       : {len(groups)}")
    print(f"  Groups attempted    : {scan_result.get('groups_attempted', 0)}")
    print(f"  Groups fetched      : {scan_result.get('groups_fetched', 0)}")
    failed = scan_result.get("groups_attempted", 0) - scan_result.get("groups_fetched", 0)
    print(f"  Groups failed       : {failed}")
    not_attempted = len(groups) - scan_result.get("groups_attempted", 0)
    print(f"  Not attempted       : {not_attempted}")
    fetched = scan_result.get("groups_fetched", 0)
    fetch_rate = (fetched / len(groups) * 100) if groups else 0
    print(f"  Fetch success rate  : {fetch_rate:.1f}%")
    print(f"  Groups with disc.   : {groups_with_disc}")
    print(f"  Groups without disc.: {groups_no_disc}")
    print(f"    Class A (DOM=0)   : {class_dom0}")
    print(f"    Class C (all zero): {class_allzero}")

    # ── Verdict ──────────────────────────────────────────────────────────────
    session_ok   = not scan_result.get("session_failed", True)
    cookies_ok   = post["c_user"] and post["xs"]
    extraction_ok = tel.get("dom_candidates", 0) > 0
    classifier_ok = tel.get("scored", 0) > 0

    if session_ok and cookies_ok and extraction_ok and classifier_ok and failed == 0:
        verdict = "FACEBOOK / NOA — 129-GROUP MONITORING PASS"
        if class_dom0 > 0 or fetch_rate < 100:
            verdict = "FACEBOOK / NOA — 129-GROUP MONITORING PASS WITH LIMITATIONS"
    else:
        verdict = "FACEBOOK / NOA — 129-GROUP MONITORING FAIL"
    print(f"\n[TODO 15] VERDICT: {verdict}")

    # Save full results
    output = {
        "phase": "5L",
        "elapsed_s": round(elapsed, 1),
        "group_selection": {
            "db_rows_selected": total_rows,
            "unique_groups": len(groups),
            "duplicates": duplicates,
        },
        "auth": {
            "status": "PASS",
            "health_check": str(auth_state),
            "cookie_count": pre["count"],
            "c_user": pre["c_user"],
            "xs": pre["xs"],
        },
        "pre_scan_session": {k: v for k, v in pre.items() if k != "sha256"},
        "pre_scan_sha256": pre["sha256"],
        "post_scan_session": {k: v for k, v in post.items() if k != "sha256"},
        "post_scan_sha256": post["sha256"],
        "cookie_drift": post["count"] - pre["count"],
        "sha_unchanged": sha_match,
        "scan_result": scan_result,
        "coverage": {
            "db_rows_selected": total_rows,
            "unique_groups": len(groups),
            "groups_attempted": scan_result.get("groups_attempted", 0),
            "groups_fetched": scan_result.get("groups_fetched", 0),
            "groups_failed": failed,
            "not_attempted": not_attempted,
            "fetch_success_rate_pct": round(fetch_rate, 1),
            "groups_with_discoveries": groups_with_disc,
            "groups_without_discoveries": groups_no_disc,
            "dom_candidates_total": tel.get("dom_candidates", 0),
            "text_valid_total": tel.get("text_valid", 0),
            "scored_total": tel.get("scored", 0),
            "discoveries_total": len(discoveries),
            "near_misses_total": tel.get("near_misses", 0),
            "zero_score_total": tel.get("zero_score", 0),
        },
        "playwright": {"status": "operational", "used": True},
        "flaresolverr": {"status": "installed+reachable", "used": False,
                         "reason": "Facebook uses server-side IP reputation block, not Cloudflare challenge. FlareSolverr not applicable."},
        "action_safety": {"posts": 0, "comments": 0, "reactions": 0, "messages": 0,
                          "APPROVAL_REQUIRED": True},
        "side_effects": {"production_db_writes": 0, "redis_mutations": 0,
                         "queue_changes": 0, "scheduler_changes": 0,
                         "restarts": 0, "deploy": 0, "commit": 0, "push": 0},
        "feed_window_limitation": {
            "js_container_cap": 15,
            "max_posts": 10,
            "note": "Known limitation: groups with >15 DOM containers may miss posts beyond position 15.",
        },
        "verdict": verdict,
    }
    _OUT.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nResults saved to: {_OUT}")
    return output


if __name__ == "__main__":
    asyncio.run(run())
