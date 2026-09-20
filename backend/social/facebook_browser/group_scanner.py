"""
Script: social/facebook_browser/group_scanner.py
Purpose: Full pipeline — discover Facebook groups → upsert to group_targets →
         scan approved groups for relevant posts → draft comments → store in
         group_comment_drafts → WhatsApp owner summary.

Process:
  1. discover_account_groups()  — browser, lists all joined groups
  2. Upsert discovered groups into group_targets (status='pending')
  3. Load all status='approved' group_targets
  4. scan_groups(approved)       — browser, scrapes recent posts per group
  5. draft_group_comment(discovery) — NOA LLM, one draft per relevant post
  6. INSERT into group_comment_drafts (skip duplicate post_url)
  7. WhatsApp owner: N new groups discovered, M comment drafts ready

Data Imported/Modified:
  group_targets       — INSERT OR IGNORE new discovered groups (status=pending)
  group_comment_drafts — INSERT drafted comments (status=pending_approval)

Data Sources: https://www.facebook.com/groups/?category=joined (Playwright)
Last Updated: 2026-08-11
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

log = logging.getLogger("group_scanner")

# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _get_db():
    from BACKEND_DATABASE_MODELS import async_session_factory
    return async_session_factory()


async def _upsert_discovered_groups(db, discovered: list[dict]) -> int:
    """Insert new groups as pending; skip if URL already exists. Returns new-row count."""
    import sqlalchemy as sa
    new_count = 0
    for g in discovered:
        url = g["url"].strip().rstrip("/") + "/"
        name = g["name"].strip()[:255]
        # Check if already exists (any status)
        res = await db.execute(
            sa.text("SELECT id FROM group_targets WHERE platform='facebook' AND group_url=:url LIMIT 1"),
            {"url": url},
        )
        if res.fetchone():
            continue
        await db.execute(
            sa.text("""
                INSERT INTO group_targets
                    (id, platform, group_url, group_name, status, created_at)
                VALUES
                    (gen_random_uuid(), 'facebook', :url, :name, 'pending', NOW())
            """),
            {"url": url, "name": name},
        )
        new_count += 1
    await db.commit()
    return new_count


async def _load_approved_groups(db) -> list[dict]:
    import sqlalchemy as sa
    res = await db.execute(
        sa.text("""
            SELECT id::text, group_url, group_name
            FROM group_targets
            WHERE platform='facebook' AND status='approved'
            ORDER BY last_posted_at NULLS FIRST, created_at
        """)
    )
    return [{"id": r[0], "group_url": r[1], "group_name": r[2]} for r in res.fetchall()]


async def _load_pending_groups(db) -> list[dict]:
    import sqlalchemy as sa
    res = await db.execute(
        sa.text("""
            SELECT id::text, group_url, group_name
            FROM group_targets
            WHERE platform='facebook' AND status='pending'
            ORDER BY created_at DESC
        """)
    )
    return [{"id": r[0], "group_url": r[1], "group_name": r[2]} for r in res.fetchall()]


async def _save_draft(db, group_id: str, post_url: str, post_text: str,
                      draft: str, score: float) -> bool:
    """Insert draft; returns True if inserted, False if duplicate post_url."""
    import sqlalchemy as sa
    try:
        res = await db.execute(
            sa.text("""
                INSERT INTO group_comment_drafts
                    (group_target_id, post_url, post_text, draft_comment, relevance_score, status, created_at)
                VALUES
                    (CAST(:gid AS uuid), :url, :text, :draft, :score, 'pending_approval', NOW())
                ON CONFLICT DO NOTHING
                RETURNING id
            """),
            {"gid": group_id, "url": post_url[:500], "text": post_text[:300],
             "draft": draft[:300], "score": score},
        )
        inserted = res.first() is not None  # ON CONFLICT DO NOTHING returns no row on a duplicate
        await db.commit()
        return inserted
    except Exception as exc:
        log.warning("group_scanner: save_draft failed for %s: %s", post_url[:60], exc)
        await db.rollback()
        return False


# ── WhatsApp notification ──────────────────────────────────────────────────────

async def _wa_notify(msg: str) -> None:
    try:
        from BACKEND_API_ROUTES import _wa_send_quiet
        await _wa_send_quiet(
            os.environ.get("OWNER_WHATSAPP_PHONE", ""),
            msg,
            critical=False,
        )
    except Exception as exc:
        log.warning("group_scanner: WhatsApp notify failed: %s", exc)


# ── Main pipeline ──────────────────────────────────────────────────────────────

async def _load_all_discovered_groups(db) -> list[dict]:
    """Load ALL groups (any status) for scanning — reading needs no approval, only posting does."""
    import sqlalchemy as sa
    res = await db.execute(
        sa.text("""
            SELECT id::text, group_url, group_name
            FROM group_targets
            WHERE platform='facebook' AND status != 'rejected'
            ORDER BY last_posted_at NULLS FIRST, created_at
        """)
    )
    return [{"id": r[0], "group_url": r[1], "group_name": r[2]} for r in res.fetchall()]


async def run_group_scanner(*, scan_limit: int | None = None) -> dict:
    """Run the full scan pipeline. Returns a summary dict.

    scan_limit: max groups to scan for posts in one pass. None (default) means
    no cap — scan the full eligible population, whatever its current size.
    A hardcoded 129 default previously capped scanning below the account's
    real group count once discovery sync grew the population past it
    (root-fixed 2026-09-19 — see the group discovery sync in
    BACKEND_API_ROUTES._group_scan_loop for the full context).
    Scanning (reading) does not require group approval — only POSTING/COMMENTING does.
    """
    from social.facebook_browser.group_agent import GroupAgent

    agent = GroupAgent()
    summary = {
        "discovered": 0,
        "new_groups": 0,
        "scanned_groups": 0,
        "relevant_posts": 0,
        "drafts_saved": 0,
        "errors": [],
    }

    db = await _get_db()
    try:
        # ── Step 1: Discover all 129 joined groups ────────────────────────────
        log.info("group_scanner: Step 1 — discovering account groups via browser (/groups/joins/)")
        discovered = await agent.discover_account_groups()
        summary["discovered"] = len(discovered)
        log.info("group_scanner: discovered %d groups from FB account", len(discovered))

        # ── Step 2: Upsert new groups as pending ──────────────────────────────
        if discovered:
            new_count = await _upsert_discovered_groups(db, discovered)
            summary["new_groups"] = new_count
            log.info("group_scanner: %d new groups inserted (pending)", new_count)

        # ── Step 3: Load ALL groups for scanning (reading needs no approval) ──
        all_groups = await _load_all_discovered_groups(db)
        to_scan = all_groups if scan_limit is None else all_groups[:scan_limit]
        summary["scanned_groups"] = len(to_scan)
        log.info("group_scanner: Step 3 — scanning %d groups for relevant posts", len(to_scan))

        if not to_scan:
            log.info("group_scanner: no groups to scan yet")
        else:
            # ── Step 4: Scan all groups ───────────────────────────────────────
            # Process in batches of 10 to keep Playwright sessions manageable
            BATCH = 10
            all_discoveries = []
            for i in range(0, len(to_scan), BATCH):
                batch = to_scan[i:i + BATCH]
                log.info("group_scanner: scanning batch %d/%d (%d groups)",
                         i // BATCH + 1, (len(to_scan) + BATCH - 1) // BATCH, len(batch))
                try:
                    scan_result = await agent.scan_groups(batch, posts_per_group=8)
                    discoveries = scan_result["discoveries"]
                    all_discoveries.extend(discoveries)
                    if scan_result.get("session_failed"):
                        log.error("group_scanner: batch %d — Facebook session not authenticated", i // BATCH + 1)
                        summary["errors"].append(f"batch {i // BATCH + 1}: session_failed — re-login required")
                    else:
                        log.info(
                            "group_scanner: batch found %d relevant posts (attempted=%d fetched=%d)",
                            len(discoveries), scan_result.get("groups_attempted", 0),
                            scan_result.get("groups_fetched", 0),
                        )
                except Exception as exc:
                    log.warning("group_scanner: batch %d failed: %s", i // BATCH + 1, exc)
                    summary["errors"].append(f"batch {i // BATCH + 1}: {str(exc)[:80]}")

            summary["relevant_posts"] = len(all_discoveries)
            log.info("group_scanner: total %d relevant posts found", len(all_discoveries))

            # ── Step 5+6: Draft + save comments ───────────────────────────────
            for disc in all_discoveries:
                if disc.get("relevance_score", 0) < 0.25:
                    continue
                try:
                    draft = await agent.draft_group_comment(disc)
                except Exception as exc:
                    log.warning("group_scanner: draft failed: %s", exc)
                    continue
                if not draft:
                    continue
                saved = await _save_draft(
                    db,
                    group_id=disc["group_id"],
                    post_url=disc["post_url"],
                    post_text=disc["post_text"],
                    draft=draft,
                    score=disc["relevance_score"],
                )
                if saved:
                    summary["drafts_saved"] += 1

    except Exception as exc:
        log.error("group_scanner: pipeline error: %s", exc)
        summary["errors"].append(str(exc)[:200])
    finally:
        await db.close()

    # ── Notify owner ──────────────────────────────────────────────────────────
    lines = ["🔍 *סריקת קבוצות פייסבוק — סיכום:*", ""]

    lines.append(f"📋 גילאתי *{summary['discovered']}* קבוצות מהחשבון שלך")
    if summary["new_groups"]:
        lines.append(f"➕ *{summary['new_groups']}* קבוצות חדשות נרשמו במערכת")
    lines.append(f"🔎 סרקתי *{summary['scanned_groups']}* קבוצות")
    lines.append(f"💬 מצאתי *{summary['relevant_posts']}* פוסטים רלוונטיים לרכב/חלפים")
    lines.append(f"✍️ NOA ניסחה *{summary['drafts_saved']}* תגובות לאישורך")

    if summary["drafts_saved"]:
        lines.append("")
        lines.append("לצפייה ואישור: כתוב *תגובות-גרופ*")
        lines.append("אשרתגובה <מזהה> — NOA תשלח מיד")

    if summary["errors"]:
        lines.append(f"\n⚠️ שגיאות ({len(summary['errors'])}): {'; '.join(summary['errors'][:2])}")

    await _wa_notify("\n".join(lines))
    log.info("group_scanner: done — %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_group_scanner())
