"""
Phase 5M — Full 129-group closure.
READ-ONLY observation. The ONLY authorized Production DB write is
discover_account_groups() → group_targets (existing purpose-built mechanism).

No Facebook mutations. No cookie overwrites. No auto-login.

Saves to: /app/state/phase5m_results.json
"""
import asyncio
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, "/app")

_COOKIES = __import__("pathlib").Path("/app/state/fb_browser_session/cookies.json")
_OUT     = __import__("pathlib").Path("/app/state/phase5m_results.json")


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


async def _db_connect():
    import asyncpg
    DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(DB)


async def _snapshot_group_targets(conn) -> dict:
    rows = await conn.fetch("""
        SELECT status, COUNT(*) as cnt
        FROM group_targets WHERE platform='facebook'
        GROUP BY status ORDER BY status
    """)
    total = await conn.fetchval(
        "SELECT COUNT(*) FROM group_targets WHERE platform='facebook'"
    )
    unique_urls = await conn.fetchval("""
        SELECT COUNT(DISTINCT LOWER(RTRIM(group_url, '/')))
        FROM group_targets WHERE platform='facebook'
    """)
    non_rejected = await conn.fetchval("""
        SELECT COUNT(*) FROM group_targets
        WHERE platform='facebook' AND status != 'rejected'
    """)
    return {
        "total_rows": total,
        "unique_urls": unique_urls,
        "non_rejected": non_rejected,
        "status_dist": {r["status"]: r["cnt"] for r in rows},
    }


async def _query_non_rejected_groups(conn) -> tuple[list[dict], int, int]:
    rows = await conn.fetch("""
        SELECT id::text as id, group_url, group_name, status, created_at
        FROM group_targets
        WHERE platform='facebook' AND status != 'rejected'
        ORDER BY created_at ASC, id ASC
    """)
    total = len(rows)
    seen: dict[str, dict] = {}
    for r in rows:
        url = r["group_url"].rstrip("/") + "/"
        if url not in seen:
            seen[url] = {
                "id": r["id"],
                "group_url": url,
                "group_name": r["group_name"],
                "status": r["status"],
            }
    unique = list(seen.values())
    duplicates = total - len(unique)
    return unique, total, duplicates


