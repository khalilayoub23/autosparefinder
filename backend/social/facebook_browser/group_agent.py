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
    # Hebrew — core automotive terms (original 17)
    "רכב", "מכונית", "מנוע", "גיר", "בלמים", "רפידות", "מסנן", "שמן", "צמיג",
    "גלגל", "מצמד", "אמורטיזר", "קרדן", "חלפים", "חלק", "תיקון", "מוסך",
    # Hebrew — parts & components (expanded Phase 5E — unambiguously automotive)
    "חלקים", "חלקי", "פנס", "פנסים", "בלם",
    # Hebrew — vehicle brand names transliterated (expanded Phase 5E)
    # NOTE: ג'יפ and פיג'ו use Hebrew geresh (apostrophe); tokenizer regex includes
    # apostrophe so these multi-char tokens are preserved and matched correctly.
    "סקודה", "ג'יפ", "פיג'ו", "סיטרואן", "רנו", "טויוטה", "יונדאי", "קיה",
    # English — existing 23 (unchanged)
    "toyota", "honda", "kia", "hyundai", "mazda", "ford", "chevrolet", "volkswagen",
    "bmw", "mercedes", "audi", "nissan", "corolla", "civic", "engine", "brake",
    "filter", "oil", "transmission", "suspension", "clutch", "parts", "spare",
    # English — vehicle brand names (expanded Phase 5E)
    "skoda", "jeep", "peugeot", "citroen", "renault",
    # Arabic — existing 8 (unchanged)
    "قطعة", "قطع", "محرك", "سيارة", "سيارات", "فلتر", "زيت", "كوابح",
}

# Single-character Hebrew proclitic prefixes that attach to word stems.
# Strip one prefix only if the resulting stem is itself a known keyword.
# This guards against false positives: "בית" (house) with prefix "ב" → "ית" (not a keyword).
_HE_PROCLITICS = frozenset("לבהומשכ")

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


def _normalize_hebrew_token(token: str) -> str:
    """Strip one leading proclitic prefix IF the resulting stem is a known keyword.

    Only strips when: token starts with one of (ל ב ה ו מ ש כ), remaining stem has
    ≥2 chars, and the stem is itself in _AUTO_KEYWORDS. Lookup-guarded to prevent
    false positives (e.g. "בית" → "ית" is not a keyword so nothing is stripped).
    """
    if len(token) >= 3 and token[0] in _HE_PROCLITICS:
        stem = token[1:]
        if stem in _AUTO_KEYWORDS:
            return stem
    return token


def _relevance_score(text: str) -> float:
    """0.0–1.0; how relevant this post text is to auto parts."""
    if not text:
        return 0.0
    # Include apostrophe so Hebrew geresh digraphs (ג'יפ, פיג'ו, צ'ק, etc.) are
    # preserved as single tokens and can match keywords containing apostrophes.
    raw_words = re.findall(r"[א-תA-Za-z'ا-ي؀-ۿ]+", text.lower())
    words = set(_normalize_hebrew_token(w) for w in raw_words)
    hits = words & _AUTO_KEYWORDS
    return min(len(hits) / 3.0, 1.0)


_FB_GROUP_ID_FROM_URL_RE = re.compile(r"/groups/([^/]+)")


def _extract_fb_group_id(group_url: str) -> str:
    """Extract Facebook's own group identifier (numeric id or vanity slug)
    from a full group URL, e.g. https://www.facebook.com/groups/123456789/
    -> "123456789". Distinct from any internal DB id — used only to scope
    the /members/ self-link structural exclusion to the group being scanned.
    Returns "" if the URL doesn't match (exclusion then simply never fires,
    same as the pre-fix behavior)."""
    m = _FB_GROUP_ID_FROM_URL_RE.search(group_url)
    return m.group(1) if m else ""


def _normalize_for_merge(text: str) -> str:
    """Collapse whitespace and lowercase for substring-relationship dedup
    comparison (see _scan_one_group's text_fallback_entries). Lowercasing
    is a no-op for Hebrew/Arabic text and only affects Latin substrings
    (names, phone-adjacent text), which is fine — it only widens what
    counts as "the same capture," never narrows it."""
    return " ".join(text.split()).lower()


# Legacy selector — no longer sufficient on its own (root-cause forensic,
# 2026-09-19: on a real group with 5 owner-confirmed relevant posts, this
# selector matched ONLY comment-tail fragments and empty skeletons; the real
# posts lived elsewhere in the DOM entirely, under no role="article"/
# data-pagelet/.du4w35lb ancestor within 10 levels). Kept as part of the
# combined candidate selector below for defensiveness (some other group/UI
# variant may still render real posts this way) but no longer the sole path.
_FEED_ARTICLE_SELECTOR = '[data-pagelet^="FeedUnit"], [role="article"], .du4w35lb'

