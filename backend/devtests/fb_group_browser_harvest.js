/**
 * Facebook Group Browser Harvest
 *
 * Run from the browser DevTools console while on:
 *   https://www.facebook.com/groups/joins/
 *
 * The script mirrors discover_account_groups() scroll + extraction logic so the
 * owner's browser (which is authenticated) can perform discovery when the server
 * IP is blocked by Facebook bot-detection.
 *
 * After extraction, the group list is POSTed to /api/v1/system/ingest-group-list
 * using the text/plain CORS simple-request pattern (no preflight needed).
 *
 * Usage:
 *   1. Open https://www.facebook.com/groups/joins/ while logged in as the account owner.
 *   2. Open DevTools → Console.
 *   3. Paste this entire script and press Enter.
 *   4. When prompted, enter the COLLECT_SECRET value (from server .env).
 *   5. Watch the console for progress and the final server reply.
 */
(async () => {
    'use strict';

    const SERVER   = "https://autosparefinder.co.il";
    const ENDPOINT = `${SERVER}/api/v1/system/ingest-group-list`;
    const MAX_SCROLLS = 40;
    const SCROLL_PAUSE_MS = 2200;

    if (!location.href.includes("facebook.com/groups")) {
        console.error("[fb_harvest] Navigate to https://www.facebook.com/groups/joins/ first.");
        return;
    }

    const SECRET = prompt("Enter COLLECT_SECRET:");
    if (!SECRET) {
        console.warn("[fb_harvest] Cancelled — no secret provided.");
        return;
    }

    // ── 1. Scroll until count stabilises ─────────────────────────────────────
    console.log("[fb_harvest] Scrolling to load all joined groups...");
    let prevCount = 0, stableRounds = 0;

    for (let i = 0; i < MAX_SCROLLS; i++) {
        window.scrollBy(0, window.innerHeight * 1.5);
        await new Promise(r => setTimeout(r, SCROLL_PAUSE_MS));
        const cur = document.querySelectorAll('a[href*="/groups/"]').length;
        if (cur === prevCount) {
            if (++stableRounds >= 3) {
                console.log(`[fb_harvest] Count stable at ${cur} — stopping scroll.`);
                break;
            }
        } else {
            stableRounds = 0;
        }
        prevCount = cur;
        console.log(`[fb_harvest] Scroll ${i + 1}/${MAX_SCROLLS}: ${cur} group links visible`);
    }

    // ── 2. Extract group names and canonical URLs ─────────────────────────────
    const META_RE = /\/groups\/(joins|discover|feed|create|search|requests|pending|notifications|see_all_groups)/;
    const results = [];
    const seen = new Set();

    for (const a of document.querySelectorAll('a[href*="/groups/"]')) {
        let href = (a.href || '').split('?')[0].replace(/\/$/, '');

        // Must match /groups/<slug_or_id> — at least 3 chars after /groups/
        if (!/\/groups\/[^/]{3,}$/.test(href)) continue;
        // Exclude meta/navigation pages
        if (META_RE.test(href)) continue;
        if (seen.has(href)) continue;
        seen.add(href);

        // Extract group name: walk up the DOM to find a card heading
        let name = '';
        let el = a.parentElement;
        for (let i = 0; i < 6 && el; i++) {
            const h = el.querySelector('[dir="auto"] > span, [role="heading"]');
            if (h && h.innerText && h.innerText.length > 1) {
                name = h.innerText.trim();
                break;
            }
            el = el.parentElement;
        }
        if (!name) {
            name = (a.getAttribute('aria-label') || a.innerText || '').trim();
        }
        name = name.replace(/\s+/g, ' ').slice(0, 120);
        if (name.length < 3) continue;
        if (/^(view group|see all|more|create new group)$/i.test(name)) continue;

        results.push({ name, url: href + '/' });
    }

    console.log(`[fb_harvest] Extracted ${results.length} unique groups`);
    if (results.length === 0) {
        console.warn("[fb_harvest] No groups found. Are you on the right page?");
        return { ok: false, error: "no groups extracted" };
    }

    // ── 3. POST to server ─────────────────────────────────────────────────────
    const body = JSON.stringify({ secret: SECRET, groups: results });
    console.log(`[fb_harvest] Sending ${results.length} groups to ${ENDPOINT}...`);

    let reply;
    try {
        const resp = await fetch(ENDPOINT, {
            method: "POST",
            body,
            credentials: "omit",
            // text/plain = CORS simple request (no preflight) — same pattern as /collect
            headers: { "Content-Type": "text/plain" },
        });
        reply = await resp.json();
    } catch (err) {
        console.error("[fb_harvest] Network error:", err);
        return { ok: false, error: String(err) };
    }

    console.log("[fb_harvest] Server reply:", reply);
    if (reply.ok) {
        console.log(
            `[fb_harvest] ✅ Done.\n` +
            `  submitted : ${reply.total_submitted}\n` +
            `  accepted  : ${reply.accepted}  (new rows in group_targets)\n` +
            `  already known : ${reply.already_known}\n` +
            `  duplicates    : ${reply.duplicates}\n` +
            `  invalid       : ${reply.rejected_invalid}`
        );
    } else {
        console.error("[fb_harvest] ❌ Server error:", reply.error);
    }
    return reply;
})();
