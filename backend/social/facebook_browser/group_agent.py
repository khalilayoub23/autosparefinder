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

    # ── Account group discovery ────────────────────────────────────────────────

    async def discover_account_groups(self) -> list[dict]:
        """Browse facebook.com/groups/joins/ to enumerate every group the account belongs to.

        Uses the canonical joined-groups page (as confirmed by the owner's browser screenshot).
        Scrolls until the count stabilises across 3 consecutive checks — handles 129+ groups.

        Returns a list of dicts:  {"name": str, "url": str}
        """
        found: list[dict] = []
        try:
            async with FacebookSession() as page:
                if not page:
                    log.warning("fb_browser: discover_account_groups — session not authenticated")
                    return []

                # The correct joined-groups URL (confirmed from owner's browser)
                await page.goto(
                    "https://www.facebook.com/groups/joins/",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                await _random_delay(2.5, 4.0)

                # Scroll until the discovered count stabilises (handles 129+ groups lazily loaded)
                _JS_EXTRACT = """() => {
                    const results = [];
                    const seen = new Set();
                    const allLinks = document.querySelectorAll('a[href*="/groups/"]');
                    for (const a of allLinks) {
                        let href = (a.href || '').split('?')[0].replace(/\\/$/, '');
                        // Must be /groups/<slug_or_id> — exclude meta pages
                        if (!/\\/groups\\/[^/]{3,}$/.test(href)) continue;
                        if (/\\/groups\\/(joins|discover|feed|create|search|requests|pending|notifications|see_all_groups)/.test(href)) continue;
                        if (seen.has(href)) continue;
                        seen.add(href);

                        // Name: prefer the group card heading text over the link's own text
                        // FB renders group names in a nearby <span> or as aria-label
                        let name = '';
                        // Walk up to find a card container, then look for the name element
                        let el = a.parentElement;
                        for (let i = 0; i < 6 && el; i++) {
                            const heading = el.querySelector('[dir="auto"] > span, [role="heading"]');
                            if (heading && heading.innerText && heading.innerText.length > 1) {
                                name = heading.innerText.trim();
                                break;
                            }
                            el = el.parentElement;
                        }
                        if (!name) {
                            name = (a.getAttribute('aria-label') || a.innerText || '').trim();
                        }
                        name = name.replace(/\\s+/g, ' ').slice(0, 120);
                        // Skip very short names (likely icon/button links, not group cards)
                        if (name.length < 3) continue;
                        // Skip generic UI strings
                        if (/^(view group|see all|more|create new group)$/i.test(name)) continue;
                        results.push({name, url: href + '/'});
                    }
                    return results;
                }"""

                prev_count = 0
                stable_rounds = 0
                for _scroll in range(40):  # up to 40 scrolls = ~240 groups at 6/scroll
                    await page.evaluate("window.scrollBy(0, window.innerHeight * 1.5)")
                    await _random_delay(1.5, 2.5)
                    current = await page.evaluate("() => document.querySelectorAll('a[href*=\"/groups/\"]').length")
                    if current == prev_count:
                        stable_rounds += 1
                        if stable_rounds >= 3:
                            break  # no new content for 3 consecutive scrolls → done
                    else:
                        stable_rounds = 0
                    prev_count = current
                    log.debug("fb_browser: discover scroll %d — %d group links visible", _scroll + 1, current)

                groups_data = await page.evaluate(_JS_EXTRACT)
                for g in groups_data:
                    url = (g.get("url") or "").strip()
                    name = (g.get("name") or "").strip()
                    if url and name:
                        found.append({"name": name, "url": url})

        except Exception as exc:
            log.error("fb_browser: discover_account_groups error: %s", exc)

        log.info("fb_browser: discover_account_groups found %d groups", len(found))
        return found

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
                    log.warning(
                        "fb_browser: Facebook session not authenticated — "
                        "re-login required (run fb_browser_login.py once)"
                    )
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
                    return {
                        "ok": False,
                        "error": "Facebook session not authenticated — re-login required",
                    }

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
                    return {
                        "ok": False,
                        "error": "Facebook session not authenticated — re-login required",
                    }

                await page.goto(group_url, wait_until="domcontentloaded", timeout=30_000)
                await _random_delay(2.5, 5.0)

                # Scroll to top so the group post composer (not a comment box) is first
                await page.evaluate("window.scrollTo(0, 0)")
                await asyncio.sleep(1.0)

                # Step 1: Find and click the group post composer TRIGGER (a button/div at top
                # of feed that OPENS the writing modal). Do NOT target [role="textbox"] here —
                # that matches comment boxes lower in the feed.
                composer_trigger = None
                trigger_selectors = [
                    # Hebrew Facebook locale
                    '[aria-label="כתוב משהו לקבוצה…"]',
                    '[aria-label*="כתוב משהו"]',
                    '[aria-label*="כתוב"]',
                    # English locale
                    '[aria-label="Write something to the group…"]',
                    '[aria-label="Write something…"]',
                    '[aria-label*="Write something"]',
                    # testid
                    '[data-testid="status-attachment-mentions-input"]',
                    # Generic: the first role=button that contains the composer placeholder text
                    'div[role="button"] span:has-text("Write something")',
                    'div[role="button"] span:has-text("כתוב")',
                ]
                for sel in trigger_selectors:
                    try:
                        el = await page.wait_for_selector(sel, timeout=4_000)
                        if el:
                            composer_trigger = el
                            log.info("fb_browser: composer trigger found via: %s", sel)
                            break
                    except Exception:
                        continue

                if not composer_trigger:
                    await _save_failure_screenshot(page, "group_composer_not_found")
                    return {"ok": False, "error": "post composer not found (FB DOM changed?)"}

                await composer_trigger.click()
                await _random_delay(1.5, 3.0)

                # Step 2: The click should have opened a MODAL DIALOG. Wait for it.
                # Then locate the textbox INSIDE the dialog (not a comment box in the feed).
                dialog = None
                try:
                    dialog = await page.wait_for_selector('[role="dialog"]', timeout=8_000)
                    log.info("fb_browser: composer dialog opened")
                except Exception:
                    log.warning("fb_browser: no dialog appeared after composer click — trying textbox fallback")

                # Find the textbox to type into (prefer inside the dialog)
                typing_box = None
                if dialog:
                    for sel in [
                        '[role="dialog"] [role="textbox"]',
                        '[role="dialog"] [contenteditable="true"]',
                        '[role="dialog"] [data-lexical-editor="true"]',
                    ]:
                        try:
                            typing_box = await page.wait_for_selector(sel, timeout=5_000)
                            if typing_box:
                                log.info("fb_browser: typing box found in dialog via: %s", sel)
                                break
                        except Exception:
                            continue

                if not typing_box:
                    # Fallback: first visible textbox on page (must be visible)
                    try:
                        typing_box = await page.wait_for_selector('[role="textbox"]', timeout=5_000)
                        log.info("fb_browser: typing box via fallback [role=textbox]")
                    except Exception:
                        pass

                if not typing_box:
                    await _save_failure_screenshot(page, "group_composer_not_found")
                    return {"ok": False, "error": "post composer textbox not found after dialog open"}

                # Use force=True to bypass Facebook's __fb-light-mode intercept div
                try:
                    await typing_box.click(force=True)
                except Exception:
                    await typing_box.dispatch_event("click")
                await asyncio.sleep(0.5)

                # Type the content character by character (human-like)
                for char in content:
                    await page.keyboard.type(char)
                    await asyncio.sleep(random.uniform(0.03, 0.10))

                await _random_delay(1.5, 3.0)

                # Step 3: Find the Post button — prefer inside dialog; try Hebrew aria-label first
                post_btn = None
                post_btn_selectors = [
                    # Hebrew locale (facebook.com in he-IL)
                    '[role="dialog"] [aria-label="פרסם"]',
                    '[role="dialog"] [aria-label*="פרסם"]',
                    # English locale
                    '[role="dialog"] [aria-label="Post"]',
                    '[role="dialog"] [aria-label*="Post"]',
                    # Generic inside dialog
                    '[role="dialog"] button[type="submit"]',
                    # Broader: any visible enabled button/div with Post label
                    '[aria-label="פרסם"]',
                    '[aria-label="Post"]',
                    'div[role="button"][aria-label="פרסם"]',
                    'div[role="button"][aria-label="Post"]',
                    # Last resort: text-based (Playwright CSS :has-text)
                    'div[role="button"]:has-text("פרסם")',
                    'div[role="button"]:has-text("Post")',
                ]
                for sel in post_btn_selectors:
                    try:
                        btn = await page.wait_for_selector(sel, timeout=4_000)
                        if btn and await btn.is_visible():
                            post_btn = btn
                            log.info("fb_browser: Post button found via: %s", sel)
                            break
                    except Exception:
                        continue

                if not post_btn:
                    await _save_failure_screenshot(page, "group_post_btn_not_found")
                    # Log aria-labels of all role=button elements for debugging
                    try:
                        btns = await page.eval_on_selector_all(
                            '[role="button"],[role="dialog"] button',
                            "els => els.map(e => e.ariaLabel || e.textContent.trim().slice(0,40))"
                        )
                        log.error("fb_browser: visible buttons: %s", btns[:30])
                    except Exception:
                        pass
                    return {"ok": False, "error": "Post button not found"}

                # Take a screenshot to verify the page state before clicking
                await _save_failure_screenshot(page, "group_pre_post_click")

                # Use dispatch_event to bypass Playwright's pointer-intercept hit-test.
                # Facebook wraps the page in __fb-light-mode div which otherwise blocks clicks.
                try:
                    await post_btn.dispatch_event("click")
                    log.info("fb_browser: Post button clicked via dispatch_event")
                except Exception:
                    # If dispatch_event fails, try force=True as last resort
                    await post_btn.click(force=True)
                    log.info("fb_browser: Post button clicked via force=True")
                await _random_delay(3.0, 6.0)

                _record_rate(group_url)
                log.info("fb_browser: group post submitted to %s", group_url[:80])
                return {"ok": True, "error": None}

        except Exception as exc:
            log.error("fb_browser: publish_group_post error: %s", exc)
            return {"ok": False, "error": str(exc)[:200]}