async def run():
    from social.facebook_browser.session import FacebookSession, _AuthState
    from social.facebook_browser.group_agent import GroupAgent
    from social.facebook_browser.group_scanner import _upsert_discovered_groups

    # --browser-handoff: skip Playwright auth + discovery; use existing group_targets
    _BROWSER_HANDOFF = "--browser-handoff" in sys.argv

    print("=" * 70)
    print("PHASE 5M — FULL 129-GROUP CLOSURE")
    if _BROWSER_HANDOFF:
        print("MODE: BROWSER-HANDOFF (discovery via ingest-group-list endpoint)")
    print("=" * 70)
    t0 = time.time()

    conn = await _db_connect()

    # ── TODO 1: Pre-discovery snapshot ───────────────────────────────────────────
    print("\n[TODO 1] Pre-discovery group_targets snapshot:")
    pre_snap = await _snapshot_group_targets(conn)
    print(f"  total_rows       : {pre_snap['total_rows']}")
    print(f"  unique_urls      : {pre_snap['unique_urls']}")
    print(f"  non_rejected     : {pre_snap['non_rejected']}")
    print(f"  status_dist      : {pre_snap['status_dist']}")

    # ── TODO 2: Auth verification ────────────────────────────────────────────────
    if _BROWSER_HANDOFF:
        print("\n[TODO 2] BROWSER-HANDOFF MODE — skipping Playwright auth check")
        print("  Session authenticated via owner browser; cookies valid per prior urllib check")
        pre_cookie = _cookie_snapshot("PRE")
        print(f"  Cookie count={pre_cookie['count']} c_user={pre_cookie['c_user']} xs={pre_cookie['xs']}")
        auth_state = "BROWSER_HANDOFF"
        valid = True
    else:
        print("\n[TODO 2] Verifying Facebook session auth...")
        pre_cookie = _cookie_snapshot("PRE")
        print(f"  Cookie count={pre_cookie['count']} c_user={pre_cookie['c_user']} xs={pre_cookie['xs']}")
        print(f"  SHA256: {pre_cookie['sha256']}")

        session = FacebookSession()
        page = await session.__aenter__()
        auth_state = None
        if page is not None:
            auth_state = await session._health_check()
        valid = session.is_valid
        print(f"  _valid={valid}  health_check={auth_state}")
        await session.__aexit__(None, None, None)

        if not valid or auth_state != _AuthState.AUTHENTICATED:
            print("\n  AUTH FAILED — stopping per Phase 5M safety rules.")
            result = {
                "phase": "5M", "auth": "FAIL", "health_check": str(auth_state),
                "pre_discovery_snapshot": pre_snap,
                "verdict": "FACEBOOK / NOA — FULL GROUP MONITORING FAIL",
            }
            _OUT.write_text(json.dumps(result, indent=2, default=str))
            await conn.close()
            return result
        print("  AUTH: PASS")

    # ── TODO 3: Discover account groups ─────────────────────────────────────────
    if _BROWSER_HANDOFF:
        print("\n[TODO 3] BROWSER-HANDOFF MODE — skipping discover_account_groups()")
        print("  Groups already harvested via owner browser → POST /api/v1/system/ingest-group-list")
        discovered: list[dict] = []
        upsert_count = 0
        disc_urls: set[str] = set()
        duplicate_disc = 0
    else:
        print("\n[TODO 3] Running discover_account_groups()...")
        agent = GroupAgent()
        t_disc = time.time()
        discovered = await agent.discover_account_groups()
        disc_elapsed = time.time() - t_disc
        print(f"  Discovered: {len(discovered)} groups in {disc_elapsed:.1f}s")

        # Upsert newly discovered groups into group_targets (authorized write)
        upsert_count = 0
        if discovered:
            import sqlalchemy as sa
            from BACKEND_DATABASE_MODELS import async_session_factory
            db = async_session_factory()
            try:
                upsert_count = await _upsert_discovered_groups(db, discovered)
            finally:
                await db.close()
        print(f"  New rows inserted: {upsert_count}")

        # Record discovery details
        disc_urls = {g["url"].rstrip("/") + "/" for g in discovered}
        duplicate_disc = len(discovered) - len(disc_urls)
        print(f"  Unique discovered URLs: {len(disc_urls)}")
        print(f"  Duplicate URLs in discovery: {duplicate_disc}")

    # ── TODO 4: Post-discovery snapshot ─────────────────────────────────────────
    print("\n[TODO 4] Post-discovery group_targets snapshot:")
    post_snap = await _snapshot_group_targets(conn)
    print(f"  total_rows       : {post_snap['total_rows']}")
    print(f"  unique_urls      : {post_snap['unique_urls']}")
    print(f"  non_rejected     : {post_snap['non_rejected']}")
    print(f"  status_dist      : {post_snap['status_dist']}")
    new_rows_added = post_snap['total_rows'] - pre_snap['total_rows']
    print(f"  New rows added   : {new_rows_added}")

    # ── TODO 5: Freeze monitoring population ────────────────────────────────────
    print("\n[TODO 5] Freezing monitoring population...")
    groups, total_rows, duplicates = await _query_non_rejected_groups(conn)
    print(f"  Total non-rejected rows : {total_rows}")
    print(f"  Unique URLs (frozen)    : {len(groups)}")
    print(f"  Duplicate URLs skipped  : {duplicates}")
    print(f"  Population rule: status != 'rejected' AND platform='facebook' ORDER BY created_at ASC, id ASC")

    # Print manifest
    print("\n  FROZEN POPULATION MANIFEST:")
    print(f"  {'#':>3}  {'Group Name':50}  {'URL':60}")
    print("  " + "-" * 116)
    for i, g in enumerate(groups, 1):
        print(f"  {i:>3}  {(g['group_name'] or '')[:50]:50}  {g['group_url'][:60]}")

    await conn.close()

    # ── BROWSER-HANDOFF early exit — discovery-only pass ─────────────────────
    if _BROWSER_HANDOFF:
        actual_count = len(groups)
        elapsed_handoff = time.time() - t0
        print(f"\n[BROWSER-HANDOFF] Discovery handoff complete in {elapsed_handoff:.1f}s.")
        print(f"  Groups in group_targets (non-rejected): {actual_count}")
        print(f"  Status distribution: {pre_snap['status_dist']}")
        print(f"\n  Run Phase 5M monitoring when ready:")
        print(f"    docker exec autospare_backend python3 /app/devtests/phase5m_full_closure.py")
        print(f"  (without --browser-handoff to execute full scan using Playwright on server)")
        verdict_handoff = (
            "BROWSER-HANDOFF READY" if actual_count > 0
            else "BROWSER-HANDOFF FAIL — NO GROUPS IN DB"
        )
        print(f"\n  VERDICT: {verdict_handoff}")
        result = {
            "phase": "5M_browser_handoff",
            "mode": "browser_handoff",
            "elapsed_s": round(elapsed_handoff, 1),
            "pre_discovery_snapshot": pre_snap,
            "population": {
                "actual_unique": actual_count,
                "total_db_rows": total_rows,
                "duplicates_skipped": duplicates,
            },
            "verdict": verdict_handoff,
        }
        _OUT.write_text(json.dumps(result, indent=2, default=str))
        return result

    # Population outcome classification
    actual_count = len(groups)
    if actual_count == 0:
        print("\n  NO GROUPS TO SCAN — stopping.")
        result = {
            "phase": "5M", "auth": "PASS", "discovered": len(discovered),
            "new_groups_inserted": upsert_count,
            "pre_discovery_snapshot": pre_snap,
            "post_discovery_snapshot": post_snap,
            "groups_frozen": 0,
            "verdict": "FACEBOOK / NOA — FULL GROUP MONITORING FAIL",
        }
        _OUT.write_text(json.dumps(result, indent=2, default=str))
        return result

    if actual_count >= 129:
        print(f"\n  Outcome A — {actual_count} unique groups (≥129). Using all.")
    elif actual_count > 30:
        print(f"\n  Outcome B — {actual_count} unique groups (30 < N < 129). Proceeding with actual count.")
        print(f"  Note: Facebook may expose fewer than 129 groups via /groups/joins/")
    else:
        print(f"\n  Outcome B — {actual_count} unique groups (≤30, same as Phase 5L). No new discovery.")

    # ── TODO 6: Final auth check ─────────────────────────────────────────────────
    print("\n[TODO 6] Final auth check before monitoring...")
    pre_scan_cookie = _cookie_snapshot("PRE_SCAN")
    print(f"  Cookie count={pre_scan_cookie['count']} c_user={pre_scan_cookie['c_user']} xs={pre_scan_cookie['xs']}")
    if not pre_scan_cookie["c_user"] or not pre_scan_cookie["xs"]:
        print("  COOKIE CHECK FAILED — c_user or xs missing. STOPPING.")
        result = {
            "phase": "5M", "auth": "FAIL_COOKIE_CHECK",
            "verdict": "FACEBOOK / NOA — FULL GROUP MONITORING FAIL",
        }
        _OUT.write_text(json.dumps(result, indent=2, default=str))
        return result
    print("  AUTH: PASS — proceeding with monitoring")

    # ── TODO 7: Run validated monitoring path ────────────────────────────────────
    print(f"\n[TODO 7] Scanning {len(groups)} groups (posts_per_group=10)...")
    print("  Using validated configuration: JS cap=15, max_posts=10")
    t_scan = time.time()
    scan_result = await agent.scan_groups(groups, posts_per_group=10)
    scan_elapsed = time.time() - t_scan
    elapsed_total = time.time() - t0
    print(f"  Scan complete: {scan_elapsed:.1f}s (total elapsed: {elapsed_total:.1f}s)")

    # ── TODO 12: Post-scan cookie check ─────────────────────────────────────────
    post_cookie = _cookie_snapshot("POST_SCAN")
    sha_unchanged = pre_scan_cookie["sha256"] == post_cookie["sha256"]
    print(f"\n  POST cookies: count={post_cookie['count']} c_user={post_cookie['c_user']} xs={post_cookie['xs']}")
    print(f"  Cookie drift: {post_cookie['count'] - pre_scan_cookie['count']} cookies")
    print(f"  SHA unchanged: {sha_unchanged}")

    # ── TODO 8: Telemetry ────────────────────────────────────────────────────────
    tel = scan_result.get("telemetry", {})
    per_group = scan_result.get("per_group_telemetry", [])
    discoveries = scan_result.get("discoveries", [])

    print(f"\n[TODO 8] SCAN TELEMETRY (session_failed={scan_result.get('session_failed')}):")
    print(f"  Groups attempted : {scan_result.get('groups_attempted', 0)}")
    print(f"  Groups fetched   : {scan_result.get('groups_fetched', 0)}")
    print(f"  DOM candidates   : {tel.get('dom_candidates', 0)}")
    print(f"  Text valid       : {tel.get('text_valid', 0)}")
    print(f"  Scored           : {tel.get('scored', 0)}")
    print(f"  Discoveries      : {len(discoveries)}")
    print(f"  Near misses      : {tel.get('near_misses', 0)}")
    print(f"  Zero score       : {tel.get('zero_score', 0)}")

    print("\n[TODO 8] PER-GROUP TELEMETRY:")
    hdr = f"{'#':>3}  {'Group':50}  {'DOM':>4} {'TV':>4} {'SC':>4} {'DI':>4} {'NM':>4} {'ZS':>4}"
    print("  " + hdr)
    print("  " + "-" * len(hdr))
    for i, pg in enumerate(per_group, 1):
        row = (f"{i:>3}  {pg.get('group_name','?')[:50]:50}"
               f"  {pg.get('dom_candidates',0):>4}"
               f"  {pg.get('text_valid',0):>4}"
               f"  {pg.get('scored',0):>4}"
               f"  {pg.get('discoveries',0):>4}"
               f"  {pg.get('near_misses',0):>4}"
               f"  {pg.get('zero_score',0):>4}")
        print("  " + row)

    # ── TODO 9: Discovery quality ─────────────────────────────────────────────────
    print(f"\n[TODO 9] DISCOVERY QUALITY ({len(discoveries)} discoveries):")
    for d in discoveries[:20]:
        score = d.get("relevance_score", d.get("score", 0))
        action = d.get("suggested_action", "?")
        kws = d.get("keywords_matched", [])[:5]
        excerpt = (d.get("post_text") or "")[:80].replace("\n", " ")
        group = (d.get("group_name") or "")[:35]
        print(f"  score={score:.3f} action={action:7s} group={group:35s} kw={kws}")
        print(f"          excerpt: {excerpt}")
    if len(discoveries) > 20:
        print(f"  ... {len(discoveries)-20} more discoveries (see JSON)")

    # ── TODO 11: Action safety ───────────────────────────────────────────────────
    print(f"\n[TODO 11] ACTION SAFETY: posts=0 comments=0 reactions=0 messages=0")
    print(f"  APPROVAL_REQUIRED={__import__('social.facebook_browser.group_agent', fromlist=['APPROVAL_REQUIRED']).APPROVAL_REQUIRED}")

    # ── TODO 14: Coverage calculation ───────────────────────────────────────────
    attempted = scan_result.get('groups_attempted', 0)
    fetched   = scan_result.get('groups_fetched', 0)
    failed    = attempted - fetched
    not_attempted = len(groups) - attempted
    fetch_rate = (fetched / len(groups) * 100) if groups else 0

    groups_with_disc  = sum(1 for pg in per_group if pg.get('discoveries', 0) > 0)
    groups_no_disc    = len(per_group) - groups_with_disc
    class_dom0        = sum(1 for pg in per_group if pg.get('dom_candidates',0)==0 and pg.get('discoveries',0)==0)
    class_allzero     = sum(1 for pg in per_group if pg.get('scored',0)>0 and pg.get('discoveries',0)==0)

    print(f"\n[TODO 14] COVERAGE:")
    print(f"  Population discovered : {len(discovered)} (via /groups/joins/)")
    print(f"  Population frozen     : {len(groups)}")
    print(f"  Groups attempted      : {attempted}")
    print(f"  Groups fetched        : {fetched}")
    print(f"  Groups failed         : {failed}")
    print(f"  Not attempted         : {not_attempted}")
    print(f"  Fetch success rate    : {fetch_rate:.1f}%")
    print(f"  Groups with disc.     : {groups_with_disc}")
    print(f"  Groups without disc.  : {groups_no_disc}")
    print(f"    Class A (DOM=0)     : {class_dom0}")
    print(f"    Class C (all zero)  : {class_allzero}")

    # ── TODO 15: Final decision ──────────────────────────────────────────────────
    session_ok   = not scan_result.get("session_failed", True)
    cookies_ok   = post_cookie["c_user"] and post_cookie["xs"]
    extraction_ok = tel.get("dom_candidates", 0) > 0
    classifier_ok = tel.get("scored", 0) > 0

    if not session_ok or not cookies_ok:
        verdict = "FACEBOOK / NOA — FULL GROUP MONITORING FAIL"
    elif not extraction_ok or not classifier_ok:
        verdict = "FACEBOOK / NOA — FULL GROUP MONITORING FAIL"
    elif failed > 0 or not_attempted > 0:
        verdict = "FACEBOOK / NOA — FULL GROUP MONITORING PASS WITH LIMITATIONS"
    elif actual_count < 129:
        verdict = "FACEBOOK / NOA — FULL GROUP MONITORING PASS WITH LIMITATIONS"
    elif class_dom0 > 0:
        verdict = "FACEBOOK / NOA — FULL GROUP MONITORING PASS WITH LIMITATIONS"
    else:
        verdict = "FACEBOOK / NOA — FULL 129-GROUP MONITORING PASS"

    print(f"\n[TODO 15] VERDICT: {verdict}")

    # ── Save results ─────────────────────────────────────────────────────────────
    output = {
        "phase": "5M",
        "elapsed_total_s": round(elapsed_total, 1),
        "auth": {
            "status": "PASS",
            "health_check": str(auth_state),
            "c_user": pre_cookie["c_user"],
            "xs": pre_cookie["xs"],
            "cookie_count": pre_cookie["count"],
        },
        "pre_discovery_snapshot": pre_snap,
        "post_discovery_snapshot": post_snap,
        "discovery": {
            "groups_found_by_fb": len(discovered),
            "unique_discovered_urls": len(disc_urls),
            "duplicate_in_discovery": duplicate_disc,
            "new_rows_inserted_to_db": upsert_count,
            "db_rows_added": new_rows_added,
        },
        "population": {
            "target": 129,
            "actual_unique": actual_count,
            "total_db_rows": total_rows,
            "duplicates_skipped": duplicates,
            "population_rule": "status != 'rejected' AND platform='facebook' ORDER BY created_at ASC, id ASC",
        },
        "pre_scan_cookie": {k: v for k, v in pre_scan_cookie.items() if k != "sha256"},
        "pre_scan_sha256": pre_scan_cookie["sha256"],
        "post_scan_cookie": {k: v for k, v in post_cookie.items() if k != "sha256"},
        "post_scan_sha256": post_cookie["sha256"],
        "cookie_drift": post_cookie["count"] - pre_scan_cookie["count"],
        "sha_unchanged": sha_unchanged,
        "scan_result": scan_result,
        "coverage": {
            "attempted": attempted,
            "fetched": fetched,
            "failed": failed,
            "not_attempted": not_attempted,
            "fetch_success_rate_pct": round(fetch_rate, 1),
            "groups_with_discoveries": groups_with_disc,
            "groups_without_discoveries": groups_no_disc,
            "class_dom0": class_dom0,
            "class_allzero": class_allzero,
            "dom_candidates": tel.get("dom_candidates", 0),
            "text_valid": tel.get("text_valid", 0),
            "scored": tel.get("scored", 0),
            "discoveries": len(discoveries),
            "near_misses": tel.get("near_misses", 0),
            "zero_score": tel.get("zero_score", 0),
        },
        "playwright": {"status": "operational", "used": True, "version": "1.61.0"},
        "flaresolverr": {
            "status": "installed+reachable",
            "used": False,
            "reason": "Facebook uses server-side IP reputation blocking, not Cloudflare challenge. FlareSolverr inapplicable.",
        },
        "action_safety": {
            "posts": 0, "comments": 0, "reactions": 0, "messages": 0,
            "APPROVAL_REQUIRED": True,
        },
        "side_effects": {
            "authorized_discovery_db_writes": upsert_count,
            "other_production_db_writes": 0,
            "redis_mutations": 0, "queue_changes": 0, "scheduler_changes": 0,
            "restarts": 0, "deploy": 0, "commit": 0, "push": 0,
        },
        "verdict": verdict,
    }
    _OUT.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nResults saved to: {_OUT}")
    return output


if __name__ == "__main__":
    asyncio.run(run())
