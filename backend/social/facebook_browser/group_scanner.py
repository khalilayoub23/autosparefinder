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
                    (platform, group_url, group_name, status, created_at)
                VALUES
                    ('facebook', :url, :name, 'pending', NOW())
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
        await db.execute(
            sa.text("""
                INSERT INTO group_comment_drafts
                    (group_target_id, post_url, post_text, draft_comment, relevance_score, status, created_at)
                VALUES
                    (CAST(:gid AS uuid), :url, :text, :draft, :score, 'pending_approval', NOW())
                ON CONFLICT DO NOTHING
            """),
            {"gid": group_id, "url": post_url[:500], "text": post_text[:300],
             "draft": draft[:300], "score": score},
        )
        await db.commit()
        return True
    except Exception as exc:
        log.warning("group_scanner: save_draft failed for %s: %s", post_url[:60], exc)
        await db.rollback()
        return False


# ── WhatsApp notification ──────────────────────────────────────────────────────

async def _wa_notify(msg: str) -> None:
    try:
        from BACKEND_AI_AGENTS import _wa_send_quiet
        await _wa_send_quiet(
            os.environ.get("OWNER_WHATSAPP_PHONE", ""),
            msg,
            critical=False,
        )
    except Exception as exc:
        log.warning("group_scanner: WhatsApp notify failed: %s", exc)


# ── Main pipeline ──────────────────────────────────────────────────────────────

async def run_group_scanner() -> dict:
    """Run the full scan pipeline. Returns a summary dict."""
    from social.facebook_browser.group_agent import GroupAgent

    agent = GroupAgent()
    summary = {
        "discovered": 0,
        "new_groups": 0,
        "approved_groups": 0,
        "relevant_posts": 0,
        "drafts_saved": 0,
        "errors": [],
    }

    db = await _get_db()
    try:
        # ── Step 1: Discover all joined groups ────────────────────────────────
        log.info("group_scanner: Step 1 — discovering account groups via browser")
        discovered = await agent.discover_account_groups()
        summary["discovered"] = len(discovered)
        log.info("group_scanner: discovered %d groups from FB account", len(discovered))

        # ── Step 2: Upsert new groups ─────────────────────────────────────────
        if discovered:
            new_count = await _upsert_discovered_groups(db, discovered)
            summary["new_groups"] = new_count
            log.info("group_scanner: %d new groups inserted (pending)", new_count)

        # ── Step 3: Load approved groups ──────────────────────────────────────
        approved = await _load_approved_groups(db)
        summary["approved_groups"] = len(approved)
        log.info("group_scanner: %d approved group(s) to scan", len(approved))

        if not approved:
            log.info("group_scanner: no approved groups yet — skipping scan step")
        else:
            # ── Step 4: Scan approved groups ──────────────────────────────────
            log.info("group_scanner: Step 4 — scanning %d approved group(s)", len(approved))
            discoveries = await agent.scan_groups(approved, posts_per_group=10)
            summary["relevant_posts"] = len(discoveries)
            log.info("group_scanner: found %d relevant posts across approved groups", len(discoveries))

            # ── Step 5+6: Draft + save comments ───────────────────────────────
            for disc in discoveries:
                if disc.get("relevance_score", 0) < 0.3:
                    continue
                draft = await agent.draft_group_comment(disc)
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

        # ── Step 7: Load pending list for owner notification ──────────────────
        pending_groups = await _load_pending_groups(db)

    except Exception as exc:
        log.error("group_scanner: pipeline error: %s", exc)
        summary["errors"].append(str(exc)[:200])
    finally:
        await db.close()

    # ── Notify owner ──────────────────────────────────────────────────────────
    lines = ["🔍 *סריקת קבוצות פייסבוק — סיכום:*", ""]

    if summary["discovered"]:
        lines.append(f"📋 גילאתי *{summary['discovered']}* קבוצות בחשבון שלך")
    if summary["new_groups"]:
        lines.append(f"➕ *{summary['new_groups']}* קבוצות חדשות נוספו לרשימה (ממתינות לאישורך)")

    if summary["approved_groups"]:
        lines.append(f"✅ סרקתי *{summary['approved_groups']}* קבוצות מאושרות")
        lines.append(f"💬 מצאתי *{summary['relevant_posts']}* פוסטים רלוונטיים")
        lines.append(f"✍️ ניסחתי *{summary['drafts_saved']}* תגובות לאישורך")
    else:
        lines.append("⚠️ אין קבוצות מאושרות עדיין — כתוב *גרופים* לרשימה ואשר קבוצות")

    if summary["drafts_saved"]:
        lines.append("")
        lines.append("לצפייה בתגובות שנוסחו: כתוב *תגובות-גרופ*")
    if summary["new_groups"] or (not summary["approved_groups"] and pending_groups):
        lines.append("")
        pending_names = [g["group_name"][:40] for g in pending_groups[:5]]
        lines.append(f"קבוצות ממתינות: {', '.join(pending_names)}")
        lines.append("לאישור קבוצה: *אשרגרופ <מזהה>* · לרשימה: *גרופים*")

    if summary["errors"]:
        lines.append(f"\n⚠️ שגיאות: {'; '.join(summary['errors'][:2])}")

    await _wa_notify("\n".join(lines))
    log.info("group_scanner: done — %s", summary)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_group_scanner())
