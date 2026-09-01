/**
 * AutoSpareFinder — Facebook Group Browser Scanner
 *
 * Self-running userscript / console paste. Persists scan state in localStorage
 * across Facebook page navigations. Navigates each group, extracts visible posts,
 * and POSTs them to the backend ingest endpoint (owner must supply the secret
 * at runtime — never hardcode it here).
 *
 * Usage (Tampermonkey header or browser console on any facebook.com tab):
 *   1. Set INGEST_SECRET below to the value of COLLECT_SECRET from the server .env
 *      at runtime — do NOT commit the real secret here.
 *   2. Paste into the browser console and press Enter.
 *   3. The script navigates each group automatically.
 *   4. Owner receives WhatsApp summary when done.
 *
 * State key: 'noa_group_scan' in localStorage. To abort: localStorage.removeItem('noa_group_scan')
 *
 * SECURITY NOTE: Fetch the group list and secret from the backend at runtime
 * rather than hardcoding them here. The endpoint /api/v1/system/ingest-group-posts
 * requires the COLLECT_SECRET — never commit that value to source control.
 */
(async function NOA_GROUP_SCANNER() {
  // ── Configuration — fill at runtime, never commit the real secret ─────────────
  const INGEST_URL    = 'https://autosparefinder.co.il/api/v1/system/ingest-group-posts';
  const INGEST_SECRET = typeof window._NOA_SECRET !== 'undefined'
    ? window._NOA_SECRET          // set window._NOA_SECRET = '...' before pasting
    : prompt('Enter COLLECT_SECRET (will not be stored):') || '';

  if (!INGEST_SECRET) {
    console.error('[NOA scanner] No secret provided — aborting.');
    return;
  }

  const STATE_KEY = 'noa_group_scan';

  // ── Group list — fetched fresh from backend at runtime ────────────────────────
  // Populated from group_targets table. Fetch with:
  //   fetch('https://autosparefinder.co.il/api/v1/system/group-list?secret=<SECRET>')
  // For now the owner pastes the list from the WhatsApp console (fb-groups command).
  // Format: [{id: "<real-uuid>", url: "https://...", name: "..."}]
  const ALL_GROUPS = window._NOA_GROUPS || [];

  if (ALL_GROUPS.length === 0) {
    console.error('[NOA scanner] No groups loaded. Set window._NOA_GROUPS = [...] before running.');
    console.info('Run in the console first:\n  window._NOA_GROUPS = await fetch(...).then(r=>r.json())');
    return;
  }

  // ── Input validation ──────────────────────────────────────────────────────────
  const FB_GROUP_RE = /^https:\/\/(www\.)?facebook\.com\/groups\/[a-zA-Z0-9._-]+\/?$/;
  const validGroups = ALL_GROUPS.filter(g =>
    g.id && g.url && FB_GROUP_RE.test(g.url) && g.name
  );
  if (validGroups.length !== ALL_GROUPS.length) {
    console.warn(`[NOA scanner] ${ALL_GROUPS.length - validGroups.length} groups dropped — invalid URL or missing fields`);
  }

  // ── State management ──────────────────────────────────────────────────────────
  function loadState() {
    try { return JSON.parse(localStorage.getItem(STATE_KEY) || 'null'); } catch { return null; }
  }
  function saveState(s) {
    try { localStorage.setItem(STATE_KEY, JSON.stringify(s)); } catch(e) {
      console.warn('[NOA scanner] localStorage write failed:', e.message);
    }
  }

  // ── Post extraction from rendered DOM ─────────────────────────────────────────
  function extractPosts(groupUrl) {
    const articles = document.querySelectorAll('[role="article"]');
    const seen = new Set();
    const posts = [];
    articles.forEach(art => {
      const spans = art.querySelectorAll('span[dir="auto"]');
      let text = '';
      spans.forEach(s => {
        const t = (s.innerText || '').replace(/\s+/g, ' ').trim();
        // Strip invisible unicode obfuscation characters Facebook injects
        const clean = t.replace(/[​-‏‪-‮﻿⁠-⁤]/g, '');
        if (clean && clean.length > 15 && !text.includes(clean)) text += clean + ' ';
      });
      text = text.trim().substring(0, 400);
      if (text.length < 15 || seen.has(text)) return;
      seen.add(text);

      // Only use same-origin post links (avoids tracking URLs)
      const postLinks = [...art.querySelectorAll('a[href*="/posts/"],a[href*="/permalink/"]')]
        .filter(a => { try { return new URL(a.href).hostname.includes('facebook.com'); } catch { return false; } });
      const postUrl = postLinks[0]?.href || groupUrl;
      posts.push({ text, post_url: postUrl.substring(0, 300) });
    });
    return posts;
  }

  // ── Send posts to backend ─────────────────────────────────────────────────────
  async function sendPosts(group, posts) {
    const payload = JSON.stringify({
      secret:     INGEST_SECRET,
      group_id:   group.id,
      group_name: group.name.substring(0, 255),
      group_url:  group.url,
      posts:      posts.slice(0, 15),   // server cap is 15; honour it client-side too
    });
    try {
      const resp = await fetch(INGEST_URL, {
        method:      'POST',
        headers:     { 'Content-Type': 'text/plain' },
        body:        payload,
        credentials: 'omit',            // never send Facebook cookies to our server
        mode:        'cors',
      });
      if (!resp.ok) {
        console.warn(`[NOA scanner] server error ${resp.status} for ${group.name}`);
        return { ok: false };
      }
      const result = await resp.json();
      console.log(`[NOA scanner] ${group.name}: drafted=${result.drafted} skipped=${result.skipped}`);
      return result;
    } catch (e) {
      console.warn('[NOA scanner] POST failed (CSP or network):', e.message);
      return { ok: false };
    }
  }

  // ── Scroll to trigger lazy-load ───────────────────────────────────────────────
  async function scrollAndWait() {
    window.scrollTo(0, 0);
    await new Promise(r => setTimeout(r, 1000));
    for (let i = 0; i < 5; i++) {
      window.scrollBy(0, 600);
      await new Promise(r => setTimeout(r, 800));
    }
    await new Promise(r => setTimeout(r, 1500));
  }

  // ── Main loop ─────────────────────────────────────────────────────────────────
  let state = loadState();
  const currentPath = window.location.pathname;
  const groupSlug = currentPath.match(/\/groups\/([^/?#]+)/)?.[1];

  if (state?.running && groupSlug) {
    // Resumed after navigation — process this group
    const currentGroup = validGroups.find(g =>
      g.url.includes('/' + groupSlug + '/') || g.url.includes('/' + groupSlug + '?')
    );

    if (currentGroup && state.pending?.length > 0) {
      console.log(`[NOA scanner] Scanning: ${currentGroup.name} (${state.pending.length - 1} left after this)`);

      await new Promise(r => setTimeout(r, 5000));   // wait for React render
      await scrollAndWait();

      const posts = extractPosts(currentGroup.url);
      console.log(`[NOA scanner] Extracted ${posts.length} unique posts`);

      if (posts.length > 0) {
        await sendPosts(currentGroup, posts);
      }

      state.done = [...(state.done || []), currentGroup.url];
      state.pending = state.pending.filter(u => u !== currentGroup.url);
      saveState(state);

      if (state.pending.length > 0) {
        await new Promise(r => setTimeout(r, 1000));
        window.location.href = state.pending[0];
      } else {
        console.log('[NOA scanner] ✅ ALL GROUPS SCANNED — owner will receive WhatsApp summary.');
        localStorage.removeItem(STATE_KEY);
        window.location.href = 'https://www.facebook.com/groups/joins/';
      }
    }
  } else {
    // Fresh start
    const pending = validGroups.map(g => g.url);
    saveState({ running: true, pending, done: [], started: Date.now() });
    console.log(`[NOA scanner] Starting scan of ${pending.length} groups…`);
    await new Promise(r => setTimeout(r, 500));
    window.location.href = pending[0];
  }
})();