# Broadened candidate search: the confirmed real posts were plain DIV/SPAN
# elements with no special role/attribute. Unioned with the legacy selector
# so nothing previously reachable is lost.
_COMBINED_CANDIDATE_SELECTOR = _FEED_ARTICLE_SELECTOR + ", div, span, li, article"

# Shared classification logic — used by BOTH the readiness check and the
# extractor so they can never drift apart on what counts as "a real post".
# Built entirely from confirmed forensic evidence (2026-09-19), not guessed:
#   - comment fragments always carry Facebook's own "Reply"/"Share" button
#     text, or "LikeReplyShare"/"View more answers" (Q&A thread expansion)
#   - sidebar/UI chrome matches specific observed strings (composer
#     placeholder, "About", online-status widget, suggested-contacts,
#     Page-added notices) or is dominated by repeated "Facebook" alt-text
#     from stacked avatar images
#   - the group's own header/about card ("Public group ... members ...
#     Join Group") is not a post
# Deliberately does NOT require an image or a contenteditable composer —
# a real post may be text-only, and requiring either would exclude
# confirmed real posts observed without one.
_IS_REAL_POST_JS_FN = r"""
        function isRealPostCandidate(text, el, groupId, feedRoot) {
            if (text.length < 30 || text.length > 900) return false;
            // Root classification boundary (2026-09-19, same-DOM confirmed):
            // Facebook exposes exactly one role="feed" ARIA landmark wrapping
            // the actual scrollable post list. Every group-owned header/about
            // variant observed (bare name, "Join Group"/"Share", members-count,
            // AND the plain About-description that had zero links and defeated
            // every link-based exclusion below) sat OUTSIDE this landmark in
            // the same DOM snapshot where a confirmed genuine post sat INSIDE
            // it. This single containment check unifies all of those
            // previously separate false-positive families without any text or
            // link matching. Fails OPEN (skips this check) if no feed landmark
            // is found at all, so scanning still works if Facebook ever ships
            // a page variant without one — the exclusions below remain as the
            // fallback in that case.
            if (feedRoot && el && !feedRoot.contains(el)) return false;
            if (/Reply[\s\S]{0,20}Share|LikeReplyShare|View more answers|השב\s*שיתוף/.test(text)) return false;
            if (/^Write something|Online status indicator|^About\s|Suggested for you|People you may know|added to their Page|Sponsored|ממומן/.test(text)) return false;
            if (/Public group[\s\S]{0,40}members[\s\S]{0,30}Join Group/.test(text)) return false;
            const fbCount = (text.match(/Facebook/g) || []).length;
            if (fbCount >= 3) return false;
            // Structural exclusion (2026-09-19, same-DOM confirmed): a candidate
            // linking to the CURRENT group's own /members/ page is the group's
            // name/header card, never a post. Verified against two genuine posts
            // captured in the same DOM snapshot — neither ever carried this link;
            // both instead linked to /groups/<id>/user/<userid>/. Scoped to the
            // group actually being scanned so a post that happens to link to a
            // DIFFERENT group's /members/ page is not affected.
            //
            // Bounded ANCESTOR walk, not a descendant search (root-fixed
            // 2026-09-19): the forensic evidence showed the /members/ link
            // sits on an ANCESTOR of the smallest text-matching element, not
            // inside its own subtree — the minimal candidate element itself
            // had zero descendant links. Checking only `el`'s own descendants
            // (the original implementation) could therefore never find it for
            // exactly that minimal-element case. Walking upward and checking
            // each ancestor's subtree naturally covers both "link is on an
            // ancestor" (the confirmed case) and "link is on the element
            // itself"; it deliberately does NOT search unrelated descendants
            // of the original candidate beyond what each ancestor's own
            // subtree already includes, so a coincidental link nested deep
            // inside a genuine post's own content is not affected by this
            // walk direction change. Bounded to the same depth the forensic
            // investigation examined (8 ancestor levels) — never unbounded.
            if (groupId && el) {
                let node = el;
                let depth = 0;
                while (node && depth <= 8) {
                    if (node.querySelector &&
                        node.querySelector('a[href*="/groups/' + groupId + '/members"]')) {
                        return false;
                    }
                    node = node.parentElement;
                    depth += 1;
                }
            }
            return true;
        }
"""

