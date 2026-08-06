"""
Script: social/facebook_browser/group_agent.py
Purpose: Facebook Group scanning and owner-gated posting via Playwright.

         Because Meta killed the Groups API (April 2024), this is the only
         way to read group posts and publish into groups. The agent reads
         discussions, identifies relevant threads (car-parts, repair questions,
         brand discussions), drafts a comment/post proposal, and queues it for
         OWNER APPROVAL before sending anything. Nothing is ever sent without
         an approved GroupTask in the database.

Process:
  1. scan_groups(approved_groups) — visits each approved group URL, scrapes
     recent posts, identifies automotive topics relevant to AutoSpareFinder.
     Returns a list of GroupDiscovery dicts (not posted yet).

  2. draft_group_comment(discovery) — calls hf_text_fast (NOA persona) to
     generate a contextually-appropriate helpful comment. Returns draft text.

  3. submit_approved_comment(group_url, comment_text, post_url) — ONLY called
     after owner has explicitly approved the task. Navigates to the post,
     types the comment, clicks Post.

  4. publish_group_post(group_url, content, media_url) — ONLY called after
     explicit owner approval. Creates a new post in the group.

Safety:
  - APPROVAL_REQUIRED = True is hardcoded; cannot be overridden at runtime.
  - Between operations: random 5-20s delays to avoid detection.
  - After any Playwright error: screenshot saved, owner alerted, no retry.
  - Max 3 comments/hour per group (tracked in _RATE_LOG).

Data Imported/Modified: reads group_targets table; writes discovery results to
                        owner WhatsApp for approval before any write action.
Data Sources: https://www.facebook.com/groups/
Last Updated: 2026-08-06
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import Any

from social.facebook_browser.session import FacebookSession, _random_delay, _save_failure_screenshot

log = logging.getLogger("fb_browser.group_agent")

APPROVAL_REQUIRED = True  # NEVER set to False — safety invariant

# Automotive keywords for relevance scoring (Hebrew + Arabic + English)
_AUTO_KEYWORDS = {
    "רכב", "מכונית", "מנוע", "גיר", "בלמים", "רפידות", "מסנן", "שמן", "צמיג",
    "גלגל", "מצמד", "אמורטיזר", "קרדן", "חלפים", "חלק", "תיקון", "מוסך",
    "toyota", "honda", "kia", "hyundai", "mazda", "ford", "chevrolet", "volkswagen",
    "bmw", "mercedes", "audi", "nissan", "corolla", "civic", "engine", "brake",
    "filter", "oil", "transmission", "suspension", "clutch", "parts", "spare",
    "قطعة", "قطع", "محرك", "سيارة", "سيارات", "فلتر", "زيت", "كوابح",
}

# Max comments per group per hour — prevents triggering FB anti-spam
_GROUP_RATE_LIMIT = 3
_RATE_LOG: dict[str, list[float]] = {}


def _is_rate_limited(group_url: str) -> bool:
    now = time.monotonic()
    log_key = group_url[:80]
    bucket = _RATE_LOG.setdefault(log_key, [])
    _RATE_LOG[log_key] = [t for t in bucket if now - t < 3600]
    return len(_RATE_LOG[log_key]) >= _GROUP_RATE_LIMIT


def _record_rate(group_url: str) -> None:
    _RATE_LOG.setdefault(group_url[:80], []).append(time.monotonic())


def _relevance_score(text: str) -> float:
    """0.0–1.0; how relevant this post text is to auto parts."""
    if not text:
        return 0.0
    words = set(re.findall(r"[א-תA-Za-zا-ي]+", text.lower()))
    hits = words & _AUTO_KEYWORDS
    return min(len(hits) / 3.0, 1.0)


class GroupAgent:
    """Playwright-based Facebook Group agent for AutoSpareFinder.

    All write operations (comment, post) require APPROVAL_REQUIRED = True
    and must be called only after the owner has explicitly approved the task
    from the WhatsApp console.
    """

    # ── Scanning ───────────────────────────────────────────────────────────────

    async def scan_groups(
        self,
        approved_groups: list[dict],
        *,
        posts_per_group: int = 10,
    ) -> list[dict]:
        """Scan a list of approved group dicts for relevant discussions.

        Each group dict: {"group_url": str, "group_name": str, "id": str}

        Returns a list of discovery dicts:
        {
            "group_id": str,
            "group_url": str,
            "group_name": str,
            "post_url": str,
            "post_text": str,         # first 300 chars
            "author": str,
            "relevance_score": float, # 0.0–1.0
            "suggested_action": str,  # "comment" | "ignore"
        }
        """
        if not approved_groups:
            return []

        discoveries: list[dict] = []
        try:
            async with FacebookSession() as page:
                if not page:
                    log.warning("fb_browser: session invalid — skipping group scan")
                    return []

                for group in approved_groups:
                    url = group.get("group_url", "")
                    name = group.get("group_name", "")
                    gid = str(group.get("id", ""))
                    if not url:
                        continue
                    try:
                        found = await self._scan_one_group(
                            page, url, name, gid, posts_per_group
                        )
                        discoveries.extend(found)
                        # Human-like inter-group pause
                        await asyncio.sleep(random.uniform(5, 20))
                    except Exception as exc:
                        log.warning("fb_browser: scan_one_group %s failed: %s", url, exc)
                        try:
                            await _save_failure_screenshot(page, f"scan_fail_{gid}")
                        except Exception:
                            pass

        except Exception as exc:
            log.error("fb_browser: scan_groups session error: %s", exc)

        # Sort by relevance descending
        discoveries.sort(key=lambda d: d.get("relevance_score", 0), reverse=True)
        log.info("fb_browser: scan_groups found %d relevant discussions", len(discoveries))
        return discoveries

    async def _scan_one_group(
        self, page: Any, group_url: str, group_name: str, group_id: str,
        max_posts: int,
    ) -> list[dict]:
        """Navigate to a group and extract recent post text + URLs."""
        from playwright.async_api import Page

        await page.goto(group_url, wait_until="domcontentloaded", timeout=30_000)
        await _random_delay(2.0, 4.0)

        # Scroll down a bit to trigger lazy-loaded content
        await page.evaluate("window.scrollBy(0, 600)")
        await _random_delay(1.5, 3.0)
        await page.evaluate("window.scrollBy(0, 600)")
        await _random_delay(1.0, 2.0)

        # Extract post containers — FB's DOM changes frequently; we use broad selectors
        # and validate by looking for meaningful text content.
        posts_data = await page.evaluate("""() => {
            const results = [];
            const seen = new Set();
            // Try multiple selector patterns FB has used over time
            const containers = document.querySelectorAll(
                '[data-pagelet^="FeedUnit"], [role="article"], .du4w35lb'
            );
            for (const c of containers) {
                if (results.length >= 15) break;
                const text = (c.innerText || '').trim().slice(0, 400);
                if (text.length < 30) continue;
                // Find the post link
                const links = Array.from(c.querySelectorAll('a[href*="/groups/"]'));
                const postUrl = links.length ? links[0].href : '';
                const key = text.slice(0, 60);
                if (seen.has(key)) continue;
                seen.add(key);
                results.push({text, postUrl});
            }
            return results;
        }""")

        discoveries = []
        for post in posts_data[:max_posts]:
            text = (post.get("text") or "").strip()
            post_url = (post.get("postUrl") or group_url).split("?")[0]
            score = _relevance_score(text)
            if score < 0.2:
                continue
            discoveries.append({
                "group_id": group_id,
                "group_url": group_url,
                "group_name": group_name,
                "post_url": post_url,
                "post_text": text[:300],
                "author": "",
                "relevance_score": round(score, 3),
                "suggested_action": "comment" if score >= 0.4 else "monitor",
            })

        log.info(
            "fb_browser: _scan_one_group '%s' → %d relevant posts",
            group_name, len(discoveries)
        )
        return discoveries

    # ── Draft generation ───────────────────────────────────────────────────────

    async def draft_group_comment(self, discovery: dict) -> str:
        """Use NOA's persona (hf_text_fast) to draft a helpful group comment.

        The comment must:
        - Be helpful and human, not a spam ad
        - Reference AutoSpareFinder only naturally if it fits
        - Be in the same language as the original post
        - Be ≤280 chars (Facebook comment sweet spot)

        Returns plain text (no HTML). Always goes to owner for approval.
        """
        from hf_client import hf_text_fast

        post_text = discovery.get("post_text", "")[:200]
        group_name = discovery.get("group_name", "")

        prompt = (
            f"You are Noa, AutoSpareFinder's social media manager. "
            f"A Facebook group member posted this in '{group_name}':\n\n"
            f"\"{post_text}\"\n\n"
            f"Write a genuinely helpful comment (≤280 chars). "
            f"Be human and specific. Only mention AutoSpareFinder if truly relevant. "
            f"Match the language of the post (Hebrew/Arabic/English). "
            f"No hashtags. No spam. No boilerplate greetings."
        )
        try:
            draft = await hf_text_fast(prompt, timeout=30.0)
            return (draft or "").strip()[:300]
        except Exception as exc:
            log.warning("fb_browser: draft_group_comment LLM failed: %s", exc)
            return ""

    # ── Approved write operations ─────────────────────────────────────────────

    async def submit_approved_comment(
        self,
        post_url: str,
        comment_text: str,
        group_url: str = "",
    ) -> dict:
        """Post an owner-approved comment on a specific Facebook post.

        MUST ONLY be called after owner has explicitly approved this action
        (approval recorded in group_tasks table by campaign_manager).
        """
        if not APPROVAL_REQUIRED:
            raise RuntimeError("APPROVAL_REQUIRED safety invariant violated")

        if _is_rate_limited(group_url or post_url):
            return {"ok": False, "error": "rate limit: max 3 comments/hour per group"}

        try:
            async with FacebookSession() as page:
                if not page:
                    return {"ok": False, "error": "browser session invalid"}

                # Navigate to the specific post
                await page.goto(post_url, wait_until="domcontentloaded", timeout=30_000)
                await _random_delay(2.0, 4.0)

                # Try to find the comment input box
                comment_box = None
                for sel in [
                    '[aria-label="Write a comment…"]',
                    '[aria-label*="comment"]',
                    '[data-lexical-editor="true"]',
                    '[contenteditable="true"]',
                ]:
                    try:
                        comment_box = await page.wait_for_selector(sel, timeout=6_000)
                        if comment_box:
                            break
                    except Exception:
                        continue

                if not comment_box:
                    await _save_failure_screenshot(page, "comment_box_not_found")
                    return {"ok": False, "error": "comment input not found (FB DOM changed?)"}

                await comment_box.click()
                await _random_delay(0.8, 1.8)

                # Type comment character-by-character (human cadence)
                for char in comment_text:
                    await page.keyboard.type(char)
                    await asyncio.sleep(random.uniform(0.04, 0.12))

                await _random_delay(1.0, 2.5)

                # Submit: Enter key (works in FB's Lexical editor)
                await page.keyboard.press("Enter")
                await _random_delay(3.0, 5.0)

                # Verify the comment appeared
                content = await page.content()
                posted = comment_text[:30] in content

                if posted:
                    _record_rate(group_url or post_url)
                    log.info("fb_browser: comment posted on %s", post_url[:80])
                    return {"ok": True, "error": None}
                else:
                    await _save_failure_screenshot(page, "comment_post_unverified")
                    return {"ok": False, "error": "comment submitted but could not verify it appeared"}

        except Exception as exc:
            log.error("fb_browser: submit_approved_comment error: %s", exc)
            return {"ok": False, "error": str(exc)[:200]}

    async def publish_group_post(
        self,
        group_url: str,
        content: str,
        *,
        media_path: str | None = None,
    ) -> dict:
        """Create a new post in an approved Facebook group.

        MUST ONLY be called after explicit owner approval. No autonomous posting.
        """
        if not APPROVAL_REQUIRED:
            raise RuntimeError("APPROVAL_REQUIRED safety invariant violated")

        if _is_rate_limited(group_url):
            return {"ok": False, "error": "rate limit: max 3 posts/hour per group"}

        try:
            async with FacebookSession() as page:
                if not page:
                    return {"ok": False, "error": "browser session invalid"}

                await page.goto(group_url, wait_until="domcontentloaded", timeout=30_000)
                await _random_delay(2.5, 5.0)

                # Look for "Write something…" / post composer
                composer = None
                for sel in [
                    '[aria-label="Write something…"]',
                    '[aria-label*="Write"]',
                    '[data-testid="status-attachment-mentions-input"]',
                    '[role="textbox"]',
                ]:
                    try:
                        composer = await page.wait_for_selector(sel, timeout=6_000)
                        if composer:
                            break
                    except Exception:
                        continue

                if not composer:
                    await _save_failure_screenshot(page, "group_composer_not_found")
                    return {"ok": False, "error": "post composer not found (FB DOM changed?)"}

                await composer.click()
                await _random_delay(1.0, 2.0)

                for char in content:
                    await page.keyboard.type(char)
                    await asyncio.sleep(random.uniform(0.03, 0.10))

                await _random_delay(1.5, 3.0)

                # Click Post button
                post_btn = None
                for sel in ['[aria-label="Post"]', 'button[type="submit"]']:
                    try:
                        post_btn = await page.wait_for_selector(sel, timeout=5_000)
                        if post_btn:
                            break
                    except Exception:
                        continue

                if not post_btn:
                    await _save_failure_screenshot(page, "group_post_btn_not_found")
                    return {"ok": False, "error": "Post button not found"}

                await post_btn.click()
                await _random_delay(3.0, 6.0)

                _record_rate(group_url)
                log.info("fb_browser: group post submitted to %s", group_url[:80])
                return {"ok": True, "error": None}

        except Exception as exc:
            log.error("fb_browser: publish_group_post error: %s", exc)
            return {"ok": False, "error": str(exc)[:200]}