# Readiness: boolean — is at least one real post candidate present right now.
_POST_READY_JS = (
    r"""(groupId) => {"""
    + _IS_REAL_POST_JS_FN
    + r"""
        const feedRoot = document.querySelector('[role="feed"]');
        const nodes = document.querySelectorAll('"""
    + _COMBINED_CANDIDATE_SELECTOR
    + r"""');
        for (const el of nodes) {
            if (isRealPostCandidate((el.innerText || '').trim(), el, groupId, feedRoot)) return true;
        }
        return false;
    }"""
)

# Extraction: full candidate list, deduplicated to the smallest (most
# specific) element per distinct text signature — the same text is often
# matched by several nested wrapper ancestors, and the innermost one is the
# actual post-text container rather than a large surrounding wrapper.
_POST_CANDIDATE_JS = (
    r"""(groupId) => {"""
    + _IS_REAL_POST_JS_FN
    + r"""
        const feedRoot = document.querySelector('[role="feed"]');
        const nodes = document.querySelectorAll('"""
    + _COMBINED_CANDIDATE_SELECTOR
    + r"""');
        let rawCount = 0;
        const byKey = {};
        nodes.forEach(el => {
            const text = (el.innerText || '').trim();
            if (text.length < 30 || text.length > 900) return;
            rawCount += 1;
            if (!isRealPostCandidate(text, el, groupId, feedRoot)) return;
            const key = text.slice(0, 80);
            const existing = byKey[key];
            if (!existing || text.length < existing.textLength) {
                const links = Array.from(el.querySelectorAll('a[href*="/groups/"]'));
                const postUrl = links.length ? links[0].href : '';
                const hasImg = !!el.querySelector('img[src]:not([src*="emoji"])');
                const hasComposer = !!el.querySelector('[contenteditable="true"]');
                byKey[key] = {
                    text: text.slice(0, 400),
                    postUrl: postUrl,
                    hasImg: hasImg,
                    hasComposer: hasComposer,
                    textLength: text.length,
                };
            }
        });
        return {raw_count: rawCount, posts: Object.values(byKey)};
    }"""
)

# Bounded incremental-scroll parameters (see _scan_one_group). Facebook
# virtualizes off-screen feed content — a post visible at scroll position N
# can be unmounted from the DOM by position N+M (confirmed live, 2026-09-19:
# a post present after the old 2x600px scroll had vanished from the DOM by
# the time a deeper scroll completed). A single "scroll to the end, extract
# once" pass therefore loses earlier content; extraction must happen at
# every step and accumulate.
_MAX_SCROLL_STEPS = 6
_STABILITY_STEPS = 2  # consecutive no-new-post steps before stopping early
_SCROLL_STEP_HEIGHT_MULT = 1.5  # x window.innerHeight per step


async def _wait_for_feed_readiness(
    page: Any, *, group_id: str = "", timeout_s: float = 8.0, poll_interval_s: float = 0.5
) -> bool:
    """Bounded poll for at least one genuine post candidate to render.

    Root fix for the empty-skeleton/comment-fragment race: domcontentloaded
    + a fixed sleep sometimes lands before Facebook has streamed in the real
    feed, or lands on a comment fragment that satisfies a naive text-length
    check before real post content exists. This polls the SAME classifier
    the extractor uses (_POST_READY_JS / _IS_REAL_POST_JS_FN), so a comment
    fragment or skeleton can never satisfy readiness on its own.

    group_id: the current group's Facebook identifier (from its URL), passed
    through to the classifier so the /members/ self-link exclusion is scoped
    to the group actually being scanned. Optional — pass "" if unknown.

    Returns True the instant a real post candidate appears (no wasted wait
    when content is already ready), False on a graceful timeout — never
    raises, and a False result is not an error: the caller proceeds to the
    existing extraction exactly as before, which correctly reports zero
    valid posts for a genuinely empty/quiet group.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            ready = await page.evaluate(_POST_READY_JS, group_id)
        except Exception:
            ready = False
        if ready:
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(poll_interval_s)


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
    ) -> dict:
        """Scan a list of approved group dicts for relevant discussions.

        Each group dict: {"group_url": str, "group_name": str, "id": str}

        Returns a result dict:
        {
            "discoveries":      list[dict],  # relevant posts found
            "groups_selected":  int,         # size of the input list (DB selection count)
            "groups_attempted": int,         # groups the scanner tried to visit (0 on auth failure)
            "groups_fetched":   int,         # groups that returned any content successfully
            "session_failed":   bool,        # True when Facebook session was not authenticated
        }

        Callers MUST check session_failed to distinguish authentication failure
        from a legitimate empty scan (zero relevant posts despite visiting groups).
        """
        groups_selected = len(approved_groups)

        if not approved_groups:
            return {
                "discoveries": [],
                "groups_selected": 0,
                "groups_attempted": 0,
                "groups_fetched": 0,
                "session_failed": False,
            }

        discoveries: list[dict] = []
        session_failed = False
        groups_attempted = 0
        groups_fetched = 0

        # Diagnostic telemetry aggregators (Phase 5H observability)
        total_dom_candidates = 0
        total_text_valid = 0
        total_scored = 0
        total_near_misses = 0
        total_zero_score = 0
        per_group_telemetry: list[dict] = []

        try:
            async with FacebookSession() as page:
                if not page:
                    log.warning(
                        "fb_browser: Facebook session not authenticated — "
                        "re-login required (run fb_browser_login.py once)"
                    )
                    session_failed = True
                else:
                    for group in approved_groups:
                        url = group.get("group_url", "")
                        name = group.get("group_name", "")
                        gid = str(group.get("id", ""))
                        if not url:
                            continue
                        groups_attempted += 1
                        try:
                            grp_result = await self._scan_one_group(
                                page, url, name, gid, posts_per_group
                            )
                            groups_fetched += 1
                            found = grp_result["discoveries"]
                            discoveries.extend(found)
                            tel = grp_result["telemetry"]
                            total_dom_candidates += tel.get("dom_candidates", 0)
                            total_text_valid += tel.get("text_valid", 0)
                            total_scored += tel.get("scored", 0)
                            total_near_misses += tel.get("near_misses", 0)
                            total_zero_score += tel.get("zero_score", 0)
                            per_group_telemetry.append({
                                "group_id": gid,
                                "group_name": name,
                                "group_url": url,
                                "dom_candidates": tel.get("dom_candidates", 0),
                                "text_valid": tel.get("text_valid", 0),
                                "scored": tel.get("scored", 0),
                                "discoveries": len(found),
                                "near_misses": tel.get("near_misses", 0),
                                "zero_score": tel.get("zero_score", 0),
                            })
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
        log.info(
            "fb_browser: scan_groups — selected=%d attempted=%d fetched=%d discoveries=%d "
            "dom=%d valid=%d scored=%d near=%d zero=%d session_failed=%s",
            groups_selected, groups_attempted, groups_fetched, len(discoveries),
            total_dom_candidates, total_text_valid, total_scored,
            total_near_misses, total_zero_score, session_failed,
        )
        return {
            # Existing keys (unchanged — backward compatible)
            "discoveries": discoveries,
            "groups_selected": groups_selected,
            "groups_attempted": groups_attempted,
            "groups_fetched": groups_fetched,
            "session_failed": session_failed,
            # Phase 5H diagnostic telemetry (new — callers that ignore extra keys are unaffected)
            "telemetry": {
                "dom_candidates": total_dom_candidates,
                "text_valid": total_text_valid,
                "scored": total_scored,
                "discoveries": len(discoveries),
                "near_misses": total_near_misses,
                "zero_score": total_zero_score,
            },
            "per_group_telemetry": per_group_telemetry,
        }

    async def _scan_one_group(
        self, page: Any, group_url: str, group_name: str, group_id: str,
        max_posts: int,
    ) -> dict:
        """Navigate to a group and extract recent post text + URLs.

        Returns:
            {
                "discoveries": list[dict],  # posts scoring >= 0.2 (unchanged behavior)
                "telemetry": {
                    "dom_candidates": int,  # total containers found by DOM selector
                    "text_valid": int,      # containers passing ≥30-char text filter
                    "scored": int,          # posts passed through _relevance_score()
                    "discoveries": int,     # count of score >= 0.2
                    "near_misses": int,     # count of 0 < score < 0.2 (structurally 0 with
                                            #   current formula: min(hits/3,1) jumps 0→0.333)
                    "zero_score": int,      # count of score == 0.0
                },
            }
        """
        await page.goto(group_url, wait_until="domcontentloaded", timeout=30_000)
        await _random_delay(2.0, 4.0)

        # Facebook's own group identifier from the URL (NOT the `group_id`
        # parameter above, which is our internal DB id) — used only to scope
        # the /members/ self-link structural exclusion to the group actually
        # being scanned, never to a different group a post might reference.
        fb_group_id = _extract_fb_group_id(group_url)

        # Incremental scroll + extract loop (root fix, 2026-09-19 — see
        # _IS_REAL_POST_JS_FN / _MAX_SCROLL_STEPS docstrings above).
        # Facebook virtualizes off-screen feed content: a post visible after
        # scroll step N can be unmounted from the DOM by step N+M, so a
        # single "scroll to the end, extract once" pass loses earlier posts.
        # Extraction now happens at every step and accumulates into
        # `collected`, keyed by post URL when available (stable identity)
        # or a normalised text prefix otherwise — so the same post seen
        # across multiple scroll positions is recorded once, and a post
        # already collected survives later unmounting. Bounded: at most
        # _MAX_SCROLL_STEPS scroll iterations, stopping early after
        # _STABILITY_STEPS consecutive steps with no new post found.
        collected: dict[str, dict] = {}
        # Candidates with no reliable post-specific URL are merged by
        # normalized substring relationship instead of an exact key (root
        # fix, 2026-09-19): nested DOM wrappers around the SAME post produce
        # several text captures that are substrings/superstrings of each
        # other (e.g. "X" vs "X +972598444670" vs the full listing with a
        # business-card suffix) — confirmed live: one genuine Arabic parts
        # post produced 4 separate discoveries this way, none a false
        # positive individually, but inflating the count for one real post.
        text_fallback_entries: list[dict] = []
        raw_count_total = 0
        stable_rounds = 0

        for step in range(_MAX_SCROLL_STEPS):
            await _wait_for_feed_readiness(page, group_id=fb_group_id, timeout_s=5.0)
            try:
                result = await page.evaluate(_POST_CANDIDATE_JS, fb_group_id)
            except Exception:
                result = None
            if not isinstance(result, dict):
                result = {"raw_count": 0, "posts": []}

            raw_count_total += int(result.get("raw_count", 0))
            new_this_step = 0
            for post in result.get("posts", []) or []:
                text = (post.get("text") or "").strip()
                if not text:
                    continue
                post_url = (post.get("postUrl") or "").split("?")[0]
                # A post_url is only a reliable identity if it points somewhere
                # more specific than the bare group URL itself — that bare
                # value is exactly the fallback used later when no permalink
                # was found in the candidate's own subtree, so it identifies
                # nothing distinct.
                reliable_url = (
                    post_url if post_url and post_url.rstrip("/") != group_url.rstrip("/") else ""
                )
                entry = {
                    "text": text,
                    "postUrl": post_url,
                    "hasImg": bool(post.get("hasImg")),
                    "hasComposer": bool(post.get("hasComposer")),
                }

                if reliable_url:
                    if reliable_url not in collected:
                        collected[reliable_url] = entry
                        new_this_step += 1
                    continue

                norm = _normalize_for_merge(text)
                merged = False
                for existing in text_fallback_entries:
                    existing_norm = _normalize_for_merge(existing["text"])
                    if norm == existing_norm or norm in existing_norm:
                        merged = True  # already covered by an equal-or-more-complete capture
                        break
                    if existing_norm in norm:
                        existing["text"] = text  # this capture is more complete — keep it
                        existing["postUrl"] = post_url
                        existing["hasImg"] = existing["hasImg"] or entry["hasImg"]
                        existing["hasComposer"] = existing["hasComposer"] or entry["hasComposer"]
                        merged = True
                        break
                if not merged:
                    text_fallback_entries.append(entry)
                    new_this_step += 1

            if new_this_step == 0:
                stable_rounds += 1
                if stable_rounds >= _STABILITY_STEPS:
                    break
            else:
                stable_rounds = 0

            if step < _MAX_SCROLL_STEPS - 1:
                await page.evaluate(
                    f"window.scrollBy(0, window.innerHeight * {_SCROLL_STEP_HEIGHT_MULT})"
                )
                await _random_delay(1.5, 2.5)

        posts_data = list(collected.values()) + text_fallback_entries
        dom_candidates = raw_count_total
        text_valid = len(posts_data)

        # Score each post, tracking diagnostic counts without changing thresholds
        discoveries: list[dict] = []
        near_misses = 0
        zero_score = 0
        scored = 0

        for post in posts_data[:max_posts]:
            text = (post.get("text") or "").strip()
            post_url = (post.get("postUrl") or group_url).split("?")[0]
            score = _relevance_score(text)
            scored += 1
            if score >= 0.2:
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
            elif score > 0:
                near_misses += 1
            else:
                zero_score += 1

        telemetry = {
            "dom_candidates": dom_candidates,
            "text_valid": text_valid,
            "scored": scored,
            "discoveries": len(discoveries),
            "near_misses": near_misses,
            "zero_score": zero_score,
        }
        log.info(
            "fb_browser: _scan_one_group '%s' → dom=%d valid=%d scored=%d disc=%d near=%d zero=%d",
            group_name, dom_candidates, text_valid, scored,
            len(discoveries), near_misses, zero_score,
        )
        return {"discoveries": discoveries, "telemetry": telemetry}

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
